"""Regression tests for the 2026-09-30 consolidated review
(docs/reviews/2026-09-30-current-iteration.md), engine side.

DND-03  an effect batch must not reopen a project completed mid-batch
DND-04  validation and application agree on numeric faction ids
DND-20  an explicit week 0 is a real week; non-integer weeks are refused
OBS-01  malformed `places` list gets a precise diagnostic
OBS-02  case-variant place ids get a did-you-mean

Zero dependencies — run directly:  python tests/test_review_fixes.py
"""

import copy
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from effects import apply_effects, apply_session_record, validate_effect
from store import validate_state

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = yaml.safe_load(
    (ROOT / "tests" / "fixtures" / "ruravaqan-state.yaml").read_text(
        encoding="utf-8"))


def fresh_state():
    return copy.deepcopy(STATE)


def _project_state():
    state = fresh_state()
    state["projects"] = [{
        "id": "project-ferry-1",
        "schema_version": 1,
        "faction_id": "mint",
        "title": "Buy the ferry concession",
        "objective": "Own the crossing.",
        "place_ids": [],
        "status": "active",
        "progress": 3,
        "target": 4,
        "visibility": "restricted",
        "known_to": [],
        "requirements": {},
        "completion": {
            "text": "The concession is theirs; tolls flow again.",
            "effects": [{"slot": "s1", "type": "set_resource",
                         "faction_id": "mint", "resource": "coin",
                         "after": "medium",
                         "reason": "Toll income resumes."}],
        },
        "created_week": 2,
    }]
    return state


# ------------------------------------------------------- DND-03


def test_batch_cannot_reopen_a_project_completed_mid_batch():
    state = _project_state()
    n_events = len(state["events"])
    progress = {"id": "eff-p1", "type": "set_project_progress",
                "project_id": "project-ferry-1", "after": 4,
                "reason": "The deal closed.", "source": "session:s1"}
    reopen = {"id": "eff-p2", "type": "set_project_status",
              "project_id": "project-ferry-1", "after": "active",
              "reason": "Reopen it.", "source": "session:s1"}
    applied, errors = apply_effects(state, [progress, reopen], week=4)
    assert applied == 0, "a batch that would invalidate itself must refuse"
    assert errors, "the reopen must carry a refusal, not a silent pass"
    project = state["projects"][0]
    assert project["status"] == "active" and project["progress"] == 3
    assert len(state["events"]) == n_events, "all-or-nothing: nothing landed"
    assert "applied_effects" not in state.get("meta", {})


def test_progress_and_status_effects_still_work_together_when_valid():
    state = _project_state()
    state["projects"][0]["progress"] = 1
    pause = {"id": "eff-q1", "type": "set_project_status",
             "project_id": "project-ferry-1", "after": "paused",
             "reason": "Wait out the strike.", "source": "session:s1"}
    applied, errors = apply_effects(state, [pause], week=4)
    assert (applied, errors) == (1, [])
    assert state["projects"][0]["status"] == "paused"


# ------------------------------------------------------- DND-04


def test_numeric_faction_id_passes_check_and_apply_alike():
    state = fresh_state()
    state["factions"][0]["id"] = 123  # hand-edited numeric id
    eff = {"id": "eff-num", "type": "set_resource", "faction_id": "123",
           "resource": "coin", "after": "none",
           "reason": "The party destroyed the treasury.",
           "source": "session:s1"}
    assert validate_effect(eff, state) == [], "validation must accept it"
    applied, errors = apply_effects(state, [eff])
    assert (applied, errors) == (1, []), "application must not StopIteration"
    faction = next(f for f in state["factions"] if f["id"] == 123)
    assert faction["resources"]["coin"] == "none"
    ev = next(e for e in state["events"] if e.get("effect") == "eff-num")
    assert ev["actor"] == "123"


# ------------------------------------------------------- DND-20


def test_explicit_week_zero_is_not_treated_as_missing():
    state = fresh_state()
    eff = {"id": "eff-w0", "type": "set_resource", "faction_id": "mint",
           "resource": "coin", "after": "low",
           "reason": "A prologue loss, back in week 0.",
           "source": "session:s1", "week": 0}
    applied, errors = apply_effects(state, [eff], week=4)
    assert (applied, errors) == (1, [])
    ev = next(e for e in state["events"] if e.get("effect") == "eff-w0")
    assert ev["week"] == 0, "week 0 fell through to the clock week"


def test_non_integer_week_is_refused():
    state = fresh_state()
    errs = validate_effect({"id": "eff-ws", "type": "set_resource",
                            "faction_id": "mint", "resource": "coin",
                            "after": "low", "reason": "r",
                            "source": "s", "week": "7"}, state)
    assert any("not a non-negative integer" in e for e in errs)


# ------------------------------------------------------- OBS-01


def test_malformed_places_list_gets_a_precise_diagnostic():
    state = fresh_state()
    state["places"] = [None]
    try:
        validate_state(state)
    except ValueError as e:
        assert "none is usable" in str(e), \
            "the message must name the malformed list, not the legacy city"
    else:
        raise AssertionError("a malformed places list must be refused")


# ------------------------------------------------------- OBS-02


def test_case_variant_place_id_gets_a_did_you_mean():
    state = fresh_state()
    record = {"source": "session:s1", "week": 4, "effects": [],
              "events": [{"actor": "world", "text": "A procession passed.",
                          "place_ids": ["Ruravaqan"]}]}
    events, applied, errors = apply_session_record(record, state)
    assert (events, applied) == (0, 0)
    assert errors and any("did you mean 'ruravaqan'" in e for e in errors)


# ------------------------------------------------------- ticket 12
# rumor element-shape guards: the hand-edit shapes that used to escape as
# AttributeError/TypeError mid-week must be refused at validation with a
# diagnostic (docs/tickets/12-engine-shape-guards-complete.md)


def _rumor_state(**overrides):
    state = fresh_state()
    rumor = {
        "id": "rum-tithe", "claim": "The tithe doubled.",
        "fidelity": "hearsay", "heard_by": ["wardens"],
        "transmissions": [], "born_week": 2,
    }
    rumor.update(overrides)
    state["rumors"] = [rumor]
    return state


def _refused(state, needle):
    try:
        validate_state(state)
    except ValueError as e:
        assert needle in str(e), f"{needle!r} not in: {e}"
    else:
        raise AssertionError(f"expected a ValueError naming {needle!r}")


def test_non_mapping_transmission_is_refused_not_crashed():
    # used to escape as AttributeError ('str' has no .get) — the guard
    # dereferenced before its own isinstance check
    _refused(_rumor_state(transmissions=["week 3"]),
             "transmission 1 is not a mapping")


def test_missing_transmission_fields_name_the_fields():
    _refused(_rumor_state(transmissions=[{"week": 3}]),
             "missing from, to, fidelity_before, fidelity_after")


def test_explicit_null_quiet_is_refused_at_validation():
    # `quiet:` with no value: the key is PRESENT with a null, which
    # .get("quiet", []) returns as None — the mid-week TypeError shape
    _refused(_rumor_state(quiet=None), "quiet must be a list")
    _refused(_rumor_state(quiet="wardens"), "quiet must be a list")
    # absent and a proper list are both fine
    validate_state(_rumor_state())
    validate_state(_rumor_state(quiet=["mint"]))


def test_non_string_heard_by_entries_are_refused():
    _refused(_rumor_state(heard_by=["wardens", 7]),
             "heard_by entries must be non-empty faction ids")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all review-fix engine tests passed")
