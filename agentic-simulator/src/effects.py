"""Structured session effects: typed, validated, deduplicated state changes.

Play produces facts prose cannot safely carry: a treasury destroyed, a
goal abandoned. Those arrive as EFFECTS — small typed records the DM
reviews before anything changes:

    id: effect-1                # stable id; re-applying never repeats it
    type: set_resource
    faction_id: mint
    resource: coin              # coin | muscle | legitimacy
    expected_before: high       # optional precondition (current value)
    after: none                 # none | low | medium | high
    reason: The party destroyed the treasury.
    source: session:session-08  # provenance, kept on the event
    week: 7                     # optional in-world week (defaults to the
                                # session record's week, then the clock)

    type: set_field             # faction goal/kind/secret/species — allowlisted
    faction_id: bastard
    field: goal
    after: Engineer the wardens' fall from grace
    ...

    type: set_project_progress  # a project's counter, moved by the table
    project_id: project-ferry-7b03
    after: 2                    # integer, clamped to the project's target
    expected_before: 1          # optional (current counter)
    ...

    type: set_project_status    # active | paused | abandoned — NEVER
    project_id: project-ferry-7b03   # `completed`: completion is the
    after: paused                    # mechanics' job, not a text effect
    ...

Every effect is validated (type, entity ids, field allowlist, value
ranges, preconditions) BEFORE any write; one invalid effect refuses the
whole batch — nothing lands half-reviewed. Applied effects append one
sourced event each (the event is the audit trail; `why.py` reads it like
any other event) and record the effect id under `meta.applied_effects`
for deduplication. Reaching a project's target through set_project_progress
invokes the SAME completion pass a weekly tick uses (projects.py) — never
a second completion algorithm; `completed` is unreachable directly.

This module is the SHARED application code: ingest.py's pending files and
the studio's session proposals both land here, and the studio drives it
through this CLI (never by importing across projects):

  python src/effects.py --check session.yaml [--state PATH]   # validate only
  python src/effects.py --apply session.yaml [--state PATH]   # apply & save

The session record carries optional history bullets alongside effects:

    source: session:session-08
    week: 7
    events:                     # history ONLY — no mechanical effect
      - actor: mint
        text: The party broke into the mint's treasury at midnight.
        place_ids: [mint-quarter]   # optional WHERE — stored with the
                                    # event, never inferred from the
                                    # actor's current place later
    effects: [...]

Exit codes: 0 applied/valid, 1 validation or IO error, 2 usage.
"""

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import (
    RESOURCE_KEYS,
    RESOURCE_LEVELS,
    STATE_FILE,
    faction_map,
    places_of,
    split_argv,
)
from store import load_state as _store_load
from store import save_state as _store_save

FIELD_KEYS = ("goal", "kind", "secret", "species")
EFFECT_TYPES = ("set_resource", "set_field",
                "set_project_progress", "set_project_status")
PROJECT_EFFECT_TYPES = ("set_project_progress", "set_project_status")
# `completed` is deliberately absent: completion happens ONLY through the
# shared mechanics (a weekly tick or the session progress pass), never by
# a typed effect's text field
PROJECT_STATUS_AFTER = ("active", "paused", "abandoned")


def _stale(expected, where, current):
    return (f"stale precondition: expected_before {expected!r} "
            f"but {where} is {current!r}")


def _validate_project_effect(effect, state):
    """The two project effect types: keyed on project_id (not faction),
    refused outright on terminal projects — corrections reopen nothing."""
    errs = []
    import projects as _projects
    pid = str(effect.get("project_id") or "").strip()
    project = _projects.find_project(state, pid)
    if project is None:
        return [f"unknown project_id {pid!r}"]
    status = project.get("status", "active")
    if status in _projects.TERMINAL_STATUSES:
        return [(f"project {pid!r} is {status} — terminal projects take no "
                 "session effects")]
    after = effect.get("after")
    if effect["type"] == "set_project_progress":
        if not isinstance(after, int) or isinstance(after, bool):
            if isinstance(after, str) and after.strip().lstrip("-").isdigit():
                after = int(after.strip())
            else:
                return [f"after {after!r} is not an integer counter"]
        target = project.get("target", _projects.DEFAULT_TARGET)
        if not (0 <= after <= target):
            return [f"after {after!r} is outside 0..{target}"]
        expected = effect.get("expected_before")
        if expected not in (None, ""):
            try:
                expected = int(expected)
            except (TypeError, ValueError):
                return [(f"expected_before {effect.get('expected_before')!r} "
                         "is not an integer")]
            if expected != project.get("progress", 0):
                return [_stale(expected, f"{pid} progress",
                               project.get("progress", 0))]
    else:  # set_project_status
        after = str(after or "").strip()
        if after not in PROJECT_STATUS_AFTER:
            return [(f"after {after!r} is not one of "
                     f"{', '.join(PROJECT_STATUS_AFTER)} — completion is "
                     "never set directly")]
        expected = effect.get("expected_before")
        if expected not in (None, "") \
                and str(expected) != str(project.get("status", "active")):
            return [_stale(expected, f"{pid} status", project.get("status"))]
        if after == "active" and status != "active":
            owner = project.get("faction_id")
            clash = _projects.one_active_clause(state, owner, ignore_pid=pid)
            if clash is not None:
                return [(f"{owner} already runs the active project "
                         f"{clash.get('id')!r} — one active project per "
                         "faction")]
    return errs


def validate_effect(effect, state):
    """Every reason one effect cannot land, as a list — empty means valid.
    Checks shape, ids, allowlists, ranges, and preconditions against the
    CURRENT state (the caller validates the whole batch against one
    snapshot, before anything mutates)."""
    errs = []
    if not isinstance(effect, dict):
        return ["effect is not a mapping"]
    eid = str(effect.get("id") or "").strip()
    etype = str(effect.get("type") or "").strip()
    if not eid:
        errs.append("missing id (a stable id is what makes re-approval safe)")
    if etype in PROJECT_EFFECT_TYPES:
        errs.extend(_validate_project_effect(effect, state))
        return errs
    if etype not in EFFECT_TYPES:
        errs.append(f"unknown type {etype!r} (use one of {', '.join(EFFECT_TYPES)})")
        return errs
    fid = str(effect.get("faction_id") or "").strip()
    faction = faction_map(state).get(fid)
    if faction is None:
        errs.append(f"unknown faction_id {fid!r}")
        return errs  # preconditions below need the faction
    if etype == "set_resource":
        resource = str(effect.get("resource") or "").strip()
        if resource not in RESOURCE_KEYS:
            errs.append(f"unknown resource {resource!r} "
                        f"(use one of {', '.join(RESOURCE_KEYS)})")
        after = str(effect.get("after") or "").strip()
        if after not in RESOURCE_LEVELS:
            errs.append(f"after {after!r} is not a resource level "
                        f"(use one of {', '.join(RESOURCE_LEVELS)})")
        expected = effect.get("expected_before")
        if expected not in (None, ""):
            current = (faction.get("resources") or {}).get(resource)
            if str(expected) != str(current):
                errs.append(_stale(expected, f"{fid}.{resource}", current))
    else:  # set_field
        field = str(effect.get("field") or "").strip()
        if field not in FIELD_KEYS:
            errs.append(f"unknown field {field!r} (use one of "
                        f"{', '.join(FIELD_KEYS)})")
        after = str(effect.get("after") or "").strip()
        if not after:
            errs.append("set_field needs non-empty after text")
        expected = effect.get("expected_before")
        if expected not in (None, ""):
            current = faction.get(field)
            if str(expected) != str(current or ""):
                errs.append(_stale(expected, f"{fid}.{field}", current))
    if not str(effect.get("reason") or "").strip():
        errs.append("missing reason (the event's text — why did this happen?)")
    return errs


def _apply_one(state, effect, week):
    """Mutate state for one already-validated effect: the value change plus
    exactly one sourced event. Returns the event."""
    import projects as _projects
    if effect["type"] in PROJECT_EFFECT_TYPES:
        pid = effect["project_id"]
        project = _projects.find_project(state, pid)
        owner = project.get("faction_id")
        if effect["type"] == "set_project_progress":
            before = project.get("progress", 0)
            project["progress"] = int(effect["after"])
            detail = (f"progress {before} -> {project['progress']} "
                      f"(target {project.get('target')})")
        else:
            before = project.get("status", "active")
            project["status"] = str(effect["after"])
            project.pop("completion_blocked", None)
            detail = f"status {before} -> {project['status']}"
        ev = {
            "week": week,
            "actor": owner,
            "project_id": pid,
            "text": f"{effect['reason']} ({detail})",
            "effect": str(effect["id"]),
        }
        if effect.get("source"):
            ev["source"] = str(effect["source"])
        state.setdefault("events", []).append(ev)
        state.setdefault("meta", {}).setdefault("applied_effects", []) \
            .append(str(effect["id"]))
        # routes through the shared completion pass (see module docstring)
        _projects.completion_pass(state, week)
        return ev
    fid = effect["faction_id"]
    faction = next(f for f in state["factions"] if f["id"] == fid)
    if effect["type"] == "set_resource":
        faction.setdefault("resources", {})[effect["resource"]] = \
            str(effect["after"])
    else:
        faction[effect["field"]] = str(effect["after"])
    ev = {
        "week": week,
        "actor": fid,
        "text": str(effect["reason"]).strip(),
        "effect": str(effect["id"]),
    }
    if effect.get("source"):
        ev["source"] = str(effect["source"])
    state.setdefault("events", []).append(ev)
    state.setdefault("meta", {}).setdefault("applied_effects", []) \
        .append(str(effect["id"]))
    return ev


def apply_effects(state, effects, week=None):
    """All-or-nothing: validate the whole batch against the CURRENT state,
    then apply. Already-applied ids are skipped (dedup), so re-applying a
    session record changes nothing twice. Returns (applied_count, errors).
    Nothing is written here — the caller owns saving (ingest.apply, the
    CLI's --apply, or the studio's publication path)."""
    known_ids = set(state.get("meta", {}).get("applied_effects") or [])
    errors = []
    todo = []
    for i, effect in enumerate(effects or []):
        if not isinstance(effect, dict):
            errors.append(f"effect {i + 1}: not a mapping")
            continue
        if str(effect.get("id")) in known_ids:
            continue  # dedup first: an applied effect must not fail on a
            # stale precondition — its own application changed the value
        errs = validate_effect(effect, state)
        if errs:
            errors.extend(f"effect {str(effect.get('id') or i + 1)}: {e}"
                          for e in errs)
            continue
        todo.append(effect)
        known_ids.add(str(effect["id"]))
    if errors:
        return 0, errors
    for effect in todo:
        _apply_one(state, effect,
                   int(effect.get("week") or week
                       or state.get("meta", {}).get("clock", {})
                       .get("week") or 0))
    return len(todo), []


def event_exists(state, week, actor, text):
    """Reimport guard for plain history bullets: the same (week, actor,
    text) is already on record."""
    for ev in state.get("events", []):
        if (ev.get("week"), ev.get("actor"), ev.get("text")) == \
                (week, actor, text):
            return True
    return False


def apply_session_record(record, state):
    """One session record = history events (no mechanical effect) +
    validated effects. All-or-nothing across both. Returns
    (events_added, effects_applied, errors). Effects land FIRST so a
    history bullet that merely restates an effect's reason dedups against
    the effect's audit event instead of recording it twice."""
    if not isinstance(record, dict):
        return 0, 0, ["session record is not a mapping"]
    week = record.get("week")
    source = str(record.get("source") or "").strip() or None
    errors = []
    # validate everything up front (against the pre-application state)
    known = {f["id"] for f in state.get("factions", [])} | {"world"}
    place_ids = {p["id"] for p in places_of(state or {})}
    for i, ev in enumerate(record.get("events") or []):
        if not isinstance(ev, dict):
            errors.append(f"event {i + 1}: not a mapping")
            continue
        actor = str(ev.get("actor") or "world")
        text = str(ev.get("text") or "").strip()
        if actor not in known:
            errors.append(f"event {i + 1}: unknown actor {actor!r}")
        elif not text:
            errors.append(f"event {i + 1}: empty text")
        # explicit event locations: the WHERE is stored with the event at
        # creation, never inferred from the actor's current place later —
        # the faction may have moved, or acted remotely
        eplaces = ev.get("place_ids")
        if eplaces is not None:
            if not isinstance(eplaces, list) or \
                    not all(str(x).strip() for x in eplaces):
                errors.append(
                    f"event {i + 1}: place_ids must be a list of place ids")
            else:
                unknown = [str(x).strip() for x in eplaces
                           if str(x).strip() not in place_ids]
                if unknown:
                    errors.append(
                        f"event {i + 1}: unknown place id(s): "
                        + ", ".join(unknown))
    for i, effect in enumerate(record.get("effects") or []):
        if not isinstance(effect, dict):
            errors.append(f"effect {i + 1}: not a mapping")
            continue
        if not str(effect.get("source") or source or "").strip():
            errors.append(f"effect {str(effect.get('id') or i + 1)}: "
                          "missing source provenance")
    if errors:
        return 0, 0, errors
    applied, effect_errors = apply_effects(state, record.get("effects") or [],
                                           week=week)
    errors.extend(effect_errors)
    if errors:
        return 0, 0, errors
    events = 0
    for ev in record.get("events") or []:
        actor = str(ev.get("actor") or "world")
        text = str(ev["text"]).strip()
        ev_week = ev.get("week") if ev.get("week") is not None else week
        if event_exists(state, ev_week, actor, text):
            continue  # already on record (incl. an effect's audit event)
        entry = {"week": ev_week, "actor": actor, "text": text}
        if source:
            entry["source"] = source
        eplaces = ev.get("place_ids")
        if eplaces:
            entry["place_ids"] = [str(x).strip() for x in eplaces
                                  if str(x).strip()]
        state.setdefault("events", []).append(entry)
        events += 1
    return events, applied, []


def main(argv):
    values, rest = split_argv(argv, "--state")
    state_path = values.get("--state", STATE_FILE)
    mode = None
    pos = []
    for a in rest:
        if a in ("--check", "--apply"):
            mode = a
        else:
            pos.append(a)
    if not mode or len(pos) != 1:
        print("usage: python src/effects.py (--check|--apply) <session.yaml> "
              "[--state <state.yaml>]", file=sys.stderr)
        return 2
    record_path = pos[0]
    try:
        record = _store_load(record_path)
        state = _store_load(state_path)
    except (OSError, yaml.YAMLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    events, applied, errors = apply_session_record(record or {}, state)
    if errors:
        for e in errors:
            print(f"refusing: {e}", file=sys.stderr)
        return 1
    if mode == "--check":
        print(f"valid: {events} history event(s), {applied} effect(s) "
              "would apply — nothing written")
        return 0
    _store_save(state, state_path)
    print(f"applied {events} history event(s), {applied} effect(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
