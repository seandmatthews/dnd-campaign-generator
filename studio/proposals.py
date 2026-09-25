"""Proposed-week bundles: a simulated week is GENERATED into a proposal and
only enters canon when the DM accepts the whole thing.

A bundle lives at simulation/proposals/<proposal-id>/ — outside every
lore/entity/pending glob, so no reader or generation pass mistakes recovery
or proposal data for canon:

    proposal.yaml        schema_version, proposal_id, kind, status,
                         base_week, proposed_week, base_revisions (exact-byte
                         hashes of what the run was built on),
                         input_set_revisions (directory fingerprints),
                         artifacts{}, run_record{seed, model, base_url...},
                         created, decided
    artifacts/state.yaml the proposed new simulation state (after-image)
    artifacts/chronicle/ the proposed chronicle files
    artifacts/timeline-entries.yaml   proposed timeline entries
    artifacts/diff.json  structured before/after (clock, resources, events,
                         rumors) for the review UI

Statuses: pending -> accepted | discarded. Exactly one pending proposal per
campaign; generating another is refused until the DM decides. Discarding
keeps the bundle as an audit receipt — rejected content never re-enters
prompts (the bundle is outside every generation input glob).

Nothing here writes canon: acceptance goes through publication.publish with
the recorded base revisions, so an external edit between generation and
acceptance is a 409, never a silent overwrite.
"""

import datetime
import json
import re
import uuid
from pathlib import Path

import yaml

import publication
import spine

PROPOSALS_DIR = "simulation/proposals"
SCHEMA_VERSION = 1


class ProposalError(Exception):
    """Proposal-store problem that maps to a 4xx response."""


def _root(campaign_dir: Path) -> Path:
    return campaign_dir / "simulation" / "proposals"


def _bundle(name: str, proposal_id: str) -> Path:
    if (not proposal_id or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*",
                                            proposal_id)
            or ".." in proposal_id):
        raise ProposalError(f"invalid proposal id: {proposal_id!r}")
    return _root(spine.campaign_dir(name)) / proposal_id


def new_id() -> str:
    return time_id() + "-" + uuid.uuid4().hex[:8]


def time_id() -> str:
    return datetime.datetime.now(datetime.timezone.utc) \
        .strftime("%Y%m%dT%H%M%S")


# ------------------------------------------------------------------ writing


def create(name: str, *, base_week, proposed_week, base_revisions: dict,
           input_set_revisions: dict, artifacts: dict, run_record: dict,
           before_state: dict, kind: str = "simulated_week") -> str:
    """Stage a new pending bundle from in-memory artifact texts. artifacts:
    {"state": text, "chronicles": {filename: text}, "entries": [dicts]}.
    kind: "simulated_week" or "session_effects". Returns the proposal id.
    Caller holds the campaign lock."""
    d = spine.campaign_dir(name)
    pid = new_id()
    bdir = _root(d) / pid
    (bdir / "artifacts" / "chronicle").mkdir(parents=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": pid,
        "kind": kind,
        "status": "pending",
        "base_week": base_week,
        "proposed_week": proposed_week,
        "base_revisions": base_revisions,
        "input_set_revisions": input_set_revisions,
        "artifacts": {},
        "run_record": run_record,
        "created": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "decided": None,
    }
    state_text = artifacts.get("state") or ""
    (bdir / "artifacts" / "state.yaml").write_text(state_text,
                                                   encoding="utf-8")
    manifest["artifacts"]["state"] = "artifacts/state.yaml"
    chronicles = artifacts.get("chronicles") or {}
    for fn in sorted(chronicles):
        (bdir / "artifacts" / "chronicle" / fn).write_text(
            chronicles[fn], encoding="utf-8")
        manifest["artifacts"].setdefault("chronicles", {})[fn] = \
            f"artifacts/chronicle/{fn}"
    entries = artifacts.get("entries") or []
    (bdir / "artifacts" / "timeline-entries.yaml").write_text(
        yaml.safe_dump(entries, sort_keys=False, allow_unicode=True,
                       width=100), encoding="utf-8")
    manifest["artifacts"]["timeline_entries"] = \
        "artifacts/timeline-entries.yaml"
    updates = artifacts.get("updates") or []
    (bdir / "artifacts" / "goal-updates.yaml").write_text(
        yaml.safe_dump(updates, sort_keys=False, allow_unicode=True,
                       width=100), encoding="utf-8")
    manifest["artifacts"]["goal_updates"] = "artifacts/goal-updates.yaml"
    replay_text = artifacts.get("replay")
    if replay_text:
        (bdir / "artifacts" / "replay.yaml").write_text(
            replay_text, encoding="utf-8")
        manifest["artifacts"]["replay"] = "artifacts/replay.yaml"
    diff = diff_states(before_state,
                       yaml.safe_load(state_text) or {},
                       entries)
    (bdir / "artifacts" / "diff.json").write_text(
        json.dumps(diff, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["artifacts"]["diff"] = "artifacts/diff.json"
    (bdir / "proposal.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True,
                       width=100), encoding="utf-8")
    return pid


def set_status(name: str, proposal_id: str, status: str,
               decided_note: str = "") -> dict:
    """pending -> accepted|discarded. The bundle stays on disk as the audit
    receipt either way. Caller holds the campaign lock."""
    bdir = _bundle(name, proposal_id)
    manifest_path = bdir / "proposal.yaml"
    if not manifest_path.is_file():
        raise ProposalError(f"no such proposal: {proposal_id}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if manifest.get("status") != "pending":
        raise ProposalError(
            f"proposal {proposal_id} is {manifest.get('status')!r}, "
            "not pending")
    manifest["status"] = status
    manifest["decided"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")
    if decided_note:
        manifest["decision_note"] = decided_note
    spine.write_text_atomic(manifest_path, yaml.safe_dump(
        manifest, sort_keys=False, allow_unicode=True, width=100))
    return manifest


# ------------------------------------------------------------------ reading


def _load_manifest(bdir: Path) -> dict | None:
    try:
        doc = yaml.safe_load(
            (bdir / "proposal.yaml").read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, yaml.YAMLError):
        return None


def find_pending(name: str) -> dict | None:
    """The one pending proposal, or None. Newest first if data was hand-
    mangled into two pendings — the routes refuse to add a third."""
    root = _root(spine.campaign_dir(name))
    if not root.is_dir():
        return None
    for bdir in sorted(root.iterdir(), reverse=True):
        if not bdir.is_dir():
            continue
        m = _load_manifest(bdir)
        if m and m.get("status") == "pending":
            return m
    return None


def load(name: str, proposal_id: str) -> dict:
    """{manifest, state_text, chronicles: {fn: text}, entries: [dicts],
    diff: dict} — everything the review UI and the accept route need."""
    bdir = _bundle(name, proposal_id)
    manifest = _load_manifest(bdir)
    if not manifest:
        raise ProposalError(f"no such proposal: {proposal_id}")
    arts = bdir / "artifacts"
    out = {"manifest": manifest, "proposal_id": str(manifest.get(
        "proposal_id") or proposal_id)}
    try:
        out["state_text"] = (arts / "state.yaml").read_text(encoding="utf-8")
        chron = {}
        cdir = arts / "chronicle"
        if cdir.is_dir():
            for p in sorted(cdir.iterdir()):
                if p.is_file():
                    chron[p.name] = p.read_text(encoding="utf-8")
        out["chronicles"] = chron
        entries = yaml.safe_load(
            (arts / "timeline-entries.yaml").read_text(encoding="utf-8"))
        out["entries"] = entries if isinstance(entries, list) else []
        updates_path = arts / "goal-updates.yaml"
        if updates_path.is_file():
            updates = yaml.safe_load(
                updates_path.read_text(encoding="utf-8"))
            out["updates"] = updates if isinstance(updates, list) else []
        else:
            out["updates"] = []
        diff = json.loads((arts / "diff.json").read_text(encoding="utf-8"))
        out["diff"] = diff if isinstance(diff, dict) else {}
    except (OSError, yaml.YAMLError, ValueError) as e:
        raise ProposalError(f"proposal {proposal_id} is damaged: {e}")
    return out


# --------------------------------------------------------------------- diff


def _clock(doc: dict):
    return ((doc.get("meta") or {}).get("clock") or {}).get("week")


def diff_states(before: dict, after: dict, entries: list) -> dict:
    """The structured before/after the review UI shows alongside the
    chronicle: clock, per-faction resource changes, added events and
    rumors. Mechanical only — prose stays in the chronicle."""
    diff = {
        "clock": {"before": _clock(before or {}), "after": _clock(after or {})},
        "resources": {},
        "fields": {},
        "events_added": [],
        "rumors_added": [],
    }
    bf = {f.get("id"): f for f in ((before or {}).get("factions") or [])
          if isinstance(f, dict)}
    af = {f.get("id"): f for f in ((after or {}).get("factions") or [])
          if isinstance(f, dict)}
    for fid, f_after in af.items():
        f_before = bf.get(fid) or {}
        res_b = f_before.get("resources") or {}
        res_a = f_after.get("resources") or {}
        changed = {k: {"before": res_b.get(k), "after": res_a.get(k)}
                   for k in set(res_b) | set(res_a)
                   if res_b.get(k) != res_a.get(k)}
        if changed:
            diff["resources"][fid] = changed
        # fact fields (goal/kind/secret) — what a set_field effect moved
        f_changed = {k: {"before": f_before.get(k), "after": f_after.get(k)}
                     for k in ("goal", "kind", "secret")
                     if (f_before.get(k) or "") != (f_after.get(k) or "")}
        if f_changed:
            diff["fields"][fid] = f_changed
    ev_b = before or {}
    before_events = {(e.get("week"), e.get("actor"), e.get("text"))
                     for e in (ev_b.get("events") or [])
                     if isinstance(e, dict)}
    for e in ((after or {}).get("events") or []):
        if isinstance(e, dict) and (e.get("week"), e.get("actor"),
                                    e.get("text")) not in before_events:
            diff["events_added"].append(
                {"week": e.get("week"), "actor": e.get("actor"),
                 "text": str(e.get("text") or ""),
                 "source": e.get("source"), "effect": e.get("effect")})
    before_rumors = {r.get("claim") for r in (ev_b.get("rumors") or [])
                     if isinstance(r, dict)}
    for r in ((after or {}).get("rumors") or []):
        if isinstance(r, dict) and r.get("claim") not in before_rumors:
            diff["rumors_added"].append(
                {"claim": str(r.get("claim") or ""),
                 "fidelity": str(r.get("fidelity") or "")})
    diff["timeline_entries"] = len(entries or [])
    return diff
