"""In-process job runner (D5): a dict + one worker thread per job.

Long work (a live week is minutes of LLM calls) is submitted with
submit(kind, fn); fn(log) runs on a daemon thread and calls log(line) for
each progress line it produces. The record keeps a capped log_tail for the
UI to poll at ~1 s via GET /api/jobs/{id}. Jobs are ephemeral on purpose —
canon lives on disk only (guardrail 2).
"""

import datetime
import threading
import uuid

_LOCK = threading.Lock()
_JOBS: dict = {}
LOG_CAP = 40  # log_tail keeps roughly the last 40 lines


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
        "_log_all": [],
    }
    with _LOCK:
        _JOBS[job_id] = job

    def log(line):
        line = str(line).rstrip("\n")
        with _LOCK:
            job["_log_all"].append(line)
            job["log_tail"] = job["_log_all"][-LOG_CAP:]

    def run():
        with _LOCK:
            job["status"] = "running"
        try:
            result = fn(log)
            with _LOCK:
                job["result"] = result
                job["status"] = "done"
        except Exception as e:  # noqa: BLE001 — any failure is a job error
            with _LOCK:
                job["error"] = f"{type(e).__name__}: {e}"
                job["status"] = "error"

    threading.Thread(target=run, name=f"job-{job_id}", daemon=True).start()
    return job_id


def get(job_id: str):
    """The public record for a job, or None if the id is unknown."""
    with _LOCK:
        job = _JOBS.get(job_id)
        return _public(job) if job else None
