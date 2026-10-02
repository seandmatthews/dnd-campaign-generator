"""Sessions tab API: immutable session notes, drafts of suggested
consequences, bounded LLM extraction, and preparation of ONE selected
batch as a `session_changes` proposal (accepted through the shared
proposal endpoints — no second publication mechanism).

Error mapping: sessions.SessionError -> 422, sessions.SessionConflict ->
409 (stale editor / racing writer / pending proposal in the way).
"""

import logging

import sessions as session_store
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool

from routers.common import (
    campaign_lock_nowait,
    require_campaign,
    store_errors,
    strict_confirm,
    submit_campaign_job,
)

router = APIRouter()
_log = logging.getLogger("studio.sessions")


class SessionCreateBody(BaseModel):
    title: str
    notes: str
    # defaults to the CURRENT simulation week at save time — records never
    # advance the clock; a future week needs an explicit explanation
    event_week: int | None = None
    place_ids: list[str] = []
    future_reason: str = ""


class NotesBody(BaseModel):
    notes: str
    expected_revision: int


class ExtractBody(BaseModel):
    confirm: StrictBool
    _confirm_is_true = strict_confirm(
        "extraction runs a metered LLM call on your provider account")


class DraftEditBody(BaseModel):
    suggestions: list[dict]
    expected_revision: int


class DraftCreateBody(BaseModel):
    """A brand-new MANUAL draft: the no-LLM workflow's first step (a saved
    session has no draft until suggestions exist)."""
    suggestions: list[dict] = []


class SuggestionsAddBody(BaseModel):
    """Append NEW suggestions to a draft; id-less cards get server-minted
    ids."""
    suggestions: list[dict]
    expected_revision: int | None = None


class PrepareBody(BaseModel):
    selected: list[str]
    expected_draft_revision: int | None = None
    draft_id: str | None = None


@router.post("/api/campaign/{name}/sessions", status_code=201)
def post_session_notes(name: str, body: SessionCreateBody):
    require_campaign(name)
    with campaign_lock_nowait(name, "saving the session notes"):
        doc = session_store.create(
            name, title=body.title, notes=body.notes,
            event_week=body.event_week, place_ids=body.place_ids,
            future_reason=body.future_reason)
    return {"session_id": doc["session_id"], "title": doc["title"],
            "event_week": doc["event_week"], "revision": doc["revision"]}


@router.get("/api/campaign/{name}/sessions")
def get_sessions(name: str):
    require_campaign(name)
    return {"sessions": session_store.list_sessions(name)}


@router.get("/api/campaign/{name}/sessions/{session_id}")
def get_session(name: str, session_id: str):
    require_campaign(name)
    # API.md documents 404 for an unknown id; the global SessionError
    # handler defaults to 422, so map it here like the extract sibling
    with store_errors(not_found=(session_store.SessionError,)):
        return session_store.session_detail(name, session_id)


@router.post("/api/campaign/{name}/sessions/{session_id}/notes",
             status_code=201)
def post_notes_revision(name: str, session_id: str, body: NotesBody):
    require_campaign(name)
    with campaign_lock_nowait(name, "appending a notes revision"):
        doc = session_store.append_notes(
            name, session_id, body.notes, body.expected_revision)
    return {"session_id": doc["session_id"],
            "notes": doc["notes"], "revision": doc["revision"]}


@router.put("/api/campaign/{name}/sessions/{session_id}/drafts/{draft_id}")
def put_draft(name: str, session_id: str, draft_id: str,
              body: DraftEditBody):
    require_campaign(name)
    with campaign_lock_nowait(name, "saving draft edits"):
        draft = session_store.update_draft(
            name, session_id, draft_id,
            suggestions=body.suggestions,
            expected_revision=body.expected_revision)
    return draft


@router.post("/api/campaign/{name}/sessions/{session_id}/drafts",
             status_code=201)
def post_draft(name: str, session_id: str, body: DraftCreateBody):
    """Create the session's first MANUAL draft — manual drafting needs no
    API key and no extraction run."""
    require_campaign(name)
    with campaign_lock_nowait(name, "creating a manual draft"):
        return session_store.add_suggestions(name, session_id,
                                             body.suggestions)


@router.post("/api/campaign/{name}/sessions/{session_id}/drafts/"
             "{draft_id}/suggestions", status_code=201)
def post_suggestions(name: str, session_id: str, draft_id: str,
                     body: SuggestionsAddBody):
    """Add NEW suggestions to an existing draft (the 'add suggestion'
    action); existing ids are never touched."""
    require_campaign(name)
    with campaign_lock_nowait(name, "adding suggestions"):
        return session_store.add_suggestions(
            name, session_id, body.suggestions, draft_id=draft_id,
            expected_revision=body.expected_revision)


@router.post("/api/campaign/{name}/sessions/{session_id}/prepare")
def post_prepare(name: str, session_id: str, body: PrepareBody):
    """Stage the selected subset as one `session_changes` proposal.
    Synchronous 200: two bounded engine subprocess calls, no LLM — the
    same latency contract as the manual session endpoint."""
    require_campaign(name)
    return session_store.prepare(
        name, session_id, selected_ids=body.selected,
        expected_draft_revision=body.expected_draft_revision,
        draft_id=body.draft_id)


@router.post("/api/campaign/{name}/sessions/{session_id}/extract",
             status_code=202)
def post_extract(name: str, session_id: str, body: ExtractBody):
    """Bounded extraction: one metered LLM pass (at most two completion
    attempts, transport retries metered) mapping the notes to semantic
    suggestions. Runs as a job; nothing is applied — the output is a DRAFT
    the DM reviews."""
    require_campaign(name)
    with store_errors(not_found=(session_store.SessionError,)):
        session_store.load(name, session_id)
        notes_text, _ = session_store.read_notes(name, session_id)
    if len(notes_text) > session_store.NOTES_MAX_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"notes are {len(notes_text)} characters; extraction is "
                   f"bounded at {session_store.NOTES_MAX_CHARS} — append a "
                   "shorter revision or split the session")

    def job(log):
        # runs under the campaign lock via submit_campaign_job
        return session_store.run_extraction(name, session_id, log)

    return submit_campaign_job("session-extract", name, job)
