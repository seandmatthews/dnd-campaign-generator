"""GET /api/campaigns, GET /api/campaign/{name}, GET .../geography (M0)."""

from fastapi import APIRouter, HTTPException

import spine
from routers.common import require_campaign

router = APIRouter()


@router.get("/api/campaigns")
def list_campaigns():
    return {"campaigns": spine.list_campaigns()}


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
