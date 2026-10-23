"""Shared router helpers: campaign resolution maps spine errors to 404.

The server rewrites every HTTPException/RequestValidationError body to
{"error": "..."} per the contract, so raising HTTPException(status, msg)
here is the whole story.
"""

import contextlib
import time

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import field_validator

import canon
import job_history
import jobs
import publication
import spine

BUSY_DETAIL = "another job is already modifying this campaign"

# Dead-man cap on a queued job's wait for the campaign lock (the queue's
# whole point is to wait, so this is far above the longest legal job — a
# 3-week forecast — times a few queued behind it; it only fires when
# something is actually wrong, e.g. a CLI process died holding the lock
# in a way the OS could not release).
QUEUE_LOCK_WAIT_S = 4 * 60 * 60


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


def blocking_findings(findings) -> list:
    """The error-level findings in a staged result — the auto-accept gate:
    advisory findings ride along as always, but one at `error` level means
    the bundle stays pending for a human (ticket 02). Non-dict findings
    (the engine's dropped-update notes) are informational, never a gate."""
    return [f for f in (findings or [])
            if isinstance(f, dict) and str(f.get("level") or "") == "error"]


# shared from job_history (ticket 13) so busy_409, the engine-home 409s,
# and proposal_flows._lock have ONE implementation; kept importable from
# routers.common because the routers import it from here
blocked_by_line = job_history.blocked_by_line


def submit_campaign_job(kind: str, name: str, job, take_lock: bool = True):
    """Submit a campaign-MUTATING long job through the campaign's FIFO
    queue (ticket 02): the answer is 202 immediately — with a queue
    position when another job is already running — and a per-campaign
    worker drains the queue in order. The per-campaign lock stays exactly
    as it was (taken before the job runs, released when it ends — the
    before_run hook below hands that discipline to the queue): the queue
    just means writers wait their turn instead of being refused.

    The queue is safe because the confirm gates do not move: `confirm:
    true` is still given at submit time, so a queued week already carries
    the DM's explicit spend approval — nothing in the queue waits on a
    decision the DM is not present for. Short interactive writers keep
    fast-failing via campaign_lock_nowait (with a wait estimate). Never
    nest acquisitions of one campaign's lock.

    take_lock=False is the NO-LOCK LANE (ticket 42): the job queues and
    drains in order like any other, but takes the campaign lock at no
    point — for read-only passes that write nothing and must never block,
    nor be blocked by, a canon writer (the sandbox design's recorded
    decision, ticket 15). The queue still serializes the lane, so a
    second design waits its turn (visible, cancellable) instead of
    running twice at once."""
    lock = spine.campaign_lock(name)

    def before_run(log, entry):
        # the turn-taking lives in the queue so the record stays "queued"
        # (position visible, cancellable) until the lock is actually held
        jobs.take_turn(log, entry, lock, max_wait_s=QUEUE_LOCK_WAIT_S)
        return lock.release  # the drainer releases when the job ends

    def wrapped(log):
        started = time.monotonic()
        try:
            return job(log)
        finally:
            # duration history feeds the wait estimates busy 409s carry;
            # best-effort — a history failure must never read as a job error
            try:
                job_history.record(name, kind, time.monotonic() - started)
            except Exception:  # noqa: BLE001 — best-effort by contract
                pass

    # "queued" must be honest even when this submission is the only one in
    # the queue: a held lock (another campaign's writer, a CLI process)
    # means the job waits for its turn even with nothing studio-side ahead
    # of it — report that as position 1 (behind the current holder). The
    # no-lock lane does not wait on the lock, so only real queue-mates
    # count for it. The probe runs BEFORE the submit, on purpose: after
    # it, this job's OWN drainer may already hold the lock through
    # before_run, and the probe would report the job as queued behind
    # itself — first-in-line submissions answered "position 1" on thread
    # scheduling alone (reproduced 6/6 in the ticket 55 lane), and the
    # same race could answer False with a genuinely-held lock mid-handoff.
    held_before = take_lock and lock.locked()
    job_id, position = jobs.submit_queued(
        name, kind, wrapped, before_run=before_run if take_lock else None)
    if position == 0 and held_before:
        position = 1
    return JSONResponse({"job_id": job_id, "queued": position > 0,
                         "queue_position": position},
                        status_code=202)


def busy_409(action: str, name: str | None = None) -> HTTPException:
    """The fast-fail a DM sees when an interactive writer races a long job:
    a 409 that says retrying is expected, not that their click failed — and
    (ticket 02) what is running and how long it usually takes, from the
    rolling duration history, so the wait is plannable."""
    running = jobs.running_jobs(campaign=name) if name else []
    line = blocked_by_line(running) if running else ""
    return HTTPException(
        status_code=409,
        detail=f"{action} cannot run right now: {BUSY_DETAIL}"
               + (line or " (a week run?)") + " — retry when it finishes. "
               "Nothing was changed.")


def running_job_line(name: str) -> str:
    """The " Blocked by: …" line for a generation job RUNNING in this
    campaign, or "". The 409s raised BEFORE the lock (the pending-proposal
    gates) must name it too: while a job runs, the follow-up its message
    points at (accept/discard) 409s as well, and the job itself was
    otherwise visible only in the studio log — the silent-blocker class
    (2026-10-03)."""
    running = jobs.running_jobs(campaign=name) if name else []
    return blocked_by_line(running) if running else ""


def pending_proposal_409(name: str, pending: dict, what: str) -> HTTPException:
    """The one implementation of the pre-lock pending-proposal gate (the
    submit-side checks that run before campaign_lock_nowait): the pending
    proposal is the correctness blocker, and when a generation job is also
    running the 409 says so — that job finishes first in practice, since
    even the accept/discard it points at takes the campaign lock."""
    detail = (f"a proposal is awaiting review ({pending.get('proposal_id')})"
              f" — accept or discard it before {what}")
    line = running_job_line(name)
    if line:
        detail += (". Another job is also running and finishes first:"
                   + line)
    return HTTPException(status_code=409, detail=detail)


@contextlib.contextmanager
def campaign_lock_nowait(name: str, action: str):
    """The campaign lock for a SHORT interactive writer, or 409 — never a
    blocking acquire. A week job holds this lock for its whole run (up to
    the derived week watchdog — ~2 h 10 m by default, env-tunable via
    STUDIO_WEEK_TIMEOUT_S); a blocking acquire used to pin the HTTP
    request that long, and a browser or proxy timeout on the way turns the would-be success
    into an error the DM must guess about. Fast-fail exactly like
    submit_campaign_job, with a message that says when to retry and what
    the wait looks like. Delegates to spine.lock_nowait — the one
    implementation — and maps CampaignBusy to the router's 409."""
    try:
        with spine.lock_nowait(name, action):
            yield
    except spine.CampaignBusy as e:
        raise busy_409(action, name=name) from e

