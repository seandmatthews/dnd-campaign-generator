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

    type: set_field             # faction goal/kind/secret — allowlisted
    faction_id: bastard
    field: goal
    after: Engineer the wardens' fall from grace
    ...

Every effect is validated (type, entity ids, field allowlist, value
ranges, preconditions) BEFORE any write; one invalid effect refuses the
whole batch — nothing lands half-reviewed. Applied effects append one
sourced event each (the event is the audit trail; `why.py` reads it like
any other event) and record the effect id under `meta.applied_effects`
for deduplication.

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
    effects: [...]

Exit codes: 0 applied/valid, 1 validation or IO error, 2 usage.
"""

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import STATE_FILE
from store import load_state as _store_load
from store import save_state as _store_save

RESOURCE_KEYS = ("coin", "muscle", "legitimacy")
RESOURCE_LEVELS = ("none", "low", "medium", "high")
FIELD_KEYS = ("goal", "kind", "secret")
EFFECT_TYPES = ("set_resource", "set_field")


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
    if etype not in EFFECT_TYPES:
        errs.append(f"unknown type {etype!r} (use one of {', '.join(EFFECT_TYPES)})")
        return errs
    fid = str(effect.get("faction_id") or "").strip()
    faction = next((f for f in state.get("factions", [])
                    if f.get("id") == fid), None)
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
                errs.append(f"stale precondition: expected_before {expected!r} "
                            f"but {fid}.{resource} is {current!r}")
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
                errs.append(f"stale precondition: expected_before "
                            f"{expected!r} but {fid}.{field} is "
                            f"{current!r}")
    if not str(effect.get("reason") or "").strip():
        errs.append("missing reason (the event's text — why did this happen?)")
    return errs


def _apply_one(state, effect, week):
    """Mutate state for one already-validated effect: the value change plus
    exactly one sourced event. Returns the event."""
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


def apply_effects(state, effects, week=None, source=None):
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
                                           week=week, source=source)
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
        state.setdefault("events", []).append(entry)
        events += 1
    return events, applied, []


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main(argv):
    state_path = STATE_FILE
    mode = None
    record_path = None
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--check", "--apply"):
            mode = arg
        elif arg == "--state" and i + 1 < len(argv):
            state_path = argv[i + 1]
            i += 1
        elif arg.startswith("--state="):
            state_path = arg.split("=", 1)[1]
        else:
            rest.append(arg)
        i += 1
    if not mode or len(rest) != 1:
        print("usage: python src/effects.py (--check|--apply) <session.yaml> "
              "[--state <state.yaml>]", file=sys.stderr)
        return 2
    record_path = rest[0]
    try:
        record = _load(record_path)
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
