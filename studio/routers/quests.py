"""Quests API over studio/quests.py's read model (tickets/
06-quests-first-class.md) — no new tab: the World Building panel renders
the Quests card, rows open the existing lore-page view.

Reads are disk-fresh (hand-edits in Obsidian are equally valid; there is
no cache to invalidate). The one write is a reviewed short write: campaign
lock or 409, publication-path revision checks (a stale
expected_page_revision -> 409 with nothing written), transactional, and
the response carries the history snapshot like every canon change.
"""

import quests
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

# the shared error mapping for every quest read and write below: quest +
# spine errors -> 422, QuestConflict (stale revision) -> 409,
# QuestNotFound -> 404 (store_errors checks in argument order, so the
# subclasses listed in conflict/not_found win over their parent in 422)
_QUEST_422 = (quests.QuestError, spine.CampaignError)
_QUEST_CONFLICT = (quests.QuestConflict,
                   publication.PublicationConflictError)
_QUEST_NOT_FOUND = (quests.QuestNotFound,)


@router.get("/api/campaign/{name}/quests")
def get_quests(name: str):
    """Every `kind: quest` page, normalized: records, per-status counts,
    and advisory findings (unknown statuses degrade to hooked)."""
    require_campaign(name)
    return quests.load(name)


@router.get("/api/campaign/{name}/quests/{page}")
def get_quest(name: str, page: str):
    """One quest: the page + backlinks. {page} is the filename stem
    (display name, URL-encoded — routes never touch a shell)."""
    require_campaign(name)
    if not spine.valid_page_name(page or ""):
        raise HTTPException(status_code=422,
                            detail=f"invalid page name: {page!r}")
    with store_errors(*_QUEST_422, conflict=_QUEST_CONFLICT,
                      not_found=_QUEST_NOT_FOUND):
        return quests.quest_view(name, page)


class QuestPatchBody(BaseModel):
    """The reviewed status change. expected_page_revision (the revision
    the view rendered) makes an external Obsidian edit a 409 instead of a
    merge."""
    status: str
    expected_page_revision: str | None = None


@router.patch("/api/campaign/{name}/quests/{page}")
def patch_quest(name: str, page: str, body: QuestPatchBody):
    """Flip a quest's status (hooked | active | resolved | abandoned) on
    the page's frontmatter. Body and unknown frontmatter keys are
    preserved; the write is transactional and lands as a history
    snapshot."""
    require_campaign(name)
    with store_errors(*_QUEST_422, conflict=_QUEST_CONFLICT,
                      not_found=_QUEST_NOT_FOUND), \
            campaign_lock_nowait(name, "updating the quest"):
        ensure_publishable_409(name)
        result = quests.set_status(
            name, page, body.status,
            expected_page_revision=body.expected_page_revision)
        if result.get("transaction"):
            result["snapshot"] = finalize_publication(
                name, result["transaction"],
                f"campaign/{name}: quest {result.get('name')} -> "
                f"{result.get('status')}")
    return result
