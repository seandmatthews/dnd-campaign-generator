"""In-process job runner (D5) + the per-campaign FIFO queue (ticket 02).

Long work (a live week is minutes of LLM calls) is submitted with
submit(kind, fn); fn(log) runs on a daemon thread and calls log(line) for
each progress line it produces. The record keeps a capped log_tail for the
UI to poll at ~1 s via GET /api/jobs/{id}. Jobs are ephemeral on purpose —
canon lives on disk only (guardrail 2).

The queue (ticket 02): campaign-MUTATING jobs go through
submit_queued(campaign, kind, fn) — a FIFO per campaign drained by ONE
worker thread, so a submission that arrives while another job runs waits
its turn (202 + queue position) instead of being refused with a 409. The
per-campaign lock stays exactly as it was — the worker acquires it before
each job (the caller's wrapped fn owns that acquire, so the lock
discipline lives in routers/common) — the queue only decides WHO goes
next. Queued jobs are cancellable: cancel(id) dequeues and flips the
record to status "cancelled"; a running or finished job cannot be
cancelled.
"""

import collections
import datetime
import logging
import threading
import time
import uuid

_LOCK = threading.Lock()
_JOBS: dict = {}
LOG_CAP = 40    # log_tail keeps roughly the last 40 lines
JOB_CAP = 200   # finished jobs older than the newest JOB_CAP are dropped
FINAL_STATES = ("done", "error", "cancelled")

# job progress lines are mirrored to the studio log (server.py attaches the
# rotating file handler); uvicorn's access log stays console-only
_log = logging.getLogger("studio.jobs")

_QUEUES_GUARD = threading.Lock()
_QUEUES: dict = {}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _new_job(kind: str, campaign: str | None = None) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "campaign": campaign,
        "status": "queued",
        "log_tail": [],
        "result": None,
        "error": None,
        "started": _now(),
        # ring buffer: a 30-minute week logs every engine line; the old
        # append-everything list pinned the whole transcript in RAM
        # (the API only ever served the tail).
        "_log_all": collections.deque(maxlen=LOG_CAP),
        # the campaign queue this job sits in, if any (cancel/position)
        "_queue": None,
    }


def _public(job: dict) -> dict:
    """The API shape — internal fields (full log, queue ref) are not
    exposed."""
    out = {
        "id": job["id"],
        "kind": job["kind"],
        "campaign": job.get("campaign"),
        "status": job["status"],
        "log_tail": list(job["log_tail"]),
        "result": job["result"],
        "error": job["error"],
        "started": job["started"],
    }
    if job["status"] == "queued" and job.get("_queue") is not None:
        out["position"] = job["_queue"].position_of(job)
    return out


def _logfn(job: dict):
    def log(line):
        line = str(line).rstrip("\n")
        with _LOCK:
            if job["_log_all"] is not None:
                job["_log_all"].append(line)
                job["log_tail"] = list(job["_log_all"])
        _log.info("[job %s] %s", job["id"], line)
    return log


def _execute(job: dict, fn):
    """The run body shared by the immediate and the queued path: status
    flips, the fn call, result/error recording, and the finished-job GC."""
    with _LOCK:
        job["status"] = "running"
    try:
        result = fn(_logfn(job))
        with _LOCK:
            job["result"] = result
            job["status"] = "done"
        _log.info("job %s (%s) done", job["id"], job["kind"])
    except Exception as e:  # noqa: BLE001 — any failure is a job error
        with _LOCK:
            job["error"] = f"{type(e).__name__}: {e}"
            job["status"] = "error"
        _log.error("job %s (%s) failed: %s", job["id"], job["kind"], e)
    finally:
        # GC: the ring is dead weight once the job is over (the tail
        # stays), and the dict itself grows without bound on a
        # long-lived server — keep only the newest JOB_CAP records.
        with _LOCK:
            job["_log_all"] = None
            if len(_JOBS) > JOB_CAP:
                finished = [j for j in _JOBS.values()
                            if j["status"] in FINAL_STATES]
                finished.sort(key=lambda j: j["started"])
                for old in finished[:len(_JOBS) - JOB_CAP]:
                    _JOBS.pop(old["id"], None)


def submit(kind: str, fn, campaign: str | None = None) -> str:
    """Run fn(log) on a worker thread; return the job id. fn may return a
    JSON-serializable result; any exception flips the job to status=error.
    No queue: the job starts immediately (the direct path used by tests
    and non-campaign work — campaign mutations go through submit_queued)."""

    job = _new_job(kind, campaign)
    with _LOCK:
        _JOBS[job["id"]] = job

    def run():
        _execute(job, fn)

    threading.Thread(target=run, name=f"job-{job['id']}", daemon=True).start()
    return job["id"]


class _CampaignQueue:
    """One FIFO per campaign, drained by ONE worker thread: submissions
    append and get their position (how many jobs must finish first — the
    one running counts), and the drainer runs them strictly in order.

    The drainer waits for the job's turn via the entry's before_run hook
    (the campaign lock, passed in by the router) while the record is STILL
    "queued" — it flips to "running" only once the turn is taken and the
    fn starts — so the jobs poll shows queued+position for exactly the
    jobs that have not run yet. A cancel during that wait is honored
    between acquire attempts: the job is marked cancelled and never runs.
    before_run returns the release callable; the drainer calls it when the
    job (or its cancellation) is done, so the lock is held exactly as long
    as the old submit-time acquire held it."""

    def __init__(self, name: str):
        self.name = name
        self.mu = threading.Lock()
        self.pending: collections.deque = collections.deque()
        self.current: dict | None = None
        self.thread: threading.Thread | None = None

    def submit(self, job: dict, fn, before_run=None) -> int:
        with self.mu:
            position = len(self.pending) + (1 if self.current is not None else 0)
            job["_queue"] = self
            self.pending.append(
                {"job": job, "fn": fn, "before_run": before_run,
                 "cancelled": False})
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(
                    target=self._drain, name=f"job-queue-{self.name}",
                    daemon=True)
                self.thread.start()
        return position

    def position_of(self, job: dict) -> int | None:
        with self.mu:
            for i, entry in enumerate(self.pending):
                if entry["job"] is job:
                    # jobs ahead in the deque + the one running now (it
                    # left the deque when it started, but it is still ahead)
                    return i + (1 if self.current is not None else 0)
        return None

    def cancel(self, job: dict) -> bool:
        """Flag a still-pending job for cancellation and dequeue it unless
        it is the head — the drainer may be mid wait-for-lock on the head
        and owns its finalization. Only QUEUE state is touched here (under
        the queue's own lock); the job-record flip is jobs.cancel's, under
        the global _LOCK — taking _LOCK inside q.mu would invert the one
        lock order the module keeps (_LOCK -> q.mu via position_of inside
        _public, never the reverse). False when the job already started
        or was already cancelled (there is nothing left to dequeue)."""
        with self.mu:
            for i, entry in enumerate(self.pending):
                if entry["job"] is job:
                    if entry["cancelled"]:
                        return False  # already cancelled by an earlier call
                    entry["cancelled"] = True
                    if i > 0:
                        del self.pending[i]
                    return True
        return False

    def _drain(self):
        while True:
            with self.mu:
                if not self.pending:
                    self.thread = None
                    return
                entry = self.pending[0]  # peek: the head waits its turn
                job, fn = entry["job"], entry["fn"]
            release = None
            try:
                release = entry["before_run"](log=_logfn(job), entry=entry) \
                    if entry.get("before_run") else None
            except _Cancelled:
                with self.mu:
                    self.pending.popleft()
                with _LOCK:
                    if job["status"] == "queued":
                        job["status"] = "cancelled"
                        job["_log_all"] = None
                continue
            except Exception as e:  # noqa: BLE001 — it never started
                with self.mu:
                    self.pending.popleft()
                with _LOCK:
                    job["error"] = f"{type(e).__name__}: {e}"
                    job["status"] = "error"
                    job["_log_all"] = None
                _log.error("job %s (%s) never started: %s",
                           job["id"], job["kind"], e)
                continue
            with self.mu:
                self.pending.popleft()
                if entry["cancelled"]:
                    # cancelled while the turn was being taken: the record
                    # is already flipped — give the turn back untouched
                    if release is not None:
                        release()
                    continue
                self.current = job
                job["status"] = "running"  # atomic with the pop
            try:
                _execute(job, fn)
            finally:
                if release is not None:
                    release()
                with self.mu:
                    self.current = None


class _Cancelled(Exception):
    """Internal: the before_run hook saw the entry's cancel flag — the
    job never starts."""


def take_turn(log, entry, lock, heartbeat_s: float = 5.0,
              max_wait_s: float | None = None) -> None:
    """The queue's wait-for-turn loop, used by before_run hooks: acquire()
    until it succeeds or the entry is cancelled (checked every attempt).
    A heartbeat log line every ~30 s keeps a long queue visible in the job
    log (six short acquire attempts per line — cancel checks stay 5 s
    apart). Returns with the lock HELD; max_wait_s is the dead-man cap for
    a lock that never frees (a wedged external process)."""
    waited = 0.0
    since_heartbeat = 0
    while not lock.acquire(True, heartbeat_s):
        if entry["cancelled"]:
            raise _Cancelled()
        waited += heartbeat_s
        since_heartbeat += 1
        if since_heartbeat >= 6:
            since_heartbeat = 0
            log(f"still waiting for the campaign lock ({waited / 60:.0f} "
                "minutes so far) — the job runs when the current one "
                "finishes")
        if max_wait_s is not None and waited >= max_wait_s:
            raise RuntimeError(
                f"the campaign lock stayed busy for over "
                f"{max_wait_s / 3600:.0f} hours — giving up without "
                "running anything (is another process holding the "
                "campaign?)")
    if entry["cancelled"]:
        lock.release()
        raise _Cancelled()


def _queue(campaign: str) -> _CampaignQueue:
    with _QUEUES_GUARD:
        return _QUEUES.setdefault(campaign, _CampaignQueue(campaign))


def submit_queued(campaign: str, kind: str, fn, before_run=None) -> tuple[str, int]:
    """Enqueue a campaign mutation: FIFO per campaign, drained in order by
    one worker. before_run(log=, entry=) takes the job's turn (the
    campaign lock) while the record is still queued and returns the
    release callable; the drainer calls it when the job ends. Returns
    (job_id, position) — position = how many jobs must finish before this
    one starts (0 = it is next onto an idle campaign)."""
    job = _new_job(kind, campaign)
    with _LOCK:
        _JOBS[job["id"]] = job
    position = _queue(campaign).submit(job, fn, before_run)
    _log.info("job %s (%s) queued for %s at position %d",
              job["id"], kind, campaign, position)
    return job["id"], position


def cancel(job_id: str) -> str:
    """Cancel a queued job (dequeue + status cancelled). Returns
    "cancelled", "not_queued" (already running/finished — or a job that
    never sat in a queue), or "unknown" for a dead id. The record flip
    happens under _LOCK like every other job-record mutation (the queue's
    cancel only touches queue state — see _CampaignQueue.cancel)."""
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return "unknown"
    q = job.get("_queue")
    if q is not None and q.cancel(job):
        with _LOCK:
            job["status"] = "cancelled"
            job["_log_all"] = None
        _log.info("job %s (%s) cancelled while queued", job_id, job["kind"])
        return "cancelled"
    return "not_queued"


def cancel_queued() -> int:
    """Cancel every still-queued job, any campaign — the teardown/
    shutdown primitive: a queued job must not start against a torn-down
    environment (tests), or outlive the server's purpose (not used there
    yet). Returns the count. Running jobs are untouched."""
    with _LOCK:
        queued = [j for j in _JOBS.values() if j["status"] == "queued"
                  and j.get("_queue") is not None]
    n = 0
    for job in queued:
        if cancel(job["id"]) == "cancelled":
            n += 1
    return n


def get(job_id: str):
    """The public record for a job, or None if the id is unknown."""
    with _LOCK:
        job = _JOBS.get(job_id)
        return _public(job) if job else None


def running_jobs(campaign: str | None = None,
                 kinds: tuple | None = None) -> list:
    """Public records of currently RUNNING jobs — what a 409 was blocked
    BY. Filter by campaign and/or kind tuple; order is registration order."""
    with _LOCK:
        jobs = [j for j in _JOBS.values()
                if j["status"] == "running"
                and (campaign is None or j.get("campaign") == campaign)
                and (kinds is None or j["kind"] in kinds)]
        return [_public(j) for j in jobs]


def queue_snapshot(campaign: str) -> dict:
    """The campaign's queue for the UI strip: the running job (if any) and
    the still-pending ones with their positions. Queue state is read under
    the queue's own lock into plain refs, then published under _LOCK — the
    two locks are never held in the opposite order anywhere."""
    q = _QUEUES.get(campaign)
    current, pending = None, []
    if q is not None:
        with q.mu:
            current = q.current
            pending = [entry["job"] for entry in q.pending]
    out = {"campaign": campaign, "running": None, "queued": []}
    with _LOCK:
        if current is not None:
            out["running"] = _public(current)
        for job in pending:
            out["queued"].append(_public(job))
    return out


def last_finished(campaign: str, kinds: tuple | None = None) -> dict:
    """The newest finished job (done/error/cancelled) per kind, one
    campaign: {kind: public record}. Where the queue snapshot answers
    "what is running", this answers "how did the last one go" — the Maps
    tab renders a failed map job as a persistent section error that
    survives a reload and clears when a later run of that kind finishes
    (ticket 41). Same in-memory lifetime as every job record."""
    with _LOCK:
        newest: dict = {}
        for job in _JOBS.values():
            if job.get("campaign") != campaign \
                    or job["status"] not in FINAL_STATES:
                continue
            if kinds is not None and job["kind"] not in kinds:
                continue
            cur = newest.get(job["kind"])
            # insertion order breaks `started` ties (later registration wins)
            if cur is None or job["started"] >= cur["started"]:
                newest[job["kind"]] = job
        return {kind: _public(job) for kind, job in newest.items()}


def wait_all(timeout: float = 30.0) -> bool:
    """Block until no submitted job is queued or running. Test quiescence:
    force-releasing a lock a live job thread still holds breaks mutual
    exclusion mid-suite (its finally then releases an unlocked lock), so
    teardown must wait for the workers to finish BEFORE touching locks.
    True when quiet, False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _LOCK:
            busy = any(j["status"] in ("queued", "running")
                       for j in _JOBS.values())
        if not busy:
            return True
        time.sleep(0.02)
    return False
