"""Approved facts with faction-specific visibility (Phase 4).

Approval makes a fact canonical; it does NOT make that fact public. The
DM records facts here — each with stable id, approved text, optional
source file + revision, subjects, and an explicit visibility — and the
studio compiles the visible slice into state.yaml's `knowledge` section
for the engine (views.py reads it; the faction prompt embeds the view).

The record file is simulation/knowledge.yaml — separate in meaning from
editorial lore (lore/) and from the simulation state (state.yaml holds
only the COMPILED copy).

Visibility contract:
  - "dm_only"   never leaves this file (the default for unclassified
                lore-facts; no automatic mass publication, ever)
  - "public"    any faction may see it (public in the world — still not
                automatically what the PARTY has discovered; player
                disclosure is a separate, later step)
  - ["mint", …] only those faction ids see it

Staleness: a fact whose source file changed since approval keeps its
APPROVED text (the compiled state never silently picks up new wording);
the GET marks it stale and the DM re-approves after reconciling.

Supersession: a corrected fact supersedes the old one — the old record
stays (provenance), flips status to superseded, and stops compiling.
"""

import datetime
import uuid

import publication
import spine

KNOWLEDGE_FILE = "simulation/knowledge.yaml"
SCHEMA_VERSION = 1
VISIBILITIES = ("dm_only", "public")


class KnowledgeError(Exception):
    """Knowledge-store problem that maps to a 4xx response."""


def _path(name: str):
    return spine.campaign_dir(name) / KNOWLEDGE_FILE


def load(name: str) -> dict:
    """The full store doc ({schema_version, facts: []}); tolerant of
    absence and of hand-edits that emptied it."""
    doc = spine._safe_load(_path(name))
    if not isinstance(doc, dict):
        return {"schema_version": SCHEMA_VERSION, "facts": []}
    if not isinstance(doc.get("facts"), list):
        doc["facts"] = []
    return doc


def _save(name: str, doc: dict) -> None:
    spine.write_text_atomic(_path(name), spine.dump_yaml(doc))


def source_revision(name: str, fact: dict) -> str:
    """The CURRENT revision of the fact's source file, or ABSENT. An unsafe
    (traversal) path — possible in hand-edited canon written before
    validation existed — reads as ABSENT, never probed."""
    rel = str(fact.get("source_file") or "")
    if not rel:
        return publication.ABSENT
    try:
        target = publication._safe_target(spine.campaign_dir(name), rel)
    except (spine.CampaignNotFound, publication.PublicationError):
        return publication.ABSENT
    return publication.file_revision(target)


def with_staleness(name: str, doc: dict) -> list:
    """The facts list annotated with stale: true where the source file's
    current bytes no longer match the approved revision."""
    out = []
    for fact in doc.get("facts", []):
        fact = dict(fact)
        recorded = fact.get("source_revision")
        if fact.get("source_file") and recorded:
            fact["stale"] = source_revision(name, fact) != recorded
        else:
            fact["stale"] = False
        out.append(fact)
    return out


def _validate_visibility(visibility, faction_ids) -> str | list:
    if visibility in VISIBILITIES:
        return visibility
    if isinstance(visibility, list):
        unknown = [v for v in visibility if v not in faction_ids]
        if unknown:
            raise KnowledgeError(
                "visibility names unknown faction(s): " + ", ".join(unknown))
        if not visibility:
            raise KnowledgeError(
                "an empty visibility list is dm_only — say what you mean")
        return [str(v) for v in visibility]
    raise KnowledgeError(
        "visibility must be 'dm_only', 'public', or a list of faction ids")


def apply_fact_records(name: str, doc: dict, faction_ids: set,
                       records: list) -> tuple:
    """PURE document transform: apply fact records (upserts and supersedes)
    to a caller-owned store doc and return (doc, created_facts) — no load,
    no save, no publication; the caller compiles and publishes the result
    (the session_changes flow builds its after-images with this instead of
    calling several independent save functions).

    Each record: {text, visibility, subjects?, place_ids?, source_file?,
    supersedes_fact_id?}. An unknown supersedes_fact_id raises
    KnowledgeError; superseding flips the old record to superseded on the
    SAME doc (one store write when the caller saves). created_facts maps
    the record's index to the new fact dict, so a caller can resolve
    references to facts the same batch creates."""
    created = {}
    for i, rec in enumerate(records or []):
        if not isinstance(rec, dict):
            raise KnowledgeError(f"fact record {i + 1}: not a mapping")
        old_id = rec.get("supersedes_fact_id")
        old = None
        if old_id:
            old = next((f for f in doc["facts"]
                        if f.get("id") == old_id), None)
            if old is None:
                raise KnowledgeError(f"no such fact: {old_id}")
        fact = _apply_upsert(name, doc, faction_ids,
                             text=rec.get("text"),
                             visibility=rec.get("visibility"),
                             subjects=rec.get("subjects"),
                             place_ids=rec.get("place_ids"),
                             source_file=rec.get("source_file") or "")
        if old is not None:
            old["status"] = "superseded"
            old["superseded_by"] = fact["id"]
        created[i] = fact
    return doc, created


def _apply_upsert(name: str, doc: dict, faction_ids: set, *, fact_id=None,
                  text, visibility, source_file: str = "", subjects=None,
                  source_revision: str | None = None,
                  place_ids=None) -> dict:
    """upsert's validation + mutation on a caller-owned (unsaved) doc.
    Shared by upsert (direct save) and the transactional record/supersede
    flows (the save rides the publication that also commits the compiled
    state)."""
    vis = _validate_visibility(visibility, faction_ids)
    text = str(text or "").strip()
    if not text:
        raise KnowledgeError("a fact needs text")
    fact = next((f for f in doc["facts"]
                 if f.get("id") == fact_id), None) if fact_id else None
    if fact is None:
        fact = {"id": "fact-" + uuid.uuid4().hex[:8],
                "created": datetime.datetime.now(
                    datetime.timezone.utc).isoformat(timespec="seconds"),
                "status": "current"}
        doc["facts"].append(fact)
    fact["text"] = text
    fact["visibility"] = vis
    if subjects is not None:
        fact["subjects"] = [str(s) for s in subjects]
    if place_ids is not None:
        # the places this fact is ABOUT (place-centered-campaign-view.md):
        # structured join metadata alongside the free-text subjects. Ids are
        # not checked against a registry here — the store must work before
        # any place page exists; the place view reports unknown ids as
        # findings instead of dropping them.
        fact["place_ids"] = [str(p).strip() for p in place_ids
                             if str(p).strip()]
    if source_file:
        # the path rides a validated target: a '..' or absolute rel would
        # hash an arbitrary file outside the campaign (and re-hash it on
        # every GET — a stale/fresh oracle) and pin the traversal into canon
        try:
            target = publication._safe_target(spine.campaign_dir(name),
                                              str(source_file))
        except publication.PublicationError as e:
            raise KnowledgeError(str(e))
        rel = target.relative_to(
            spine.campaign_dir(name).resolve()).as_posix()
        rev = publication.file_revision(target)
        if rev == publication.ABSENT:
            raise KnowledgeError(f"no such source file: {rel}")
        fact["source_file"] = rel
        fact["source_revision"] = source_revision or rev
        fact["stale"] = False
    fact["approved"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")
    return fact


def upsert(name: str, *, fact_id: str | None = None, text: str,
           visibility, source_file: str = "", subjects=None,
           source_revision: str | None = None, place_ids=None) -> dict:
    """Create or update one fact, saving the store directly. Creating
    records the source file's CURRENT revision as approved (when a source
    file is given); updates that change text/visibility re-approve against
    the current revision too. Caller holds the campaign lock. (The studio's
    record/supersede routes use the transactional variants below — this
    direct save is the standalone path.)"""
    doc = load(name)
    state = spine.load_state(name)
    faction_ids = {f.get("id") for f in (state.get("factions") or [])}
    fact = _apply_upsert(name, doc, faction_ids, fact_id=fact_id, text=text,
                         visibility=visibility, source_file=source_file,
                         subjects=subjects, source_revision=source_revision,
                         place_ids=place_ids)
    _save(name, doc)
    return fact


def supersede(name: str, old_id: str, *, text: str, visibility,
              source_file: str = "", subjects=None, place_ids=None) -> dict:
    """Approve a corrected fact and flip the old one to superseded (kept
    for provenance, never compiled again). Direct-save variant; the
    studio's route uses supersede_fact_tx. place_ids/subjects omitted in
    the correction body carry forward from the old fact — a typo fix must
    not silently strip the fact's place associations or subjects."""
    doc = load(name)
    old = next((f for f in doc["facts"] if f.get("id") == old_id), None)
    if old is None:
        raise KnowledgeError(f"no such fact: {old_id}")
    subjects = old.get("subjects") if subjects is None else subjects
    place_ids = old.get("place_ids") if place_ids is None else place_ids
    state = spine.load_state(name)
    faction_ids = {f.get("id") for f in (state.get("factions") or [])}
    new = _apply_upsert(name, doc, faction_ids, text=text,
                        visibility=visibility, source_file=source_file,
                        subjects=subjects, place_ids=place_ids)
    # _apply_upsert mutated OUR doc — mark the old record on the same
    # object, one save, no lost-update window
    old["status"] = "superseded"
    old["superseded_by"] = new["id"]
    _save(name, doc)
    return new


def record_fact_tx(name: str, *, fact_id: str | None = None, text: str,
                   visibility, source_file: str = "", subjects=None,
                   place_ids=None) -> tuple:
    """Record a fact AND compile the visible slice in ONE publication
    transaction: the store save and the compiled state commit are two
    writes of the same publish — a crash leaves neither or both, never a
    recorded fact the engine state never saw (the old two-step order left
    exactly that window, self-healing only on the next knowledge write).
    Caller holds the campaign lock. Returns (fact, publication result)."""
    doc = load(name)
    state = spine.load_state(name)
    faction_ids = {f.get("id") for f in (state.get("factions") or [])}
    fact = _apply_upsert(name, doc, faction_ids, fact_id=fact_id, text=text,
                         visibility=visibility, source_file=source_file,
                         subjects=subjects, place_ids=place_ids)
    return fact, _publish_store_and_state(name, doc, state,
                                          operation="record knowledge fact")


def supersede_fact_tx(name: str, old_id: str, *, text: str, visibility,
                      source_file: str = "", subjects=None,
                      place_ids=None) -> tuple:
    """supersede + compile in one publication transaction (same reasoning
    as record_fact_tx). place_ids/subjects omitted in the correction body
    carry forward from the old fact (see supersede). Returns (new fact,
    publication result)."""
    doc = load(name)
    old = next((f for f in doc["facts"] if f.get("id") == old_id), None)
    if old is None:
        raise KnowledgeError(f"no such fact: {old_id}")
    subjects = old.get("subjects") if subjects is None else subjects
    place_ids = old.get("place_ids") if place_ids is None else place_ids
    state = spine.load_state(name)
    faction_ids = {f.get("id") for f in (state.get("factions") or [])}
    new = _apply_upsert(name, doc, faction_ids, text=text,
                        visibility=visibility, source_file=source_file,
                        subjects=subjects, place_ids=place_ids)
    old["status"] = "superseded"
    old["superseded_by"] = new["id"]
    return new, _publish_store_and_state(
        name, doc, state, operation="supersede knowledge fact")


def _publish_store_and_state(name: str, doc: dict, state: dict,
                             operation: str) -> dict:
    """One transactional publish carrying BOTH the knowledge store and the
    state with the freshly compiled section."""
    state = dict(state or {})
    state["knowledge"] = compile_state_section(name, doc=doc)
    result = publication.publish(name, [
        {"path": KNOWLEDGE_FILE, "text": spine.dump_yaml(doc)},
        {"path": "simulation/state.yaml", "text": spine.dump_yaml(state)},
    ], operation=operation)
    return result


def reconcile(name: str, fact_id: str) -> dict:
    """Re-approve a fact's source file after the DM has read the changed
    bytes: the recorded revision catches up. Approved text unchanged."""
    doc = load(name)
    fact = next((f for f in doc["facts"] if f.get("id") == fact_id), None)
    if fact is None:
        raise KnowledgeError(f"no such fact: {fact_id}")
    if not fact.get("source_file"):
        raise KnowledgeError("this fact has no source file")
    fact["source_revision"] = source_revision(name, fact)
    fact["approved"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")
    _save(name, doc)
    return fact


def compile_state_section(name: str, doc: dict | None = None) -> list:
    """The engine-side knowledge list: current, non-superseded facts only,
    compiled to the explicit contract views.py reads:
      {id, text, visibility: "public"|"restricted", known_to: [ids]}
    dm_only and superseded facts never reach the engine. `doc` skips the
    reload when the caller holds the store it just mutated."""
    doc = doc if doc is not None else load(name)
    out = []
    for fact in doc.get("facts", []):
        if fact.get("status") not in (None, "current"):
            continue
        vis = fact.get("visibility")
        if vis == "public":
            out.append({"id": fact.get("id"), "text": fact.get("text"),
                        "visibility": "public", "known_to": []})
        elif isinstance(vis, list) and vis:
            out.append({"id": fact.get("id"), "text": fact.get("text"),
                        "visibility": "restricted",
                        "known_to": [str(v) for v in vis]})
        # dm_only: omitted entirely
    return out
