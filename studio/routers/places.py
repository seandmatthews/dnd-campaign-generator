"""Places tab API: the place-centered campaign view (place-centered-
campaign-view.md) over studio/places.py's deterministic read model.

Reads work with no simulation, geography, or maps — missing optional
sections come back empty with the rest of the page intact. An unknown
place id is a 404; an id two lore pages both claim is a 422 (identity is
a conflict, never first-match-wins).

Writes (link / patch / associate) are SHORT interactive writers: campaign
lock or 409, publication-path revision checks (stale base revision -> 409),
transactional. They never accept arbitrary file paths — a mutation names
a source kind, source record id, relationship, and expected revision.
"""

import canon
import places
import publication
import spine
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routers.common import (
    campaign_lock_nowait,
    ensure_publishable_409,
    require_campaign,
)

router = APIRouter()


def _map_place_errors(e: places.PlaceError) -> HTTPException:
    """PlaceConflict (stale revision / identity) -> 409, PlaceNotFound ->
    404, every other PlaceError -> 422."""
    if isinstance(e, places.PlaceConflict):
        return HTTPException(status_code=409, detail=str(e))
    if isinstance(e, places.PlaceNotFound):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=422, detail=str(e))


@router.get("/api/campaign/{name}/places")
def get_places(name: str):
    """The Places navigation entry: explicit + provisional records,
    suggested joins (labeled as suggestions, never merged), conflicts."""
    require_campaign(name)
    return places.list_places(name)


@router.get("/api/campaign/{name}/places/{place_id}")
def get_place(name: str, place_id: str):
    """The consolidated DM view: identity, maps, people, factions,
    projects, recent events, facts, pending, forecasts, mentions."""
    require_campaign(name)
    try:
        return places.place_view(name, place_id)
    except places.PlaceError as e:
        raise _map_place_errors(e)


@router.get("/api/campaign/{name}/places/{place_id}/events")
def get_place_events(name: str, place_id: str, window: str = "recent",
                     include_descendants: bool = False,
                     limit: int = places.DEFAULT_LIMIT, offset: int = 0):
    """Paginated canon events explicitly located at this place — the
    all-history view behind the place page's recent window."""
    require_campaign(name)
    if window not in ("recent", "all"):
        raise HTTPException(status_code=422,
                            detail="window must be 'recent' or 'all'")
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    try:
        view = places.place_view(name, place_id, window=window,
                                 include_descendants=include_descendants,
                                 event_limit=limit, event_offset=offset,
                                 mention_limit=1)
    except places.PlaceError as e:
        raise _map_place_errors(e)
    return {"place": view["place"], "events": view["events"],
            "counts": view["counts"]["events"]}


@router.get("/api/campaign/{name}/places/{place_id}/mentions")
def get_place_mentions(name: str, place_id: str,
                       limit: int = places.DEFAULT_LIMIT, offset: int = 0):
    """Paginated wiki-link mentions (backlinks with excerpts) — context,
    never residence/ownership/event locations."""
    require_campaign(name)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    try:
        view = places.place_view(name, place_id, mention_limit=limit,
                                 mention_offset=offset, event_limit=1)
    except places.PlaceError as e:
        raise _map_place_errors(e)
    return {"place": view["place"], "mentions": view["mentions"],
            "counts": view["counts"]["mentions"]}


class LinkBody(BaseModel):
    """Confirm a legacy/source-to-ID association. The place id comes from
    the URL; the page stem names the lore page that will own the identity
    (required only when the place has no page yet)."""
    page: str | None = None
    expected_page_revision: str | None = None


@router.post("/api/campaign/{name}/places/{place_id}/link")
def post_link(name: str, place_id: str, body: LinkBody):
    """The one-time 'Link place records' action: persist the place_id onto
    the lore page (minting a durable id for lore-only records; adopting
    the simulation's id when one exists, so no state rewrite is needed).
    One file, one transaction; the page is re-read under the campaign lock
    and merged, and the revision read then is the publish baseline — an
    external edit mid-flight is a 409, never a silent overwrite. Passing
    expected_page_revision extends that guarantee to edits since the DM's
    view was rendered."""
    require_campaign(name)
    try:
        with campaign_lock_nowait(name, "linking place records"):
            ensure_publishable_409(name)
            result = places.link(name, place_id, page=body.page,
                                 expected_page_revision=
                                 body.expected_page_revision)
            if result.get("transaction"):
                snap = canon.snapshot_result(
                    name, f"campaign/{name}: link place "
                          f"{result.get('linked')}")
                publication.after_snapshot(name, result["transaction"], snap)
    except places.PlaceError as e:
        raise _map_place_errors(e)
    except spine.CampaignError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except publication.PublicationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return result


class PatchBody(BaseModel):
    """Rename / parent / aliases / preferred map. All optional; only the
    present fields change. Rename keeps the place_id and folds the old
    name into aliases so human wiki-links keep resolving."""
    name: str | None = None
    parent_place_id: str | None = None
    aliases: list[str] | None = None
    preferred_map: str | None = None
    expected_page_revision: str | None = None


@router.patch("/api/campaign/{name}/places/{place_id}")
def patch_place(name: str, place_id: str, body: PatchBody):
    require_campaign(name)
    try:
        with campaign_lock_nowait(name, "updating the place"):
            ensure_publishable_409(name)
            result = places.patch(
                name, place_id, name_=body.name,
                parent_place_id=body.parent_place_id, aliases=body.aliases,
                preferred_map=body.preferred_map,
                expected_page_revision=body.expected_page_revision)
            if result.get("transaction"):
                snap = canon.snapshot_result(
                    name, f"campaign/{name}: patch place {place_id} "
                          f"({', '.join(result.get('patched') or [])})")
                publication.after_snapshot(name, result["transaction"], snap)
    except places.PlaceError as e:
        raise _map_place_errors(e)
    except publication.PublicationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except spine.CampaignError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result


class AssociationBody(BaseModel):
    """One typed association update to an allowlisted source record:
    entity (resides_at/present_at), event (occurred_at), fact (about),
    faction (interest), map (depicts). `add` false removes."""
    source_kind: str
    source_id: str
    relationship: str
    add: bool = True
    expected_revision: str | None = None


@router.post("/api/campaign/{name}/places/{place_id}/associations")
def post_association(name: str, place_id: str, body: AssociationBody):
    require_campaign(name)
    try:
        with campaign_lock_nowait(name, "updating place associations"):
            ensure_publishable_409(name)
            result = places.associate(
                name, place_id, source_kind=body.source_kind,
                source_id=body.source_id, relationship=body.relationship,
                add=body.add, expected_revision=body.expected_revision)
            if result.get("transaction"):
                snap = canon.snapshot_result(
                    name, f"campaign/{name}: associate {body.source_kind} "
                          f"{body.source_id} -> {place_id}")
                publication.after_snapshot(name, result["transaction"], snap)
    except places.PlaceError as e:
        raise _map_place_errors(e)
    except spine.CampaignError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except publication.PublicationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return result
