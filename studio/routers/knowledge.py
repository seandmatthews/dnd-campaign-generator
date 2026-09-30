"""Knowledge API (Phase 4): approved facts with faction-specific visibility.

GET lists the DM's fact records with staleness flags; POST upserts a fact
(classifying its visibility) and compiles the visible slice into the
simulation state's knowledge section through the publication helper; the
supersede route records corrections without deleting provenance; reconcile
re-approves a changed source file after the DM has read it.

Visibility is explicit or nothing ships: unclassified facts default to
dm_only and never reach the engine. There is no automatic mass publication
of existing lore — ever.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import briefing
import canon
import knowledge
import publication
from routers.common import (campaign_lock_nowait, ensure_publishable_409,
                            finalize_publication, require_campaign,
                            store_errors)

router = APIRouter()


class FactBody(BaseModel):
    text: str
    visibility: str | list[str]  # "dm_only" | "public" | [faction ids]
    source_file: str = ""
    subjects: list[str] | None = None
    # place ids this fact is about (place-centered-campaign-view.md)
    place_ids: list[str] | None = None
    id: str | None = None        # present = update the existing fact


class SupersedeBody(BaseModel):
    text: str
    visibility: str | list[str]
    source_file: str = ""
    subjects: list[str] | None = None
    place_ids: list[str] | None = None


def _require_state(name: str) -> None:
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign — knowledge "
                   "compiles into the simulation state, so create the "
                   "sandbox first")
    return d


@router.get("/api/campaign/{name}/knowledge")
def get_knowledge(name: str):
    require_campaign(name)
    doc = knowledge.load(name)
    return {"schema_version": doc.get("schema_version"),
            "facts": knowledge.with_staleness(name, doc)}


@router.post("/api/campaign/{name}/knowledge")
def post_knowledge(name: str, body: FactBody):
    """Record/approve a fact and compile the visible slice into the state.
    Visibility must be explicit: dm_only, public, or a list of faction ids.
    The store save and the compiled state commit are two writes of the ONE
    publication transaction — a crash can no longer leave a fact recorded
    but never compiled."""
    _require_state(name)
    try:
        with campaign_lock_nowait(name, "recording the fact"):
            ensure_publishable_409(name)
            fact, pub = knowledge.record_fact_tx(
                name, fact_id=body.id, text=body.text,
                visibility=body.visibility,
                source_file=body.source_file or "", subjects=body.subjects,
                place_ids=body.place_ids)
            snap = finalize_publication(
                name, pub["transaction"],
                f"campaign/{name}: record fact {fact['id']}")
    except knowledge.KnowledgeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except publication.PublicationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"fact": fact, "compiled": len(knowledge.compile_state_section(name)),
            "transaction": pub.get("transaction"), "snapshot": snap}


@router.post("/api/campaign/{name}/knowledge/{fact_id}/supersede")
def post_supersede(name: str, fact_id: str, body: SupersedeBody):
    """Approve a corrected fact and retire the old one (provenance kept,
    never compiled again). Store save + compiled state commit in one
    publication transaction, like record."""
    _require_state(name)
    try:
        with campaign_lock_nowait(name, "superseding the fact"):
            ensure_publishable_409(name)
            new, pub = knowledge.supersede_fact_tx(
                name, fact_id, text=body.text, visibility=body.visibility,
                source_file=body.source_file or "", subjects=body.subjects,
                place_ids=body.place_ids)
            snap = finalize_publication(
                name, pub["transaction"],
                f"campaign/{name}: supersede fact {fact_id}")
    except knowledge.KnowledgeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except publication.PublicationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"fact": new, "superseded": fact_id,
            "compiled": len(knowledge.compile_state_section(name)),
            "transaction": pub.get("transaction"), "snapshot": snap}


@router.post("/api/campaign/{name}/knowledge/{fact_id}/reconcile")
def post_reconcile(name: str, fact_id: str):
    """The DM has read the changed source file and re-approves it: the
    recorded revision catches up to the current bytes. The approved text is
    unchanged — staleness clears, nothing else moves."""
    require_campaign(name)
    with campaign_lock_nowait(name, "reconciling the fact"), \
            store_errors(knowledge.KnowledgeError):
        doc = knowledge.load(name)
        fact = next((f for f in doc["facts"] if f.get("id") == fact_id), None)
        if fact is None:
            raise HTTPException(status_code=404,
                                detail=f"no such fact: {fact_id}")
        if not fact.get("source_file"):
            raise HTTPException(status_code=422,
                                detail="this fact has no source file")
        knowledge.reconcile(name, fact_id)
        snap = canon.snapshot_result(
            name, f"campaign/{name}: reconcile fact {fact_id}")
        updated = next(f for f in knowledge.load(name)["facts"]
                       if f["id"] == fact_id)
        return {"fact": updated, "snapshot": snap}


# ------------------------------------------------- briefing + disclosure


@router.get("/api/campaign/{name}/briefing")
def get_briefing(name: str, since_week: int = 0, focus: str = ""):
    """The deterministic DM briefing: approved changes after `since_week`,
    affected factions/places, and unresolved decisions — capped at the
    first ten items with an honest 'N more not shown' count. `focus` is a
    comma-separated list of faction/place names. Items marked pending are
    proposals, never canon."""
    require_campaign(name)
    doc = briefing.build(
        name, since_week=since_week,
        focus=[f.strip() for f in focus.split(",") if f.strip()])
    return doc


@router.get("/api/campaign/{name}/briefing/markdown",
            response_class=PlainTextResponse)
def get_briefing_markdown(name: str, since_week: int = 0, focus: str = ""):
    """The same briefing as durable Markdown (save/print for the session)."""
    require_campaign(name)
    doc = briefing.build(
        name, since_week=since_week,
        focus=[f.strip() for f in focus.split(",") if f.strip()])
    return doc["markdown"]


@router.post("/api/campaign/{name}/knowledge/{fact_id}/reveal")
def post_reveal(name: str, fact_id: str, body: dict | None = None):
    """Disclose one current public fact to the party — the explicit reveal
    that makes public-in-world knowledge party knowledge."""
    require_campaign(name)
    with campaign_lock_nowait(name, "revealing the fact"), \
            store_errors(ValueError):
        week = None
        if isinstance(body, dict) and body.get("week") is not None:
            try:
                week = int(body["week"])
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=422,
                    detail=f"week must be an integer, got {body['week']!r}")
        briefing.reveal(name, fact_id, week=week)
        snap = canon.snapshot_result(
            name, f"campaign/{name}: reveal fact {fact_id}")
        return {"revealed": fact_id, "snapshot": snap}


@router.post("/api/campaign/{name}/knowledge/{fact_id}/revoke")
def post_revoke(name: str, fact_id: str):
    """Undo a reveal (not yet at the table)."""
    require_campaign(name)
    with campaign_lock_nowait(name, "revoking the reveal"):
        briefing.revoke(name, fact_id)
        snap = canon.snapshot_result(
            name, f"campaign/{name}: revoke fact {fact_id}")
        return {"revoked": fact_id, "snapshot": snap}


@router.get("/api/campaign/{name}/disclosure")
def get_disclosure(name: str):
    """The party-disclosure record: what has deliberately been revealed."""
    require_campaign(name)
    return briefing.load_disclosure(name)


@router.get("/api/campaign/{name}/disclosure/handout",
            response_class=PlainTextResponse)
def get_handout(name: str):
    """The shareable player handout: ONLY revealed facts. Empty disclosure
    produces an empty handout with an explanation — never a fallback to the
    DM briefing."""
    require_campaign(name)
    return briefing.handout_markdown(name)
