"""Shared router helpers: campaign resolution maps spine errors to 404.

The server rewrites every HTTPException/RequestValidationError body to
{"error": "..."} per the contract, so raising HTTPException(status, msg)
here is the whole story.
"""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

import canon
import jobs
import spine

BUSY_DETAIL = "another job is already modifying this campaign"


def require_campaign(name: str):
    """Campaign dir or 404 (unknown or malformed name)."""
    try:
        return spine.require_campaign(name)
    except spine.CampaignNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except spine.CampaignError as e:
        raise HTTPException(status_code=404, detail=str(e))


def ensure_entities_migrated(name: str):
    """Lazy one-time entities.yaml -> entity pages migration (vault §1),
    called when a campaign is opened (summary) or its lore is read.

    Runs under the campaign lock, non-blocking: while a job is mutating the
    campaign the migration is skipped — the next open retries. Never raises;
    a campaign that cannot migrate keeps reading its legacy yaml (the loader
    is tolerant by design). Returns the page count, or None for nothing to
    do / skipped."""
    try:
        if not (spine.campaign_dir(name) / "lore" / "entities.yaml").is_file():
            return None
    except spine.CampaignNotFound:
        return None
    lock = spine.campaign_lock(name)
    if not lock.acquire(blocking=False):
        return None
    try:
        written = spine.migrate_entities_yaml_to_pages(name)
    except (spine.CampaignError, OSError):
        return None
    finally:
        lock.release()
    if written is not None:
        canon.snapshot(
            name, f"campaign/{name}: migrate entities.yaml to {written} "
                  "entity pages")
    return written


def submit_campaign_job(kind: str, name: str, job):
    """Submit a campaign-MUTATING long job under the per-campaign lock.

    The lock is acquired non-blocking here (submit time) and released in the
    worker thread when the job finishes; while it is held the route answers
    409 {"error": "another job is already modifying this campaign"} instead
    of queueing a second mutation. Review approve/delete take the same lock
    blocking (they wait). Never nest acquisitions of one campaign's lock."""
    lock = spine.campaign_lock(name)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=BUSY_DETAIL)

    def wrapped(log):
        try:
            return job(log)
        finally:
            lock.release()

    try:
        return JSONResponse({"job_id": jobs.submit(kind, wrapped)},
                            status_code=202)
    except BaseException:  # the job never started — do not hold the lock
        lock.release()
        raise
