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
from routers.common import (blocking_findings, campaign_lock_nowait,
                            ensure_entities_migrated, require_campaign,
                            store_errors, submit_campaign_job)
from routers.review import ReviewBody, approve_items
from spine import CampaignError, CampaignExists, CampaignNotFound

router = APIRouter()

AUTO_ACCEPT_HELP = ("auto-accept: approve the staged bundle through the "
                    "Review path the moment it lands (error-level "
                    "verification findings leave it pending instead)")


def _auto_accept_staged(name: str, result: dict, log) -> dict:
    """The auto-accept half of a lore staging job (ticket 02): the job
    itself approves the pending file it just staged, through the SAME
    approve path the Review tab uses (same publication transactions,
    receipts, snapshots), recorded in the transaction as auto: true.
    Findings at error level block it — the bundle stays pending, as today.
    The job already holds the campaign lock; the staged result is returned
    with an `auto_accept` note either way, and an approve failure never
    reads as a failed run (the staged bundle is the deliverable)."""
    findings = result.get("findings") or []
    blockers = blocking_findings(findings)
    if blockers:
        log(f"auto-accept blocked: {len(blockers)} error-level finding(s) — "
            "the staged entries stay pending for review")
        return {**result, "auto_accept": "blocked",
                "blocking_findings": blockers}
    pending = str(result.get("pending") or "")
    if not pending:
        return {**result, "auto_accept": "nothing-staged"}
    log(f"auto-accepting {pending} (the job is the approver; recorded as "
        "auto in the publication)")
    try:
        approved = approve_items(name, spine.campaign_dir(name),
                                 ReviewBody(file=pending, all=True),
                                 auto=True)
    except Exception as e:  # noqa: BLE001 — staged, but the approve failed
        log(f"auto-accept failed, the staged bundle stays pending: {e}")
        return {**result, "auto_accept": "error", "auto_accept_error": str(e)}
    log(f"auto-accepted: {approved.get('approved')} item(s) into the "
        f"timeline ({approved.get('transaction')})")
    return {**result, "auto_accept": "approved", "approval": approved}


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
    # opt-in auto-accept (ticket 02): approve the staged drafts through the
    # Review path as soon as they land — unless an error-level finding
    # leaves them pending
    auto_accept: bool = False


class EntriesBody(BaseModel):
    focus: str = ""
    # opt-in auto-accept (ticket 02) — same gate as missing-pages
    auto_accept: bool = False


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
    auto_accept = False
    if body is not None:
        if body.targets is not None:
            targets = [t for t in body.targets if str(t).strip()]
        if body.limit is not None and body.limit > 0:
            limit = body.limit
        auto_accept = bool(body.auto_accept)

    def job(log):
        result = loregen.generate_missing_pages(d, targets, log=log,
                                                limit=limit)
        canon.snapshot(name,
                       f"campaign/{name}: stage {result['pages']} pending "
                       "lore pages")
        if auto_accept:
            result = _auto_accept_staged(name, result, log)
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
    # the campaign lock like every other short canon writer (ticket 26):
    # an unlocked style PUT during a week job raced the job's
    # canon.snapshot, committing a half-published state under the wrong
    # message — 409 with the retry line instead, like its siblings.
    with campaign_lock_nowait(name, "updating the style file"), \
            store_errors(CampaignError,
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
    an empty list would send the LLM in with nothing in-voice to draw from.

    Takes the campaign lock like PUT (ticket 26) — it stages canon and
    snapshots it, exactly the write pair a week job must not interleave
    with."""
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
    with campaign_lock_nowait(name, "staging the style file"):
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
    auto_accept = bool(body.auto_accept) if body is not None else False
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
        if auto_accept:
            result = _auto_accept_staged(name, result, log)
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
    except RuntimeError as e:  # llm_client's readable provider errors —
        # the sibling LLM endpoints (simulation, maps) answer these 503
        raise HTTPException(status_code=503, detail=str(e))
    return {"names": names}
