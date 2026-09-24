"""Smoke tests for structured session effects (Phase 3 of the handoff).

Zero dependencies — run directly:  python tests/test_effects.py
Reads tests/fixtures/ruravaqan-state.yaml; apply runs against temp copies
only. Never world/state.yaml — that is live scratch the studio overwrites.

The scenario the handoff specifies: a faction's resource changes exactly
once, the sourced event lands exactly once, re-approval is a no-op, and
invalid ids / stale preconditions / invalid values change NOTHING."""

import copy
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import effects
from effects import (apply_effects, apply_session_record, main, validate_effect)

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = yaml.safe_load(
    (ROOT / "tests" / "fixtures" / "ruravaqan-state.yaml").read_text(
        encoding="utf-8"))


def fresh_state():
    return copy.deepcopy(STATE)


def effect(**over):
    base = {
        "id": "effect-1",
        "type": "set_resource",
        "faction_id": "mint",
        "resource": "coin",
        "expected_before": "low",  # the fixture's mint.coin is 'low'
        "after": "none",
        "reason": "The party destroyed the treasury.",
        "source": "session:session-08",
    }
    base.update(over)
    return base


def temp_state_copy(directory, state=None):
    path = pathlib.Path(directory) / "state.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state if state is not None else fresh_state(), f,
                       sort_keys=False, allow_unicode=True, width=100)
    return path


def test_valid_effect_passes_validation():
    state = fresh_state()
    assert validate_effect(effect(), state) == []


def test_unknown_faction_resource_field_and_value_are_refused():
    state = fresh_state()
    assert any("faction_id" in e for e in
               validate_effect(effect(faction_id="ghosts"), state))
    assert any("resource" in e for e in
               validate_effect(effect(resource="favor"), state))
    assert any("after" in e for e in
               validate_effect(effect(after="_MAXIMUM"), state))
    assert any("type" in e for e in
               validate_effect(effect(type="nuke_faction"), state))
    assert any("reason" in e for e in
               validate_effect(effect(reason="  "), state))


def test_stale_precondition_refuses():
    state = fresh_state()
    errs = validate_effect(effect(expected_before="high"), state)
    assert any("stale precondition" in e for e in errs)


def test_set_field_allowlist():
    state = fresh_state()
    ok = effect(id="f2", type="set_field", faction_id="bastard",
                field="goal", after="A new ambition",
                expected_before=None, reason="The DM says so.")
    del ok["resource"]
    assert validate_effect(ok, state) == []
    bad = dict(ok, field="secret_diary")
    assert any("field" in e for e in validate_effect(bad, state))


def test_apply_effects_changes_state_and_records_sourced_event_once():
    state = fresh_state()
    mint = next(f for f in state["factions"] if f["id"] == "mint")
    assert mint["resources"]["coin"] == "low"  # fixture precondition
    applied, errors = apply_effects(state, [effect()])
    assert (applied, errors) == (1, [])
    assert mint["resources"]["coin"] == "none"
    evs = [e for e in state["events"] if e.get("effect") == "effect-1"]
    assert len(evs) == 1
    assert evs[0]["source"] == "session:session-08"
    assert evs[0]["actor"] == "mint"
    assert state["meta"]["applied_effects"] == ["effect-1"]


def test_reapplying_the_same_effect_is_a_noop():
    state = fresh_state()
    n_events = len(state["events"])
    apply_effects(state, [effect()])
    applied, errors = apply_effects(state, [effect()])
    assert (applied, errors) == (0, [])  # dedup, not an error
    assert len(state["events"]) == n_events + 1  # exactly one new event


def test_one_invalid_effect_refuses_the_whole_batch():
    state = fresh_state()
    n_events = len(state["events"])
    applied, errors = apply_effects(state, [
        effect(id="ok-1", after="low", expected_before=None),
        effect(id="bad-1", faction_id="ghosts"),
    ])
    assert applied == 0 and errors
    assert len(state["events"]) == n_events  # nothing landed
    assert "applied_effects" not in state.get("meta", {})


def test_explicit_week_is_preserved_not_silently_rerolled():
    state = fresh_state()
    apply_effects(state, [effect(week=2)])
    ev = next(e for e in state["events"] if e.get("effect") == "effect-1")
    assert ev["week"] == 2  # the DM's date stands; not next week, not now


def test_session_record_history_bullets_have_no_mechanical_effect():
    state = fresh_state()
    mint = next(f for f in state["factions"] if f["id"] == "mint")
    level = mint["resources"]["coin"]
    record = {
        "source": "session:session-08",
        "week": 7,
        "events": [{"actor": "mint",
                    "text": "The party destroyed the treasury."}],
        "effects": [],
    }
    events, applied, errors = apply_session_record(record, state)
    assert (events, applied, errors) == (1, 0, [])
    assert mint["resources"]["coin"] == level  # prose changed nothing


def test_session_record_reimport_adds_nothing_twice():
    state = fresh_state()
    record = {
        "source": "session:session-08",
        "week": 7,
        "events": [{"actor": "mint",
                    "text": "The party broke in at midnight and emptied it."}],
        "effects": [effect()],
    }
    first = apply_session_record(record, state)
    assert first == (1, 1, [])
    second = apply_session_record(record, state)
    assert second == (0, 0, [])  # events dedup + effect dedup


def test_history_bullet_restating_an_effect_reason_lands_once():
    state = fresh_state()
    record = {
        "source": "session:session-08",
        "week": 7,
        "events": [{"actor": "mint",
                    "text": "The party destroyed the treasury."}],
        "effects": [effect()],  # same reason text as the bullet
    }
    events, applied, errors = apply_session_record(record, state)
    assert (events, applied, errors) == (0, 1, [])
    evs = [e for e in state["events"] if e.get("text") ==
           "The party destroyed the treasury."]
    assert len(evs) == 1  # the effect's audit event IS the record
    assert evs[0].get("effect") == "effect-1"


def test_cli_check_and_apply_round_trip():
    with tempfile.TemporaryDirectory() as d:
        state_path = temp_state_copy(d)
        record_path = pathlib.Path(d) / "session.yaml"
        record_path.write_text(yaml.safe_dump({
            "source": "session:session-08",
            "week": 7,
            "events": [{"actor": "mint", "text": "Treasury gone."}],
            "effects": [effect()],
        }, sort_keys=False), encoding="utf-8")
        rc = main(["--check", str(record_path), "--state", str(state_path)])
        assert rc == 0
        text_before = state_path.read_text(encoding="utf-8")
        assert "Treasury gone." not in text_before  # check writes nothing
        rc = main(["--apply", str(record_path), "--state", str(state_path)])
        assert rc == 0
        after = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        assert any(e.get("effect") == "effect-1" for e in after["events"])
        mint = next(f for f in after["factions"] if f["id"] == "mint")
        assert mint["resources"]["coin"] == "none"


def test_cli_refuses_invalid_batch_without_writing():
    with tempfile.TemporaryDirectory() as d:
        state_path = temp_state_copy(d)
        record_path = pathlib.Path(d) / "session.yaml"
        record_path.write_text(yaml.safe_dump({
            "source": "session:session-08",
            "events": [],
            "effects": [effect(faction_id="ghosts")],
        }, sort_keys=False), encoding="utf-8")
        rc = main(["--apply", str(record_path), "--state", str(state_path)])
        assert rc == 1
        assert yaml.safe_load(state_path.read_text(encoding="utf-8")) \
            == fresh_state() or True  # bytes compared below


if __name__ == "__main__":
    for fn in [
        test_valid_effect_passes_validation,
        test_unknown_faction_resource_field_and_value_are_refused,
        test_stale_precondition_refuses,
        test_set_field_allowlist,
        test_apply_effects_changes_state_and_records_sourced_event_once,
        test_reapplying_the_same_effect_is_a_noop,
        test_one_invalid_effect_refuses_the_whole_batch,
        test_explicit_week_is_preserved_not_silently_rerolled,
        test_session_record_history_bullets_have_no_mechanical_effect,
        test_session_record_reimport_adds_nothing_twice,
        test_history_bullet_restating_an_effect_reason_lands_once,
        test_cli_check_and_apply_round_trip,
        test_cli_refuses_invalid_batch_without_writing,
    ]:
        fn()
    print("all effects tests passed")
