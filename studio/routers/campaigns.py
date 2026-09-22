"""GET/POST /api/campaigns, GET /api/campaign/{name}, GET .../geography."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import canon
import spine
from routers.common import require_campaign

router = APIRouter()


class NewCampaign(BaseModel):
    name: str
    title: str = ""


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
    try:
        return spine.summary(name)
    except spine.CampaignNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/campaign/{name}/geography")
def campaign_geography(name: str):
    require_campaign(name)
    geo = spine.load_geography(name)
    if not geo:
        raise HTTPException(status_code=404,
                            detail="no world/geography.yaml in this campaign")
    return geo
