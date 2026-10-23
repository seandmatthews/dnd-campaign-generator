"""Faction projects: concrete undertakings with persistent progress.

A goal is an aspiration ("dominate the river trade"); a project is a
concrete undertaking with a counter ("buy the ferry concession, 2 of 4").
The one-sentence mechanic:

    a successful work action advances its project one step, a successful
    sabotage action removes one step, and reaching the target completes
    the project and applies its preapproved effects once.

No progress rolls, no variable speed, no spending, no dependencies — the
dice that decide the ACTION decide the project, through the existing
resolver. This module owns the pure mechanics; tick.py calls apply_week
after a week's ordinary consequences, effects.py routes the session
effects (set_project_progress / set_project_status) through the same
completion pass, and the studio drives DM edits through the CLI below
(never by importing across projects).

Week application order (the whole contract, in one place):
  1. tick snapshots project state and validates project actions against
     it BEFORE resolution (llm.propose validated against the view first;
     this is the state-truth backstop — an invalid project action is
     refused, never silently resolved as an ordinary one)
  2. the existing seeded resolver resolves everything
  3. one consolidated outcome per actor (worst margin; a tied loss
     defeats a tied success) — inactive defensive outcomes are not actions
  4. ordinary resource consequences once per actor (tick.apply_outcomes)
  5. project deltas: successful work +1, successful sabotage -1, failure
     0 — aggregated per project, then clamped once to [0, target]
  6. completion from final counters — work and sabotage that cancel at
     the threshold do not complete; proposal ordering cannot change this
  7. completion effects validated against the post-resource state;
     simultaneous completions are evaluated against ONE shared snapshot
     and overlapping writes to the same faction field/resource block the
     conflicting completions (all of them), never by iteration order.

A blocked completion keeps progress at target, status active, and a
`completion_blocked` finding on the record; every later tick rechecks it
deterministically — no LLM call, no new action — while sabotage can pull
the counter back down. Completion executes exactly once via the existing
applied-effect deduplication (a synthetic `project-complete:<id>` guard
covers the event itself).

Standalone CLI (the same validators weekly execution and session effects
use):

  python src/projects.py --report  [--state PATH] [--out FILE]
                                   # the DM list + computed findings
  python src/projects.py --check changes.yaml [--state PATH]
  python src/projects.py --preview changes.yaml [--state PATH] [--out FILE]
  python src/projects.py --apply changes.yaml [--state PATH]

changes.yaml carries typed operations (create/edit/pause/resume/abandon);
--apply is explicit standalone canon mutation, --preview writes the
normalized operations, findings, and the candidate state. --apply against
the shared engine home claims the home's cross-process lock around the
whole read-modify-write (store.standalone_home_lock — ticket 56), like
tick.py --live and ingest.py --apply.

Exit codes: 0 ok, 1 validation or IO error, 2 usage, 4 the shared engine
home is held by another process.
"""

import argparse
import copy
import pathlib
import re
import sys
import uuid

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import resolve
import store as _store
from store import STATE_FILE, HomeLockBusy, standalone_home_lock
from store import load_state as _store_load
from store import save_state as _store_save

RULE_VERSION = 1          # the project mechanics version (replay trails)
SCHEMA_VERSION = 1        # the per-project record version
STATUSES = ("active", "paused", "completed", "abandoned")
TERMINAL_STATUSES = ("completed", "abandoned")
RESUMABLE_STATUSES = ("paused",)
VISIBILITIES = ("public", "restricted")
INTENTS = ("ordinary", "work_project", "sabotage_project")
LEVELS = _store.RESOURCE_LEVELS
_LEVEL_INDEX = _store.LEVEL_INDEX
RESOURCE_KEYS = _store.RESOURCE_KEYS
DEFAULT_TARGET = 4
TARGET_MIN, TARGET_MAX = 2, 8
EFFECT_TYPES = ("set_resource", "set_field")
COMPLETION_EFFECT_CAP = 4
TITLE_MAX = 120
OBJECTIVE_MAX = 300
OPERATION_KINDS = ("create", "edit", "pause", "resume", "abandon")
EDITABLE_FIELDS = ("title", "objective", "target", "place_ids",
                   "visibility", "known_to", "requirements", "completion")

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _one_line(text, cap):
    """One readable line that survives the state file, prompts, and JSON."""
    return re.sub(r"\s+", " ", _CTRL.sub(" ", str(text or "").strip()))[:cap]


def projects_of(state):
    """The state's project records (missing `projects` = an empty list —
    old campaigns keep their exact old behavior)."""
    raw = (state or {}).get("projects")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict)]


def find_project(state, project_id):
    for p in projects_of(state):
        if str(p.get("id") or "") == str(project_id or ""):
            return p
    return None


def _faction_ids(state):
    return {str(f.get("id")) for f in (state or {}).get("factions", [])
            if isinstance(f, dict) and f.get("id")}


def _place_ids(state):
    return {str(p.get("id")) for p in _store.places_of(state or {})
            if p.get("id")}


# ------------------------------------------------------------- normalization


def normalize_project(raw, *, week, state, id_factory=None):
    """One authoring record -> (normalized project, findings). Fills the
    defaults, mints the id and the completion-effect slot ids ONCE (ids are
    never derived again from a renamed title), and validates content.
    Findings empty means the record is good to persist."""
    findings = []
    if not isinstance(raw, dict):
        return None, ["project is not a mapping"]
    faction_ids = _faction_ids(state)
    place_ids = _place_ids(state)
    owner = str(raw.get("faction_id") or "").strip()
    if owner not in faction_ids:
        findings.append(f"unknown faction_id {owner!r}")
        owner = owner or "?"
    title = _one_line(raw.get("title"), TITLE_MAX)
    if not title:
        findings.append("missing title")
    objective = _one_line(raw.get("objective"), OBJECTIVE_MAX)
    if not objective:
        findings.append("missing objective")
    target = raw.get("target", DEFAULT_TARGET)
    if not isinstance(target, int) or isinstance(target, bool) \
            or not (TARGET_MIN <= target <= TARGET_MAX):
        findings.append(
            f"target {target!r} is not an integer in "
            f"{TARGET_MIN}-{TARGET_MAX}")
        target = DEFAULT_TARGET
    places = raw.get("place_ids") or []
    if not isinstance(places, list):
        findings.append("place_ids is not a list")
        places = []
    places = [str(x).strip() for x in places if str(x).strip()]
    for pid in places:
        if pid not in place_ids:
            findings.append(f"unknown place_id {pid!r}")
    visibility = str(raw.get("visibility") or "restricted").strip()
    if visibility not in VISIBILITIES:
        findings.append(
            f"visibility {visibility!r} is not one of "
            f"{', '.join(VISIBILITIES)} (default: restricted)")
        visibility = "restricted"
    known_to = raw.get("known_to") or []
    if not isinstance(known_to, list):
        findings.append("known_to is not a list")
        known_to = []
    known_to = [str(x).strip() for x in known_to if str(x).strip()]
    for fid in known_to:
        if fid not in faction_ids:
            findings.append(f"known_to names unknown faction {fid!r}")
    known_to = [fid for fid in known_to if fid != owner]  # the owner knows
    requirements, req_findings = _normalize_requirements(
        raw.get("requirements"))
    findings.extend(req_findings)
    completion, comp_findings = _normalize_completion(
        raw.get("completion"), owner, state)
    findings.extend(comp_findings)
    created = raw.get("created_week", week)
    if not isinstance(created, int) or isinstance(created, bool):
        findings.append(f"created_week {created!r} is not an integer")
        created = week
    status = str(raw.get("status") or "active").strip()
    if status not in STATUSES:
        findings.append(f"status {status!r} is not one of "
                        f"{', '.join(STATUSES)}")
        status = "active"
    progress = raw.get("progress", 0)
    if not isinstance(progress, int) or isinstance(progress, bool) \
            or not (0 <= progress <= target):
        findings.append(f"progress {progress!r} is not an integer in "
                        f"0..{target}")
        progress = 0
    if findings:
        return None, findings
    pid = str(raw.get("id") or "").strip()
    if not pid:
        stem = "-".join(_WORD_RE.findall(title.lower()))[:16].rstrip("-")
        pid = f"project-{stem or 'untitled'}-{(id_factory or _new_id)()}"
    return {
        "id": pid,
        "schema_version": SCHEMA_VERSION,
        "faction_id": owner,
        "title": title,
        "objective": objective,
        "place_ids": places,
        "status": status,
        "progress": progress,
        "target": target,
        "visibility": visibility,
        "known_to": known_to,
        "requirements": requirements,
        "completion": completion,
        "created_week": created,
        "completed_week": raw.get("completed_week"),
    }, []


def _new_id():
    return uuid.uuid4().hex[:4]


def _normalize_requirements(raw):
    """Minimum eligibility grades, not a consumable balance: `coin: low`
    means work cannot progress while the owner is at coin none. Nothing is
    deducted by a requirement."""
    if raw in (None, {}):
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["requirements is not a mapping"]
    out, findings = {}, []
    for k, v in raw.items():
        key = str(k).strip()
        grade = str(v).strip()
        if key not in RESOURCE_KEYS:
            findings.append(f"requirements: unknown resource {key!r} "
                            f"(use one of {', '.join(RESOURCE_KEYS)})")
            continue
        if grade not in _LEVEL_INDEX:
            findings.append(f"requirements: {key}: {grade!r} is not a "
                            f"resource grade (use one of {', '.join(LEVELS)})")
            continue
        out[key] = grade
    return out, findings


def _normalize_completion(raw, owner, state):
    """The preapproved completion: descriptive text plus at most a few
    typed effects for the OWNER only (set_resource / set_field — no
    transfers, no arbitrary paths, no recursive projects). Each effect
    gets a stable slot id in the persisted record, even if the authoring
    input omitted one."""
    if raw in (None, {}):
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["completion is not a mapping"]
    findings = []
    text = _one_line(raw.get("text"), OBJECTIVE_MAX)
    if not text:
        findings.append("completion.text is missing (what did this change, "
                        "in the world's own words?)")
    effects_raw = raw.get("effects") or []
    if not isinstance(effects_raw, list):
        findings.append("completion.effects is not a list")
        effects_raw = []
    if len(effects_raw) > COMPLETION_EFFECT_CAP:
        findings.append(f"completion carries {len(effects_raw)} effects — "
                        f"the cap is {COMPLETION_EFFECT_CAP}")
    import effects as _effects
    effects_out = []
    for i, eff in enumerate(effects_raw[:COMPLETION_EFFECT_CAP]):
        if not isinstance(eff, dict):
            findings.append(f"completion effect {i + 1}: not a mapping")
            continue
        etype = str(eff.get("type") or "").strip()
        if etype not in EFFECT_TYPES:
            findings.append(
                f"completion effect {i + 1}: type {etype!r} is not one of "
                f"{', '.join(EFFECT_TYPES)}")
            continue
        eff_owner = str(eff.get("faction_id") or "").strip()
        if eff_owner != owner:
            findings.append(
                f"completion effect {i + 1}: faction_id {eff_owner!r} is "
                f"not the project's owner — completion effects touch the "
                f"owner only")
            continue
        rec = {"slot": str(eff.get("slot") or "").strip() or _new_id(),
               "type": etype, "faction_id": eff_owner}
        if etype == "set_resource":
            resource = str(eff.get("resource") or "").strip()
            after = str(eff.get("after") or "").strip()
            if resource not in _effects.RESOURCE_KEYS:
                findings.append(
                    f"completion effect {i + 1}: unknown resource "
                    f"{resource!r}")
            if after not in _effects.RESOURCE_LEVELS:
                findings.append(
                    f"completion effect {i + 1}: after {after!r} is not a "
                    f"resource grade")
            rec.update({"resource": resource, "after": after})
        else:
            field = str(eff.get("field") or "").strip()
            after = str(eff.get("after") or "").strip()
            if field not in _effects.FIELD_KEYS:
                findings.append(
                    f"completion effect {i + 1}: unknown field {field!r} "
                    f"(use one of {', '.join(_effects.FIELD_KEYS)})")
            if not after:
                findings.append(f"completion effect {i + 1}: empty after")
            rec.update({"field": field, "after": after})
        expected = eff.get("expected_before")
        if expected not in (None, ""):
            rec["expected_before"] = str(expected)
        reason = _one_line(eff.get("reason"), OBJECTIVE_MAX)
        if not reason:
            findings.append(f"completion effect {i + 1}: missing reason")
        rec["reason"] = reason
        effects_out.append(rec)
    if findings:
        return {}, findings
    return {"text": text, "effects": effects_out}, []


def find_state_issues(state):
    """Every reason the persisted projects list cannot spend a week against
    — the same checks validate_state does for the rest of the state, run
    BEFORE any LLM call. Returns findings; empty means clean."""
    raw = (state or {}).get("projects")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return ["projects is not a list"]
    findings = []
    faction_ids = _faction_ids(state)
    place_ids = _place_ids(state)
    seen = set()
    active_per_owner = {}
    for i, p in enumerate(raw):
        label = f"project {i + 1}"
        if not isinstance(p, dict):
            findings.append(f"{label}: not a mapping")
            continue
        pid = str(p.get("id") or "").strip()
        if not pid:
            findings.append(f"{label}: missing id")
        elif pid in seen:
            findings.append(f"{label}: duplicate id {pid!r}")
        else:
            seen.add(pid)
            label = f"project {pid!r}"
        owner = str(p.get("faction_id") or "").strip()
        if owner not in faction_ids:
            findings.append(f"{label}: unknown faction_id {owner!r}")
        for pid2 in (p.get("place_ids") or []):
            if str(pid2) not in place_ids:
                findings.append(f"{label}: unknown place_id {pid2!r}")
        status = p.get("status", "active")
        if status not in STATUSES:
            findings.append(f"{label}: status {status!r} invalid")
        target = p.get("target", DEFAULT_TARGET)
        if not isinstance(target, int) or isinstance(target, bool) \
                or not (TARGET_MIN <= target <= TARGET_MAX):
            findings.append(f"{label}: target {target!r} outside "
                            f"{TARGET_MIN}-{TARGET_MAX}")
            target = DEFAULT_TARGET
        progress = p.get("progress", 0)
        if not isinstance(progress, int) or isinstance(progress, bool) \
                or not (0 <= progress <= target):
            findings.append(f"{label}: progress {progress!r} outside "
                            f"0..{target}")
        visibility = p.get("visibility", "restricted")
        if visibility not in VISIBILITIES:
            findings.append(f"{label}: visibility {visibility!r} invalid")
        for fid in (p.get("known_to") or []):
            if str(fid) not in faction_ids:
                findings.append(f"{label}: known_to names unknown faction "
                                f"{fid!r}")
        _, req_findings = _normalize_requirements(p.get("requirements"))
        findings.extend(f"{label}: {f}" for f in req_findings)
        _, comp_findings = _normalize_completion(p.get("completion"), owner,
                                                 state)
        findings.extend(f"{label}: {f}" for f in comp_findings)
        if status == "active":
            active_per_owner.setdefault(owner, 0)
            active_per_owner[owner] += 1
    for owner, count in sorted(active_per_owner.items()):
        if count > 1:
            findings.append(f"{owner} holds {count} active projects — one "
                            "active project per faction")
    return findings


# ----------------------------------------------------------------- visibility


def visible_to(project, faction_id):
    """Owners see their own project in full; others only when it is public
    or explicitly addressed to them through known_to."""
    if str(project.get("faction_id")) == str(faction_id):
        return True
    if project.get("visibility") == "public":
        return True
    return str(faction_id) in [str(x) for x in (project.get("known_to") or [])]


def coarse_progress_label(progress, target):
    """The outsider's progress label — exact counters are DM/owner
    information in version one."""
    if target <= 0:
        return "starting"
    ratio = progress / target
    if ratio <= 1 / 3:
        return "starting"
    if progress >= target - 1:
        return "near completion"
    return "underway"


def outsider_view(project, place_names=None):
    """What a rival faction's view may carry: identity, not counters —
    never requirements, never completion effects."""
    names = place_names or {}
    return {
        "id": project.get("id"),
        "faction_id": project.get("faction_id"),
        "title": project.get("title"),
        "status": project.get("status", "active"),
        "places": [names.get(pid, pid)
                   for pid in (project.get("place_ids") or [])],
        "progress": coarse_progress_label(
            project.get("progress", 0), project.get("target", DEFAULT_TARGET)),
    }


def owner_view(project, faction=None):
    """The owner's own project in full detail — still minus the completion
    EFFECTS (mechanics for the DM; the descriptive completion.text is what
    the owner knows it is working toward)."""
    ok, missing = requirements_state(project, faction)
    out = {
        "id": project.get("id"),
        "title": project.get("title"),
        "objective": project.get("objective"),
        "status": project.get("status", "active"),
        "progress": project.get("progress", 0),
        "target": project.get("target", DEFAULT_TARGET),
        "requirements": project.get("requirements") or {},
        "requirements_met": ok,
        "place_ids": list(project.get("place_ids") or []),
        "completion_text": (project.get("completion") or {}).get("text", ""),
        "created_week": project.get("created_week"),
    }
    if not ok:
        out["requirements_unmet"] = missing
    return out


# --------------------------------------------------------------- requirements


def requirements_state(project, faction=None):
    """(met, missing): each requirement is a minimum grade on the owner's
    CURRENT resources — `coin: low` fails only at coin none. No resource is
    deducted by the requirement itself."""
    faction = faction or {}
    resources = faction.get("resources") or {}
    missing = []
    for key, grade in sorted((project.get("requirements") or {}).items()):
        have = _LEVEL_INDEX.get(str(resources.get(key, "none")), 0)
        need = _LEVEL_INDEX.get(str(grade), 0)
        if have < need:
            missing.append(f"{key} {resources.get(key, 'none')} below the "
                           f"required {grade}")
    return (not missing), missing


def activation_overrides(state):
    """Faction ids whose eligible active project lets them act even when the
    ordinary quiet-week gate would hold them. A project with unmet
    requirements, or one sitting at target awaiting its completion
    correction, creates NO override — there is nothing to work toward."""
    factions = _store.faction_map(state)
    out = {}
    for project in projects_of(state):
        if project.get("status") != "active":
            continue
        owner = str(project.get("faction_id") or "")
        faction = factions.get(owner)
        if faction is None:
            continue
        if project.get("progress", 0) >= project.get("target", DEFAULT_TARGET):
            continue  # awaiting completion, not work
        ok, _ = requirements_state(project, faction)
        if ok:
            out[owner] = project.get("id")
    return out


# ------------------------------------------------------------------- actions


def validate_action(proposal, state, actor_id):
    """The state-truth check of one proposal's project intent, against the
    pre-week snapshot. Returns (updates, findings): updates are applied to
    the normalized proposal (sabotage's resolver target becomes the
    project's owner — a model cannot mark a targeted rival project as
    `self` to obtain an uncontested roll); findings non-empty means the
    action is REFUSED, never silently resolved as an ordinary one."""
    intent = str((proposal or {}).get("intent") or "ordinary").strip()
    if intent not in INTENTS:
        return {}, [f"unknown intent {intent!r}"]
    if intent == "ordinary":
        return {}, []
    pid = str(proposal.get("project_id") or "").strip()
    project = find_project(state, pid) if pid else None
    if project is None:
        return {}, [f"unknown project_id {pid!r}"]
    if project.get("status") != "active":
        return {}, [(f"project {pid!r} is {project.get('status')!r}, "
                     "not active")]
    if intent == "work_project":
        if str(project.get("faction_id")) != str(actor_id):
            return {}, [(f"work targets your own project — {pid!r} belongs "
                         f"to {project.get('faction_id')!r}")]
        faction = next((f for f in state.get("factions", [])
                        if f.get("id") == actor_id), None)
        ok, missing = requirements_state(project, faction or {})
        if not ok:
            return {}, ["requirements unmet: " + "; ".join(missing)
                        + " — work cannot progress this week"]
        return {"target": "self"}, []
    # sabotage: another faction's VISIBLE active project
    if str(project.get("faction_id")) == str(actor_id):
        return {}, [(f"project {pid!r} is your own — work it with "
                     f"intent: work_project, do not sabotage it")]
    if not visible_to(project, actor_id):
        return {}, [f"project {pid!r} is not visible to {actor_id!r}"]
    return {"target": project.get("faction_id")}, []


# ------------------------------------------------------- weekly application


def week_deltas(proposals, outcomes):
    """Project deltas from the week's consolidated outcomes: successful
    work +1, successful sabotage -1, failure 0. One consolidated outcome
    per actor (resolve.consolidate_outcomes) — an actor in several contests
    moves a project at most one step, and any loss defeats a success at the
    same worst margin. Proposal ordering cannot change the result."""
    consolidated = resolve.consolidate_outcomes(outcomes, proposals)
    deltas = {}
    for p in proposals or []:
        intent = str(p.get("intent") or "ordinary")
        if intent not in ("work_project", "sabotage_project"):
            continue
        pid = str(p.get("project_id") or "")
        outcome = consolidated.get(p.get("actor"))
        if outcome is None or outcome.get("defensive"):
            continue  # a defense is not an action
        if not pid:
            continue
        step = 0
        if outcome["success"]:
            step = 1 if intent == "work_project" else -1
        entry = deltas.setdefault(pid, {"delta": 0, "actions": []})
        entry["delta"] += step
        entry["actions"].append({
            "actor": p.get("actor"),
            "action_id": p.get("action_id"),
            "intent": intent,
            "success": bool(outcome["success"]),
            "margin": outcome["margin"],
        })
    return deltas


def apply_week(state, proposals, outcomes):
    """Steps 5-7 of the week contract (module docstring), mutating state in
    memory: clamp the aggregated deltas once, annotate this week's project
    events with the movement, then run the completion pass. Returns the
    report the replay trail and chronicle carry."""
    week = state["meta"]["clock"]["week"]
    report = {"week": week, "deltas": {}, "completions": [], "blocked": [],
              "findings": []}
    by_id = {str(p.get("id")): p for p in projects_of(state) if p.get("id")}
    deltas = week_deltas(proposals, outcomes)
    for pid in sorted(deltas):
        project = by_id.get(pid)
        if project is None or project.get("status") != "active":
            report["findings"].append(
                f"{pid}: a project action named a missing or inactive "
                "project — no progress (the proposal should have been "
                "refused before resolution)")
            continue
        before = project.get("progress", 0)
        after = max(0, min(int(project.get("target", DEFAULT_TARGET)),
                           before + deltas[pid]["delta"]))
        project["progress"] = after
        report["deltas"][pid] = {
            "title": project.get("title"),
            "faction_id": project.get("faction_id"),
            "before": before, "after": after,
            "delta": after - before,
        }
        wanted = {a.get("action_id") for a in deltas[pid]["actions"]
                  if a.get("action_id")}
        for ev in state.get("events", []):
            if ev.get("week") == week and ev.get("action_id") in wanted:
                ev["project_id"] = pid
                ev["progress_before"] = before
                ev["progress_after"] = after
                ev["place_ids"] = list(project.get("place_ids") or [])
    completions, blocked, findings = completion_pass(state, week)
    report["completions"] = completions
    report["blocked"] = blocked
    report["findings"].extend(findings)
    return report


def completion_pass(state, week):
    """The one completion algorithm: every ACTIVE project sitting at target
    (newly reached, or blocked on an earlier tick) has its preapproved
    effects validated against the CURRENT post-resource state; valid ones
    complete exactly once, overlapping ones block together. Returns
    (completed_ids, blocked_records, findings). Deterministic, no LLM, no
    new action — this is what makes a later-tick recheck free."""
    completions, blocked, findings = [], [], []
    candidates = []
    applied = set(state.get("meta", {}).get("applied_effects") or [])
    for project in projects_of(state):
        pid = str(project.get("id") or "")
        if project.get("status") != "active":
            _clear_blocked(project)
            continue
        if project.get("progress", 0) < project.get("target", DEFAULT_TARGET):
            _clear_blocked(project)
            continue
        guard = f"project-complete:{pid}"
        if guard in applied:
            findings.append(
                f"{pid}: already completed once (dedup guard) — status "
                "left as it stands")
            continue
        candidates.append(project)
    if not candidates:
        return completions, blocked, findings

    import effects as _effects
    # validate each candidate's effects against the ONE shared post-resource
    # snapshot — a stale precondition fails its whole project, nothing lands
    valid = {}
    for project in candidates:
        pid = str(project.get("id"))
        effects_in = [
            {"id": f"{pid}:{eff.get('slot') or 'x'}", **eff}
            for eff in (project.get("completion") or {}).get("effects", [])
            if isinstance(eff, dict)
        ]
        errs = []
        for eff in effects_in:
            errs.extend(f"{pid}: {e}" for e in
                        _effects.validate_effect(eff, state))
        if errs:
            blocked.append({"project_id": pid, "finding": "; ".join(errs)})
            findings.extend(errs)
            project["completion_blocked"] = "; ".join(errs)
        else:
            valid[pid] = (project, effects_in)
    # overlapping writes to the same faction field/resource block ALL the
    # conflicting completions, never by iteration order
    writes = {}
    for pid, (project, effects_in) in valid.items():
        keys = {(eff.get("faction_id"),
                 "resource" if eff["type"] == "set_resource" else "field",
                 eff.get("resource") or eff.get("field"))
                for eff in effects_in}
        for key in keys:
            writes.setdefault(key, []).append(pid)
    conflicted = {pid for pids in writes.values() if len(pids) > 1
                  for pid in pids}
    for pid in sorted(conflicted):
        project, _ = valid[pid]
        finding = (f"{pid}: its completion writes overlap another "
                   "completing project's write to the same faction "
                   "field/resource — both stay open at target")
        blocked.append({"project_id": pid, "finding": finding})
        findings.append(finding)
        project["completion_blocked"] = finding
        del valid[pid]
    # apply the survivors, one project at a time (each project's effects
    # fail together or apply together)
    for pid in sorted(valid):
        project, effects_in = valid[pid]
        applied_n, errs = _effects.apply_effects(state, effects_in, week=week)
        if errs:  # unreachable barring a validation gap — refuse, don't halve
            finding = f"{pid}: completion refused at apply time: " \
                      + "; ".join(errs)
            blocked.append({"project_id": pid, "finding": finding})
            findings.extend(errs)
            project["completion_blocked"] = finding
            continue
        project["status"] = "completed"
        project["completed_week"] = week
        _clear_blocked(project)
        state.setdefault("meta", {}).setdefault("applied_effects", []) \
            .append(f"project-complete:{pid}")
        completions.append(pid)
        _emit_completion_event(state, project, week, applied_n)
    return completions, blocked, findings


def _clear_blocked(project):
    project.pop("completion_blocked", None)


def _emit_completion_event(state, project, week, applied_n):
    """Exactly one completion event per project (the synthetic guard in
    meta.applied_effects is checked by the caller): the DM's chronicle may
    name the project; the rumor mill quotes public_action only, so a
    restricted completion never announces itself."""
    pid = project.get("id")
    owner = project.get("faction_id")
    text = (project.get("completion") or {}).get("text") \
        or "the undertaking is done"
    public = (f"{owner} saw a private undertaking through to its end"
              if project.get("visibility") != "public"
              else f"{project.get('title')} — completed")
    count = sum(1 for ev in state.get("events", [])
                if ev.get("week") == week and ev.get("actor") == owner)
    state.setdefault("events", []).append({
        "event_id": f"ev-{week:04d}-{owner}-c{count + 1}-{pid}",
        "week": week,
        "actor": owner,
        "project_id": pid,
        "action": f"Complete {project.get('title')}",
        "public_action": public,
        "text": f"{project.get('title')} — completed: {text} "
                f"({applied_n} effect(s) applied)",
        "outcome": "succeeded",
        "contested": False,
        "margin": 0,
    })


def report_lines(report):
    """Human-readable movement lines for the chronicle and the run log."""
    lines = []
    for pid in sorted(report.get("deltas") or {}):
        d = report["deltas"][pid]
        lines.append(f"{d.get('faction_id')}: {d.get('title')} — progress "
                     f"{d['before']} -> {d['after']}"
                     + (f" ({d['delta']:+d})" if d["delta"]
                        else " (no change)"))
    for pid in report.get("completions") or []:
        lines.append(f"{pid} — COMPLETED this week")
    for b in report.get("blocked") or []:
        lines.append(f"{b.get('project_id')} — completion blocked: "
                     f"{b.get('finding')}")
    return lines


# ------------------------------------------------------------ DM operations


def apply_operations(state, operations, week=None, id_factory=None):
    """Typed DM operations (create/edit/pause/resume/abandon) against an
    in-memory state, in order. Returns (normalized_operations, findings) —
    findings non-empty means the batch is REFUSED whole (the caller never
    persists a partial edit). No LLM anywhere."""
    week = week if week is not None \
        else (state or {}).get("meta", {}).get("clock", {}).get("week", 0)
    norm_ops, findings = [], []
    for i, op in enumerate(operations or []):
        oid = str((op or {}).get("id") or f"op-{i + 1}")
        if not isinstance(op, dict):
            findings.append(f"operation {oid}: not a mapping")
            continue
        kind = str(op.get("op") or "").strip()
        reason = _one_line(op.get("reason"), OBJECTIVE_MAX)
        if not reason:
            findings.append(f"operation {oid}: missing reason (the DM must "
                            "see what justifies this)")
            continue
        if kind == "create":
            _op_create(state, op, oid, reason, week, id_factory,
                       norm_ops, findings)
        elif kind in ("pause", "resume", "abandon"):
            _op_status(state, op, oid, kind, reason, norm_ops, findings)
        elif kind == "edit":
            _op_edit(state, op, oid, reason, norm_ops, findings)
        else:
            findings.append(f"operation {oid}: unknown op {kind!r} (use one "
                            f"of {', '.join(OPERATION_KINDS)})")
    return norm_ops, findings


def one_active_clause(state, owner, ignore_pid=None):
    """The owner's other ACTIVE project, if any — the one-active-per-faction
    rule shared by DM operations and session effects."""
    for p in projects_of(state):
        if p.get("status") == "active" and p.get("faction_id") == owner \
                and p.get("id") != ignore_pid:
            return p
    return None


def _op_create(state, op, oid, reason, week, id_factory, norm_ops, findings):
    record, errs = normalize_project(op.get("project"), week=week, state=state,
                                     id_factory=id_factory)
    if errs:
        findings.extend(f"operation {oid}: {e}" for e in errs)
        return
    clash = one_active_clause(state, record["faction_id"])
    if clash is not None:
        findings.append(
            f"operation {oid}: {record['faction_id']} already runs the "
            f"active project {clash.get('id')!r} — one active project per "
            "faction; pause or abandon it first")
        return
    state.setdefault("projects", []).append(record)
    norm_ops.append({"id": oid, "op": "create", "reason": reason,
                     "project_id": record["id"], "project": record})


def _op_status(state, op, oid, kind, reason, norm_ops, findings):
    pid = str(op.get("project_id") or "").strip()
    project = find_project(state, pid)
    if project is None:
        findings.append(f"operation {oid}: unknown project_id {pid!r}")
        return
    current = project.get("status", "active")
    if kind == "pause":
        if current != "active":
            findings.append(f"operation {oid}: {pid!r} is {current!r} — "
                            "only an active project can pause")
            return
        project["status"] = "paused"  # progress is preserved
    elif kind == "resume":
        if current not in RESUMABLE_STATUSES:
            findings.append(f"operation {oid}: {pid!r} is {current!r} — "
                            "only a paused project can resume")
            return
        clash = one_active_clause(state, project.get("faction_id"),
                                   ignore_pid=pid)
        if clash is not None:
            findings.append(
                f"operation {oid}: {project.get('faction_id')} already runs "
                f"the active project {clash.get('id')!r}")
            return
        project["status"] = "active"
    else:  # abandon
        if current in TERMINAL_STATUSES:
            findings.append(f"operation {oid}: {pid!r} is already "
                            f"{current!r}")
            return
        project["status"] = "abandoned"
    norm_ops.append({"id": oid, "op": kind, "project_id": pid,
                     "reason": reason, "from_status": current,
                     "to_status": project["status"]})


def _op_edit(state, op, oid, reason, norm_ops, findings):
    pid = str(op.get("project_id") or "").strip()
    project = find_project(state, pid)
    if project is None:
        findings.append(f"operation {oid}: unknown project_id {pid!r}")
        return
    if project.get("status") in TERMINAL_STATUSES:
        findings.append(
            f"operation {oid}: {pid!r} is {project['status']} — version one "
            "refuses terminal mutation")
        return
    changes = op.get("set")
    if not isinstance(changes, dict) or not changes:
        findings.append(f"operation {oid}: edit needs a non-empty `set` "
                        "mapping")
        return
    unknown = [k for k in changes if k not in EDITABLE_FIELDS]
    if unknown:
        findings.append(f"operation {oid}: unknown field(s) "
                        f"{', '.join(sorted(unknown))} — editable: "
                        f"{', '.join(EDITABLE_FIELDS)}")
        return
    # a target below the live progress is a rules change, not an edit —
    # refuse before normalization clamps the counter into range
    new_target = changes.get("target")
    if isinstance(new_target, int) and not isinstance(new_target, bool) \
            and project.get("progress", 0) > new_target:
        findings.append(
            f"operation {oid}: target {new_target} is below the project's "
            f"progress {project.get('progress', 0)} — raise the target or "
            "abandon the project")
        return
    draft = copy.deepcopy(project)
    draft.update(copy.deepcopy(changes))
    normalized, errs = normalize_project(draft,
                                         week=draft.get("created_week", 0),
                                         state=state)
    if errs:
        findings.extend(f"operation {oid}: {e}" for e in errs)
        return
    before = {k: project.get(k) for k in EDITABLE_FIELDS}
    for k in EDITABLE_FIELDS:
        project[k] = normalized[k]
    after = {k: project.get(k) for k in EDITABLE_FIELDS}
    project.pop("completion_blocked", None)  # terms changed: recheck cleanly
    norm_ops.append({"id": oid, "op": "edit", "project_id": pid,
                     "reason": reason, "before": before, "after": after})


# ------------------------------------------------------------------ reporting


def report(state):
    """The DM view behind GET /simulation/projects: every project
    normalized, with computed requirement and completion findings."""
    factions = _store.faction_map(state)
    out = []
    for project in projects_of(state):
        rec = copy.deepcopy(project)
        rec.setdefault("status", "active")
        rec.setdefault("progress", 0)
        rec.setdefault("target", DEFAULT_TARGET)
        owner = factions.get(str(rec.get("faction_id")))
        ok, missing = requirements_state(rec, owner or {})
        rec["requirements_met"] = ok
        if not ok:
            rec["requirements_unmet"] = missing
        rec["progress_label"] = coarse_progress_label(
            rec["progress"], rec["target"])
        out.append(rec)
    return {
        "week": (state or {}).get("meta", {}).get("clock", {}).get("week"),
        "factions": sorted(factions),
        "projects": out,
    }


# ----------------------------------------------------------------------- CLI


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="projects.py",
        description="Faction projects: report, validate, preview, or apply "
                    "typed DM operations.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--report", action="store_true",
                      help="print the DM project list + computed findings")
    mode.add_argument("--check", metavar="CHANGES",
                      help="validate operations against the state")
    mode.add_argument("--preview", metavar="CHANGES",
                      help="normalized operations + findings + candidate "
                           "state, written to --out")
    mode.add_argument("--apply", metavar="CHANGES",
                      help="explicit standalone canon mutation")
    parser.add_argument("--state", default=None,
                        help="state.yaml (default: the engine home's)")
    parser.add_argument("--out", default=None,
                        help="output file for --report/--preview")
    args = parser.parse_args(argv)
    if args.report:
        return _cmd_report(args)
    return _cmd_changes(args)


def _load(path):
    try:
        return _store_load(path), None
    except (OSError, yaml.YAMLError) as e:
        return None, str(e)


def _cmd_report(args):
    state, err = _load(args.state)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    doc = report(state)
    findings = find_state_issues(state)
    if findings:
        doc["findings"] = findings
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_changes(args):
    mode = "--check" if args.check else "--preview" if args.preview \
        else "--apply"
    changes_path = args.check or args.preview or args.apply
    # --apply against the SHARED engine home (the default --state) claims
    # the home's cross-process lock (ticket 56) around the WHOLE
    # read-modify-write — load, apply, save — matching tick.py --live and
    # ingest.py --apply: a load outside the lock reads pre-week state,
    # applies, saves, and the week's save then overwrites it (or the
    # reverse, leaving a chronicle for a week the state never recorded).
    # --check/--preview write nothing (an --out target is the caller's
    # own file); an explicit non-home --state is likewise the caller's
    # own — neither claims, neither refuses.
    if mode == "--apply":
        home = args.state is None or \
            pathlib.Path(args.state).resolve() == \
            pathlib.Path(STATE_FILE).resolve()
        if home:
            try:
                with standalone_home_lock():
                    return _cmd_changes_locked(args, mode, changes_path)
            except HomeLockBusy as e:
                print(f"refused: {e}", file=sys.stderr)
                return 4
    return _cmd_changes_locked(args, mode, changes_path)


def _cmd_changes_locked(args, mode, changes_path):
    state, err = _load(args.state)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    try:
        with open(changes_path, encoding="utf-8") as f:
            changes = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    operations = changes.get("operations") if isinstance(changes, dict) \
        else None
    if operations is None and isinstance(changes, list):
        operations = changes  # a bare operations list is unambiguous
    if not isinstance(operations, list):
        print("refusing: changes.yaml carries no operations list",
              file=sys.stderr)
        return 1
    working = copy.deepcopy(state) if mode != "--apply" else state
    norm_ops, findings = apply_operations(working, operations)
    if mode == "--check":
        if findings:
            for f in findings:
                print(f"refusing: {f}", file=sys.stderr)
            return 1
        print(f"valid: {len(norm_ops)} operation(s) would apply — "
              "nothing written")
        return 0
    if findings:
        for f in findings:
            print(f"refusing: {f}", file=sys.stderr)
        return 1
    # a state-changing edit can complete or unblock a project at target —
    # the same deterministic pass a tick uses, never a second algorithm
    week = (working.get("meta") or {}).get("clock", {}).get("week", 0)
    completions, blocked, pass_findings = completion_pass(working, week)
    if mode == "--preview":
        doc = {"operations": norm_ops,
               "findings": list(findings) + list(pass_findings),
               "completions": completions,
               "state": working}
        text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                              width=100)
        if args.out:
            out = pathlib.Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            print(f"preview written: {args.out} "
                  f"({len(norm_ops)} operation(s), "
                  f"{len(completions)} completion(s))")
        else:
            sys.stdout.write(text)
        return 0
    _store_save(working, args.state)
    print(f"applied {len(norm_ops)} operation(s), "
          f"{len(completions)} completion(s)")
    for line in report_lines({"deltas": {}, "completions": completions,
                              "blocked": blocked}):
        print(f"  [projects] {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
