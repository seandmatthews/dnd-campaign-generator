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

import spine
import yaml

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
           before_state: dict, kind: str = "simulated_week",
           chronicle_meta: dict | None = None,
           extra_artifacts: dict | None = None,
           manifest_extra: dict | None = None,
           diff_extra: dict | None = None,
           proposal_id: str | None = None) -> str:
    """Stage a new pending bundle from in-memory artifact texts. artifacts:
    {"state": text, "chronicles": {filename: text}, "entries": [dicts],
    "updates": [dicts], "findings": [str]}. kind: "simulated_week",
    "session_effects", "goal_updates", "project_changes", or
    "session_changes". chronicle_meta: the week's primary-chronicle summary
    (parsed at run time, while the engine home still holds the files) —
    acceptance reads it from here, because the scratch copies may be gone
    by then. extra_artifacts: {name: text} written verbatim as
    artifacts/<name>.yaml and declared in the manifest (session_changes'
    knowledge/disclosure after-images ride this). manifest_extra: dict
    merged into the manifest (session_id, selected suggestions...).
    diff_extra: dict merged into the computed diff.json (knowledge/
    disclosure deltas the state diff cannot see). Returns the proposal id.
    Caller holds the campaign lock."""
    d = spine.campaign_dir(name)
    if not (d / "campaign.yaml").is_file():
        # mkdir(parents=True) below would happily mint a junk campaign tree
        # for a stale/unknown name — refuse instead
        raise ProposalError(f"unknown campaign: {name}")
    pid = proposal_id or new_id()
    if proposal_id:
        # a caller-supplied id (session_changes: the decision record embeds
        # it) must pass the same validation the reader applies
        _bundle(name, pid)  # raises ProposalError on an invalid id
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
    if chronicle_meta:
        manifest["chronicle"] = chronicle_meta
    for k, v in (manifest_extra or {}).items():
        manifest[k] = v
    for fname, text in sorted((extra_artifacts or {}).items()):
        (bdir / "artifacts" / f"{fname}.yaml").write_text(
            text, encoding="utf-8")
        manifest["artifacts"][fname] = f"artifacts/{fname}.yaml"
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
        spine.dump_yaml(entries), encoding="utf-8")
    manifest["artifacts"]["timeline_entries"] = \
        "artifacts/timeline-entries.yaml"
    updates = artifacts.get("updates") or []
    (bdir / "artifacts" / "goal-updates.yaml").write_text(
        spine.dump_yaml(updates), encoding="utf-8")
    manifest["artifacts"]["goal_updates"] = "artifacts/goal-updates.yaml"
    findings = artifacts.get("findings") or []
    (bdir / "artifacts" / "findings.yaml").write_text(
        spine.dump_yaml(findings), encoding="utf-8")
    manifest["artifacts"]["findings"] = "artifacts/findings.yaml"
    changes = artifacts.get("changes") or []
    (bdir / "artifacts" / "project-changes.yaml").write_text(
        spine.dump_yaml(changes), encoding="utf-8")
    manifest["artifacts"]["changes"] = "artifacts/project-changes.yaml"
    replay_text = artifacts.get("replay")
    if replay_text:
        (bdir / "artifacts" / "replay.yaml").write_text(
            replay_text, encoding="utf-8")
        manifest["artifacts"]["replay"] = "artifacts/replay.yaml"
    diff = diff_states(before_state,
                       yaml.safe_load(state_text) or {},
                       entries)
    if diff_extra:
        diff.update(diff_extra)
    (bdir / "artifacts" / "diff.json").write_text(
        json.dumps(diff, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["artifacts"]["diff"] = "artifacts/diff.json"
    (bdir / "proposal.yaml").write_text(spine.dump_yaml(manifest),
                                        encoding="utf-8")
    return pid


def set_status(name: str, proposal_id: str, status: str,
               decided_note: str = "") -> dict:
    """pending -> accepted|discarded. The bundle stays on disk as the audit
    receipt either way. Caller holds the campaign lock.

    A damaged (unreadable) manifest can only be discarded: accept refuses to
    apply bundle contents it could not read, while discard replaces the
    manifest with a minimal stub receipt — that is the documented exit for a
    wedged review slot (a damaged pending bundle blocks all new weeks)."""
    bdir = _bundle(name, proposal_id)
    manifest_path = bdir / "proposal.yaml"
    if not manifest_path.is_file():
        raise ProposalError(f"no such proposal: {proposal_id}")
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        manifest = None
    now = datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")
    if not isinstance(manifest, dict):
        if status != "discarded":
            raise ProposalError(
                f"proposal {proposal_id}'s manifest is damaged (unreadable "
                f"YAML) — refusing to {status} it blind; discard it instead")
        manifest = {
            "proposal_id": proposal_id,
            "status": "discarded",
            "decided": now,
            "decision_note": (decided_note or "discarded by the DM") +
                             " (the original manifest was unreadable; "
                             "replaced by this stub)",
        }
    else:
        if manifest.get("status") != "pending":
            raise ProposalError(
                f"proposal {proposal_id} is {manifest.get('status')!r}, "
                "not pending")
        manifest["status"] = status
        manifest["decided"] = now
        if decided_note:
            manifest["decision_note"] = decided_note
    spine.write_text_atomic(manifest_path, spine.dump_yaml(manifest))
    return manifest


# ------------------------------------------------------------------ reading


def _load_manifest(bdir: Path) -> dict | None:
    return spine.read_yaml_dict(bdir / "proposal.yaml")


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
        findings_path = arts / "findings.yaml"
        if findings_path.is_file():
            findings = yaml.safe_load(
                findings_path.read_text(encoding="utf-8"))
            out["findings"] = findings if isinstance(findings, list) else []
        else:
            out["findings"] = []
        changes_path = arts / "project-changes.yaml"
        if changes_path.is_file():
            changes = yaml.safe_load(
                changes_path.read_text(encoding="utf-8"))
            out["changes"] = changes if isinstance(changes, list) else []
        else:
            out["changes"] = []
        diff = json.loads((arts / "diff.json").read_text(encoding="utf-8"))
        out["diff"] = diff if isinstance(diff, dict) else {}
        # session_changes and future kinds declare additional after-images
        # (knowledge/disclosure/decision records) through the manifest; a
        # declared-but-unreadable one is the same damaged-bundle condition
        # as a damaged state — the except below names it
        out["extra"] = {}
        for key, rel in (manifest.get("artifacts") or {}).items():
            if key in ("state", "chronicles", "timeline_entries", "goal_updates",
                       "findings", "changes", "replay", "diff"):
                continue
            out["extra"][key] = yaml.safe_load(
                (bdir / rel).read_text(encoding="utf-8"))
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
        "projects_added": [],
        "projects_changed": {},
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
                 "source": e.get("source"), "effect": e.get("effect"),
                 # explicit event locations (place-centered-campaign-view.md):
                 # the place page's pending section joins on these — never on
                 # the actor's current place
                 "place_ids": [str(x) for x in (e.get("place_ids") or [])]})
    before_rumors = {r.get("claim") for r in (ev_b.get("rumors") or [])
                     if isinstance(r, dict)}
    for r in ((after or {}).get("rumors") or []):
        if isinstance(r, dict) and r.get("claim") not in before_rumors:
            diff["rumors_added"].append(
                {"claim": str(r.get("claim") or ""),
                 "fidelity": str(r.get("fidelity") or "")})
    # projects: additions and per-project field changes (progress movement,
    # status, terms) — what a work week, a sabotage, or a session effect moved
    bp = {p.get("id"): p for p in ((before or {}).get("projects") or [])
          if isinstance(p, dict)}
    ap = {p.get("id"): p for p in ((after or {}).get("projects") or [])
          if isinstance(p, dict)}
    for pid, p_after in ap.items():
        p_before = bp.get(pid)
        if p_before is None:
            diff["projects_added"].append(
                {"id": pid,
                 "faction_id": p_after.get("faction_id"),
                 "title": str(p_after.get("title") or ""),
                 "place_ids": [str(x) for x in (p_after.get("place_ids")
                                                or [])]})
            continue
        changed = {k: {"before": p_before.get(k), "after": p_after.get(k)}
                   for k in ("progress", "target", "status", "title",
                             "objective", "visibility")
                   if p_before.get(k) != p_after.get(k)}
        comp_b = (p_before.get("completion") or {}).get("text") or ""
        comp_a = (p_after.get("completion") or {}).get("text") or ""
        if comp_b != comp_a:
            changed["completion_text"] = {"before": comp_b, "after": comp_a}
        blocked_b = p_before.get("completion_blocked")
        blocked_a = p_after.get("completion_blocked")
        if blocked_b != blocked_a:
            changed["completion_blocked"] = {"before": blocked_b,
                                             "after": blocked_a}
        if changed:
            changed["faction_id"] = p_after.get("faction_id")
            changed["title"] = str(p_after.get("title") or "")
            diff["projects_changed"][pid] = changed
    diff["timeline_entries"] = len(entries or [])
    return diff
