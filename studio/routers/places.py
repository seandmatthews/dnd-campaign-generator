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

import places
import publication
import spine
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routers.common import (
    campaign_lock_nowait,
    ensure_publishable_409,
    finalize_publication,
    require_campaign,
    store_errors,
)

router = APIRouter()

# the shared error mapping for every place read and write below: place
# store + spine errors -> 422, PlaceConflict (stale revision / identity)
# -> 409, PlaceNotFound -> 404 (exception tuples for store_errors — the
# context manager itself is single-use, so each route builds its own)
_PLACE_422 = (places.PlaceError, spine.CampaignError)
_PLACE_CONFLICT = (places.PlaceConflict,
                   publication.PublicationConflictError)
_PLACE_NOT_FOUND = (places.PlaceNotFound,)


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
    with store_errors(*_PLACE_422, conflict=_PLACE_CONFLICT,
                    not_found=_PLACE_NOT_FOUND):
        return places.place_view(name, place_id)


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
    with store_errors(*_PLACE_422, conflict=_PLACE_CONFLICT,
                    not_found=_PLACE_NOT_FOUND):
        view = places.place_view(name, place_id, window=window,
                                 include_descendants=include_descendants,
                                 event_limit=limit, event_offset=offset,
                                 mention_limit=1)
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
    with store_errors(*_PLACE_422, conflict=_PLACE_CONFLICT,
                    not_found=_PLACE_NOT_FOUND):
        view = places.place_view(name, place_id, mention_limit=limit,
                                 mention_offset=offset, event_limit=1)
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
    the lore page. The page is re-read under the campaign lock and the
    revision read then is the publish baseline — an external edit mid-flight
    is a 409, never a silent overwrite; expected_page_revision extends that
    guarantee to edits since the DM's view was rendered."""
    require_campaign(name)
    with store_errors(*_PLACE_422, conflict=_PLACE_CONFLICT,
                    not_found=_PLACE_NOT_FOUND), \
            campaign_lock_nowait(name, "linking place records"):
        ensure_publishable_409(name)
        result = places.link(name, place_id, page=body.page,
                             expected_page_revision=
                             body.expected_page_revision)
        if result.get("transaction"):
            finalize_publication(
                name, result["transaction"],
                f"campaign/{name}: link place {result.get('linked')}")
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
    with store_errors(*_PLACE_422, conflict=_PLACE_CONFLICT,
                    not_found=_PLACE_NOT_FOUND), \
            campaign_lock_nowait(name, "updating the place"):
        ensure_publishable_409(name)
        result = places.patch(
            name, place_id, name_=body.name,
            parent_place_id=body.parent_place_id, aliases=body.aliases,
            preferred_map=body.preferred_map,
            expected_page_revision=body.expected_page_revision)
        if result.get("transaction"):
            finalize_publication(
                name, result["transaction"],
                f"campaign/{name}: patch place {place_id} "
                f"({', '.join(result.get('patched') or [])})")
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
    with store_errors(*_PLACE_422, conflict=_PLACE_CONFLICT,
                    not_found=_PLACE_NOT_FOUND), \
            campaign_lock_nowait(name, "updating place associations"):
        ensure_publishable_409(name)
        result = places.associate(
            name, place_id, source_kind=body.source_kind,
            source_id=body.source_id, relationship=body.relationship,
            add=body.add, expected_revision=body.expected_revision)
        if result.get("transaction"):
            finalize_publication(
                name, result["transaction"],
                f"campaign/{name}: associate {body.source_kind} "
                f"{body.source_id} -> {place_id}")
    return result
