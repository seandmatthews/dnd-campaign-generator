"""Shared router helpers: campaign resolution maps spine errors to 404.

The server rewrites every HTTPException/RequestValidationError body to
{"error": "..."} per the contract, so raising HTTPException(status, msg)
here is the whole story.
"""

import contextlib

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import field_validator

import canon
import jobs
import publication
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


@contextlib.contextmanager
def store_errors(*default_422, conflict=(), not_found=()):
    """Convert listed store exception types to HTTPExceptions with the
    mapped status and str(e) as the detail: not_found -> 404, conflict ->
    409, everything in default_422 -> 422. Checked in that order, so a
    subclass listed in not_found/conflict wins over its parent in
    default_422. HTTPException passes through untouched."""
    try:
        yield
    except not_found as e:
        raise HTTPException(status_code=404, detail=str(e))
    except conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except default_422 as e:
        raise HTTPException(status_code=422, detail=str(e))


def finalize_publication(name: str, transaction_id: str | None,
                         message: str) -> dict:
    """The canonical post-publication snapshot: commit the campaign-wide
    git point and hand it to the transaction that just landed. Returns the
    snapshot result for responses that surface it."""
    snap = canon.snapshot_result(name, message)
    publication.after_snapshot(name, transaction_id, snap)
    return snap


def publication_500(op: str, e: Exception) -> HTTPException:
    """The mid-flight-publication 500: the staged transaction is kept on
    disk and boot recovery rolls it forward, so the message says that
    instead of reading as a bare failure."""
    return HTTPException(
        status_code=500,
        detail=f"the {op} could not be published ({e}) — the staged "
               "transaction is kept and will be recovered automatically "
               "when the studio restarts")


def strict_confirm(reason: str):
    """The `confirm` field validator for expensive-action bodies: the value
    must be exactly true (StrictBool already refuses the string "true"),
    and the 422 must name WHY this action is gated. Assign in a model body:
    `confirm: StrictBool` + `_confirm_is_true = strict_confirm("...")`."""
    @field_validator("confirm")
    @classmethod
    def _confirm_is_true(cls, v):
        if v is not True:
            raise ValueError(f"confirm must be exactly true — {reason}")
        return v
    return _confirm_is_true


def ensure_publishable_409(name: str) -> None:
    """Refuse publication-path writers while the campaign has unresolved
    recovery conflicts (409 with the affected files + how to resolve)."""
    with store_errors(conflict=(publication.PublicationConflictError,)):
        publication.ensure_publishable(name)


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
    of queueing a second mutation. Short interactive writers use
    campaign_lock_nowait (same fast-fail). Never nest acquisitions of one
    campaign's lock."""
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


def busy_409(action: str) -> HTTPException:
    """The fast-fail a DM sees when an interactive writer races a long job:
    a 409 that says retrying is expected, not that their click failed."""
    return HTTPException(
        status_code=409,
        detail=f"{action} cannot run right now: another job is already "
               "modifying this campaign (a week run?) — retry when it "
               "finishes. Nothing was changed.")


@contextlib.contextmanager
def campaign_lock_nowait(name: str, action: str):
    """The campaign lock for a SHORT interactive writer, or 409 — never a
    blocking acquire. A week job holds this lock for its whole run (up to
    30 minutes); a blocking acquire used to pin the HTTP request that long,
    and a browser or proxy timeout on the way turns the would-be success
    into an error the DM must guess about. Fast-fail exactly like
    submit_campaign_job, with a message that says when to retry."""
    lock = spine.campaign_lock(name)
    if not lock.acquire(blocking=False):
        raise busy_409(action)
    try:
        yield
    finally:
        lock.release()
