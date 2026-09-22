"""Lore tab API: reads, text-form writes, and M2 generation.

PUT entities validates with yaml.safe_load before writing — YAML is the
source of truth, the textarea is just a form over it. PUT style/{file} is
constrained to an already-existing [A-Za-z0-9._-] filename. Generation is
fed or refused (guardrail 4): POST entries 422s without world/geography.yaml
and otherwise runs as a job (the LLM call takes a minute), staging pending
entries and attaching advisory findings; POST name is synchronous and needs
a names-<culture>.txt seed list.
"""

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import canon
import jobs
import loregen
import spine
from routers.common import require_campaign
from spine import CampaignError, CampaignNotFound

router = APIRouter()


class TextBody(BaseModel):
    text: str


class NameBody(BaseModel):
    culture: str
    count: int = 8
    hint: str | None = None


@router.get("/api/campaign/{name}/lore")
def get_lore(name: str):
    require_campaign(name)
    style = {}
    for fn in spine.style_files(name):
        style[fn] = spine.read_style(name, fn)
    return {
        "timeline": spine.load_timeline(name),
        "entities": spine.load_entities(name),
        "style": style,
    }


@router.put("/api/campaign/{name}/lore/entities")
def put_entities(name: str, body: TextBody):
    require_campaign(name)
    try:
        doc = yaml.safe_load(body.text)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"invalid YAML: {e}")
    if not isinstance(doc, dict):
        raise HTTPException(status_code=422,
                            detail="entities.yaml must be a YAML mapping")
    spine.write_entities(name, body.text)
    canon.snapshot(name, f"campaign/{name}: update lore entities")
    return {"ok": True}


@router.put("/api/campaign/{name}/lore/style/{file}")
def put_style(name: str, file: str, body: TextBody):
    require_campaign(name)
    try:
        spine.write_style(name, file, body.text)
    except CampaignNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except CampaignError as e:
        raise HTTPException(status_code=422, detail=str(e))
    canon.snapshot(name, f"campaign/{name}: update style/{file}")
    return {"ok": True}


@router.post("/api/campaign/{name}/lore/entries", status_code=202)
def post_entries(name: str, body: dict | None = None):
    d = require_campaign(name)
    focus = ""
    if isinstance(body, dict) and isinstance(body.get("focus"), str):
        focus = body["focus"].strip()
    if not (d / "world" / "geography.yaml").is_file():
        raise HTTPException(
            status_code=422,
            detail="world/geography.yaml is missing — generation is fed or "
                   "refused (guardrail 4); import a world first")

    def job(log):
        result = loregen.generate_entries(d, focus, log=log)
        # the pending file is staged canon (derived, but reviewable work
        # worth a snapshot point); timeline.yaml only changes on approval
        canon.snapshot(name,
                       f"campaign/{name}: stage {result['entries']} pending "
                       "lore entries")
        return result

    return JSONResponse({"job_id": jobs.submit("lore-entries", job)},
                        status_code=202)


@router.post("/api/campaign/{name}/lore/name")
def post_name(name: str, body: NameBody):
    d = require_campaign(name)
    culture = (body.culture or "").strip()
    if not culture:
        raise HTTPException(status_code=422, detail="culture is required")
    try:
        names = loregen.name_candidates(d, culture, body.count, body.hint or "")
    except loregen.FedOrRefused as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"names": names}
