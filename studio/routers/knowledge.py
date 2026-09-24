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
from pydantic import BaseModel

import knowledge
import publication
import spine
from routers.common import require_campaign, ensure_publishable_409

router = APIRouter()


class FactBody(BaseModel):
    text: str
    visibility: str | list[str]  # "dm_only" | "public" | [faction ids]
    source_file: str = ""
    subjects: list[str] | None = None
    id: str | None = None        # present = update the existing fact


class SupersedeBody(BaseModel):
    text: str
    visibility: str | list[str]
    source_file: str = ""
    subjects: list[str] | None = None


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
    Visibility must be explicit: dm_only, public, or a list of faction ids."""
    _require_state(name)
    try:
        with spine.campaign_lock(name):
            ensure_publishable_409(name)
            fact = knowledge.upsert(
                name, fact_id=body.id, text=body.text,
                visibility=body.visibility,
                source_file=body.source_file or "", subjects=body.subjects)
            pub = knowledge.compile_into_state(name)
    except knowledge.KnowledgeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except publication.PublicationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"fact": fact, "compiled": len(knowledge.compile_state_section(name)),
            "transaction": pub.get("transaction")}


@router.post("/api/campaign/{name}/knowledge/{fact_id}/supersede")
def post_supersede(name: str, fact_id: str, body: SupersedeBody):
    """Approve a corrected fact and retire the old one (provenance kept,
    never compiled again)."""
    _require_state(name)
    try:
        with spine.campaign_lock(name):
            ensure_publishable_409(name)
            new = knowledge.supersede(
                name, fact_id, text=body.text, visibility=body.visibility,
                source_file=body.source_file or "", subjects=body.subjects)
            pub = knowledge.compile_into_state(name)
    except knowledge.KnowledgeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except publication.PublicationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"fact": new, "superseded": fact_id,
            "compiled": len(knowledge.compile_state_section(name)),
            "transaction": pub.get("transaction")}


@router.post("/api/campaign/{name}/knowledge/{fact_id}/reconcile")
def post_reconcile(name: str, fact_id: str):
    """The DM has read the changed source file and re-approves it: the
    recorded revision catches up to the current bytes. The approved text is
    unchanged — staleness clears, nothing else moves."""
    require_campaign(name)
    with spine.campaign_lock(name):
        doc = knowledge.load(name)
        fact = next((f for f in doc["facts"] if f.get("id") == fact_id), None)
        if fact is None:
            raise HTTPException(status_code=404,
                                detail=f"no such fact: {fact_id}")
        if not fact.get("source_file"):
            raise HTTPException(status_code=422,
                                detail="this fact has no source file")
        try:
            knowledge.reconcile(name, fact_id)
        except knowledge.KnowledgeError as e:
            raise HTTPException(status_code=422, detail=str(e))
        updated = next(f for f in knowledge.load(name)["facts"]
                       if f["id"] == fact_id)
        return {"fact": updated}
