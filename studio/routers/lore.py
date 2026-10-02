"""Lore tab API: reads, text-form writes, and M2 generation.

Entities are vault pages now (obsidian-integration.md §1): GET lore returns
the canonical page list, GET lore/pages/{page} serves one page with its
computed backlinks. The old PUT entities (raw entities.yaml textarea) is
gone — pages are hand-edited in Obsidian or created by migration/stubbing,
and a PUT over them would be a form over the wrong file. PUT style/{file} is
constrained to an already-existing [A-Za-z0-9._-] filename. Generation is
fed or refused (guardrail 4): POST entries 422s without world/geography.yaml
and otherwise runs as a job (the LLM call takes a minute), staging pending
entries and attaching advisory findings; POST name is synchronous and needs
a names-<culture>.txt seed list.
"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import canon
import links
import llm_client
import loregen
import spine
from routers.common import (ensure_entities_migrated, require_campaign,
                            store_errors, submit_campaign_job)
from spine import CampaignError, CampaignExists, CampaignNotFound

router = APIRouter()


class TextBody(BaseModel):
    text: str


class NameBody(BaseModel):
    culture: str
    count: int = 8
    hint: str | None = None


class MissingPagesBody(BaseModel):
    """Optional bounds on a missing-pages run: a target subset and/or a cap.
    Absent/null means 'no bound' — the job decides. Typed so a malformed
    payload is a 422, not a silently ignored dict."""
    targets: list[str] | None = None
    limit: int | None = None


class EntriesBody(BaseModel):
    focus: str = ""


@router.get("/api/campaign/{name}/lore")
def get_lore(name: str):
    require_campaign(name)
    ensure_entities_migrated(name)
    style = {}
    for fn in spine.style_files(name):
        style[fn] = spine.read_style(name, fn)
    return {
        "timeline": spine.load_timeline(name),
        "entities": spine.load_entities(name),
        "style": style,
    }


@router.get("/api/campaign/{name}/lore/path")
def get_lore_path(name: str):
    """The resolved lore folder and the obsidian:// deep link into it.

    The browser only ever sees /api/... URLs — it cannot know the absolute
    campaign path — so the resolved folder, and the obsidian://open?path=
    URI URL-encoded from it, come from this one read-only GET. It is a
    pointer for the DM's outside editor (Obsidian); there is no write here
    and none will be (the no-in-app-editor ruling)."""
    lore = require_campaign(name) / "lore"
    path = str(lore.resolve())
    return {
        "path": path,
        "obsidian_uri": "obsidian://open?path=" + quote(path, safe=""),
    }


@router.get("/api/campaign/{name}/lore/pages/missing")
def get_missing_pages(name: str):
    """The stage-4 generation targets: dangling wiki-links grouped by name,
    most-linked first. Empty list = nothing to write."""
    require_campaign(name)
    return {"targets": links.missing_page_targets(name)}


@router.post("/api/campaign/{name}/lore/pages/missing", status_code=202)
def post_missing_pages(name: str, body: MissingPagesBody | None = None):
    """Write the missing pages (fed-or-refused, vault stage 4): each dangling
    wiki-link is a generation target grounded in the pages that link it.
    Body {"targets"?: [str]} limits the run to those names, and
    {"limit"?: int} to at most that many — bounding the run BEFORE it burns
    calls instead of recursively expanding every new dangling link. A job
    stages lore/pending-pages-N.yaml for review — nothing lands unreviewed.
    Deferred drafts are left alone either way: regeneration writes the next
    free pending file and never overwrites one awaiting a decision."""
    d = require_campaign(name)
    targets = None
    limit = None
    if body is not None:
        if body.targets is not None:
            targets = [t for t in body.targets if str(t).strip()]
        if body.limit is not None and body.limit > 0:
            limit = body.limit

    def job(log):
        result = loregen.generate_missing_pages(d, targets, log=log,
                                                limit=limit)
        canon.snapshot(name,
                       f"campaign/{name}: stage {result['pages']} pending "
                       "lore pages")
        return result

    return submit_campaign_job("lore-missing-pages", name, job)


@router.get("/api/campaign/{name}/lore/pages/{page}")
def get_entity_page(name: str, page: str):
    """One entity page + its backlinks (the 'everything about this' view).

    {page} is the filename stem (display name, URL-encoded — routes never
    pass it through a shell). Backlinks list chronicles and entities that
    link here, computed server-side so Obsidian need not be open."""
    require_campaign(name)
    if not spine.valid_page_name(page or ""):
        raise HTTPException(status_code=422, detail=f"invalid page name: {page!r}")
    idx = links.build_index(name)
    target = None
    for p in idx["pages"]:
        if p["source"] == "entity" and \
                p["file"] == f"lore/entities/{page}.md":
            target = p
            break
    if target is None:
        raise HTTPException(status_code=404, detail=f"no such entity page: {page}")
    target = dict(target)
    # the index is already built — pass it so backlinks need no second pass
    target["backlinks"] = links.backlinks_for(name, target["file"], idx)
    return target


@router.put("/api/campaign/{name}/lore/style/{file}")
def put_style(name: str, file: str, body: TextBody):
    require_campaign(name)
    with store_errors(CampaignError,
                      not_found=(CampaignNotFound,)):
        spine.write_style(name, file, body.text)
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
def post_entries(name: str, body: EntriesBody | None = None):
    d = require_campaign(name)
    focus = (body.focus if body is not None else "").strip()
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
    llm_client.begin_run()  # this request gets its own (fresh) call ceiling
    try:
        names = loregen.name_candidates(d, culture, body.count, body.hint or "")
    except loregen.FedOrRefused as e:
        raise HTTPException(status_code=422, detail=str(e))
    except llm_client.BudgetExceeded as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"names": names}
