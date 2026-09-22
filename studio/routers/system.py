"""System: health, doctor (M5 — wired to studio/doctor.py), and jobs poll."""

from fastapi import APIRouter, HTTPException

import doctor
import jobs

router = APIRouter()


@router.get("/api/health")
def health():
    return {"ok": True, "version": "0.1.0"}


@router.get("/api/doctor")
def get_doctor():
    # doctor.py is stdlib-only and runs its probes (engine CLIs + one tiny
    # live LLM probe) per request; tests monkeypatch doctor.report.
    return doctor.report()


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job
