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
from pydantic import BaseModel

import canon
import loregen
import spine
from routers.common import require_campaign, submit_campaign_job
from spine import CampaignError, CampaignExists, CampaignNotFound

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


@router.post("/api/campaign/{name}/lore/style/{file}", status_code=201)
def post_style(name: str, file: str, body: TextBody):
    """Create a style file from the UI (PUT only edits existing ones). The
    purpose is staging a culture's name seed list — without one the name
    helper refuses to generate (fed-or-refused), and until now there was no
    way to stage the list from the app. Name lists need at least 3 seeds:
    an empty list would send the LLM in with nothing in-voice to draw from."""
    require_campaign(name)
    if file.startswith("names-"):
        seeds = [ln for ln in body.text.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        if len(seeds) < 3:
            raise HTTPException(
                status_code=422,
                detail="a name list needs at least 3 names, one per line "
                       "(lines starting with # are comments) — the helper "
                       "draws in-voice from these seeds")
    try:
        spine.create_style(name, file, body.text)
    except CampaignExists as e:
        raise HTTPException(
            status_code=409,
            detail=f"{e} — edit it in Style & voice instead")
    except CampaignNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except CampaignError as e:
        raise HTTPException(status_code=422, detail=str(e))
    canon.snapshot(name, f"campaign/{name}: stage style/{file}")
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
            detail="world/geography.yaml is missing — generate a world "
                   "first (Maps tab), then come back. Generation is fed or "
                   "refused: it needs ground data before it runs")

    def job(log):
        result = loregen.generate_entries(d, focus, log=log)
        # the pending file is staged canon (derived, but reviewable work
        # worth a snapshot point); timeline.yaml only changes on approval
        canon.snapshot(name,
                       f"campaign/{name}: stage {result['entries']} pending "
                       "lore entries")
        return result

    return submit_campaign_job("lore-entries", name, job)


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
