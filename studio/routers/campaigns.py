"""GET/POST /api/campaigns, GET /api/campaign/{name}, GET .../geography,
publication recovery status/resolve, and the history-snapshot retry."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import canon
import publication
import spine
from routers.common import (ensure_entities_migrated, ensure_publishable_409,
                            require_campaign)

router = APIRouter()


class NewCampaign(BaseModel):
    name: str
    title: str = ""


class ResolveBody(BaseModel):
    transaction: str
    resolution: str  # "apply" | "discard"


@router.get("/api/campaigns")
def list_campaigns():
    return {"campaigns": spine.list_campaigns()}


@router.post("/api/campaigns", status_code=201)
def create_campaign(body: NewCampaign):
    """Start a campaign from nothing: directory + campaign.yaml + empty
    section dirs. The world itself arrives via POST .../world (seed)."""
    try:
        created = spine.create_campaign(body.name.strip(), body.title)
    except spine.CampaignExists as e:
        raise HTTPException(status_code=409, detail=str(e))
    except spine.CampaignError as e:
        raise HTTPException(status_code=422, detail=str(e))
    canon.snapshot(created["name"], f"campaign/{created['name']}: created")
    return created


@router.get("/api/campaign/{name}")
def campaign_summary(name: str):
    ensure_entities_migrated(name)  # lazy legacy yaml -> pages, no-op when done
    try:
        out = spine.summary(name)
    except spine.CampaignNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    # recovery/publication state rides on the summary so the UI can show a
    # banner instead of presenting a half-recovered campaign as canonical
    txs = publication.transactions(name)
    if any(t["status"] == "conflict" for t in txs) \
            or any(t["status"] == "complete"
                   and (t.get("snapshot") or {}).get("state") in
                   publication.NO_SNAPSHOT_STATES
                   for t in txs):
        out["publication"] = {"transactions": txs}
    return out


@router.post("/api/campaign/{name}/publication/resolve")
def publication_resolve(name: str, body: ResolveBody):
    """Resolve a recovery conflict explicitly: 'apply' forces the staged
    outcome over the external edit; 'discard' keeps the live bytes and
    abandons the transaction. Recovery never chooses by itself."""
    require_campaign(name)
    with spine.campaign_lock(name):
        try:
            return publication.resolve(name, body.transaction, body.resolution)
        except publication.PublicationError as e:
            raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/campaign/{name}/snapshot")
def snapshot_retry(name: str):
    """Retry the history snapshot for a campaign whose last publication
    committed but whose git snapshot failed or was unavailable. Also
    finishes the bookkeeping of any retained publication payloads."""
    require_campaign(name)
    ensure_publishable_409(name)
    with spine.campaign_lock(name):
        snap = canon.snapshot_result(name, f"campaign/{name}: snapshot retry")
        for t in publication.transactions(name):
            if t["status"] == "complete" and (t.get("snapshot") or {}).get(
                    "state") in publication.NO_SNAPSHOT_STATES:
                publication.after_snapshot(name, t["transaction"], snap)
    return {"snapshot": snap}


@router.get("/api/campaign/{name}/geography")
def campaign_geography(name: str):
    require_campaign(name)
    geo = spine.load_geography(name)
    if not geo:
        raise HTTPException(status_code=404,
                            detail="no world/geography.yaml in this campaign")
    return geo
