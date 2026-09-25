"""In-process job runner (D5): a dict + one worker thread per job.

Long work (a live week is minutes of LLM calls) is submitted with
submit(kind, fn); fn(log) runs on a daemon thread and calls log(line) for
each progress line it produces. The record keeps a capped log_tail for the
UI to poll at ~1 s via GET /api/jobs/{id}. Jobs are ephemeral on purpose —
canon lives on disk only (guardrail 2).
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

# job progress lines are mirrored to the studio log (server.py attaches the
# rotating file handler); uvicorn's access log stays console-only
_log = logging.getLogger("studio.jobs")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _public(job: dict) -> dict:
    """The API shape — internal fields (full log) are not exposed."""
    return {
        "id": job["id"],
        "kind": job["kind"],
        "status": job["status"],
        "log_tail": list(job["log_tail"]),
        "result": job["result"],
        "error": job["error"],
        "started": job["started"],
    }


def submit(kind: str, fn) -> str:
    """Run fn(log) on a worker thread; return the job id. fn may return a
    JSON-serializable result; any exception flips the job to status=error."""

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "log_tail": [],
        "result": None,
        "error": None,
        "started": _now(),
        # the DURING-the-run log is a ring: a 30-minute week logs every
        # engine line, and the old append-everything list pinned the whole
        # transcript in RAM until the finally-trim (the API only ever
        # served the 40-line tail, so nothing observable changes)
        "_log_all": collections.deque(maxlen=LOG_CAP),
    }
    with _LOCK:
        _JOBS[job_id] = job

    def log(line):
        line = str(line).rstrip("\n")
        with _LOCK:
            job["_log_all"].append(line)
            job["log_tail"] = list(job["_log_all"])
        _log.info("[job %s] %s", job_id, line)

    def run():
        with _LOCK:
            job["status"] = "running"
        try:
            result = fn(log)
            with _LOCK:
                job["result"] = result
                job["status"] = "done"
            _log.info("job %s (%s) done", job_id, job["kind"])
        except Exception as e:  # noqa: BLE001 — any failure is a job error
            with _LOCK:
                job["error"] = f"{type(e).__name__}: {e}"
                job["status"] = "error"
            _log.error("job %s (%s) failed: %s", job_id, job["kind"], e)
        finally:
            # GC: the ring is dead weight once the job is over (the tail
            # stays), and the dict itself grows without bound on a
            # long-lived server — keep only the newest JOB_CAP records.
            with _LOCK:
                job["_log_all"] = None
                if len(_JOBS) > JOB_CAP:
                    finished = [j for j in _JOBS.values()
                                if j["status"] in ("done", "error")]
                    finished.sort(key=lambda j: j["started"])
                    for old in finished[:len(_JOBS) - JOB_CAP]:
                        _JOBS.pop(old["id"], None)

    threading.Thread(target=run, name=f"job-{job_id}", daemon=True).start()
    return job_id


def get(job_id: str):
    """The public record for a job, or None if the id is unknown."""
    with _LOCK:
        job = _JOBS.get(job_id)
        return _public(job) if job else None


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
