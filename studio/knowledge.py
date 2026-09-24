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

import yaml

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
    spine.write_text_atomic(
        _path(name),
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))


def source_revision(name: str, fact: dict) -> str:
    """The CURRENT revision of the fact's source file, or ABSENT."""
    rel = str(fact.get("source_file") or "")
    if not rel:
        return publication.ABSENT
    try:
        return publication.file_revision(spine.campaign_dir(name) / rel)
    except spine.CampaignNotFound:
        return publication.ABSENT


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


def upsert(name: str, *, fact_id: str | None = None, text: str,
           visibility, source_file: str = "", subjects=None,
           source_revision: str | None = None) -> dict:
    """Create or update one fact. Creating records the source file's
    CURRENT revision as approved (when a source file is given); updates
    that change text/visibility re-approve against the current revision
    too. Caller holds the campaign lock."""
    doc = load(name)
    state = spine.load_state(name)
    faction_ids = {f.get("id") for f in (state.get("factions") or [])}
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
    if source_file:
        rel = str(source_file).replace("\\", "/")
        rev = publication.file_revision(spine.campaign_dir(name) / rel)
        if rev == publication.ABSENT:
            raise KnowledgeError(f"no such source file: {rel}")
        fact["source_file"] = rel
        fact["source_revision"] = source_revision or rev
        fact["stale"] = False
    fact["approved"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")
    _save(name, doc)
    return fact


def supersede(name: str, old_id: str, *, text: str, visibility,
              source_file: str = "", subjects=None) -> dict:
    """Approve a corrected fact and flip the old one to superseded (kept
    for provenance, never compiled again)."""
    doc = load(name)
    old = next((f for f in doc["facts"] if f.get("id") == old_id), None)
    if old is None:
        raise KnowledgeError(f"no such fact: {old_id}")
    new = upsert(name, text=text, visibility=visibility,
                 source_file=source_file, subjects=subjects)
    # upsert re-loaded and saved the store — reload before mutating the old
    # record, or this save would clobber the new fact (lost update)
    doc = load(name)
    old = next(f for f in doc["facts"] if f.get("id") == old_id)
    old["status"] = "superseded"
    old["superseded_by"] = new["id"]
    _save(name, doc)
    return new


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


def compile_state_section(name: str) -> list:
    """The engine-side knowledge list: current, non-superseded facts only,
    compiled to the explicit contract views.py reads:
      {id, text, visibility: "public"|"restricted", known_to: [ids]}
    dm_only and superseded facts never reach the engine."""
    doc = load(name)
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


def compile_into_state(name: str) -> dict:
    """Write the compiled knowledge section into simulation/state.yaml
    through the publication helper (revision-checked, transactional).
    Caller holds the campaign lock. Returns the publication result."""
    compiled = compile_state_section(name)
    state = spine.load_state(name)
    state["knowledge"] = compiled
    text = yaml.safe_dump(state, sort_keys=False, allow_unicode=True, width=100)
    result = publication.publish(
        name, [{"path": "simulation/state.yaml", "text": text}],
        operation="compile knowledge into simulation state")
    return result
