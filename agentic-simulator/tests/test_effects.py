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
import store
from effects import (apply_effects, apply_session_record, coerce_week, main,
                     validate_effect)

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
        before = state_path.read_bytes()
        record_path = pathlib.Path(d) / "session.yaml"
        record_path.write_text(yaml.safe_dump({
            "source": "session:session-08",
            "events": [],
            "effects": [effect(faction_id="ghosts")],
        }, sort_keys=False), encoding="utf-8")
        rc = main(["--apply", str(record_path), "--state", str(state_path)])
        assert rc == 1
        assert state_path.read_bytes() == before, \
            "an invalid batch is all-or-nothing: not one byte written"


# ---- session events with explicit place ids (place-centered view) ----


def _apply_record(record):
    state = fresh_state()
    events, applied, errors = apply_session_record(record, state)
    return state, events, applied, errors


def test_session_event_place_ids_are_stored_with_the_event():
    state, events, _applied, errors = _apply_record({
        "source": "session:session-08",
        "events": [{"actor": "mint", "text": "The ferrymaster talked.",
                    "place_ids": ["ruravaqan"]}],
        "effects": [],
    })
    assert errors == [] and events == 1
    assert state["events"][-1]["place_ids"] == ["ruravaqan"]


def test_session_event_unknown_place_id_refuses():
    _state, _events, _applied, errors = _apply_record({
        "source": "session:session-08",
        "events": [{"actor": "mint", "text": "Somewhere else.",
                    "place_ids": ["not-a-place"]}],
        "effects": [],
    })
    assert errors and "not-a-place" in errors[0]


def test_session_event_place_ids_must_be_a_list():
    _state, _events, _applied, errors = _apply_record({
        "source": "session:session-08",
        "events": [{"actor": "mint", "text": "Bad shape.",
                    "place_ids": "ruravaqan"}],
        "effects": [],
    })
    assert errors and "place_ids" in errors[0]


def test_session_event_without_place_ids_stays_unlocated():
    state, events, _applied, errors = _apply_record({
        "source": "session:session-08",
        "events": [{"actor": "mint", "text": "No where recorded."}],
        "effects": [],
    })
    assert errors == [] and events == 1
    assert "place_ids" not in state["events"][-1]


# ---- ticket 19: batch semantics — normalization, weeks, duplicate ids ----


def test_padded_values_apply_normalized_never_padded_keys():
    # the ticket's repro: `field: " goal "` / `after: " high "` validated
    # clean (the validator strips) but used to WRITE the padded originals
    state = fresh_state()
    record = {
        "source": "session:session-08",
        "week": 7,
        "events": [],
        "effects": [
            {"id": " pad-1 ", "type": "set_field", "faction_id": " mint ",
             "field": " goal ", "after": " A new ambition ",
             "reason": "The DM says so.", "source": "session:session-08"},
            {"id": "pad-2", "type": "set_resource", "faction_id": "mint",
             "resource": " coin ", "expected_before": "low",
             "after": " high ", "reason": "Treasury refilled.",
             "source": "session:session-08"},
        ],
    }
    events, applied, errors = apply_session_record(record, state)
    assert (events, applied, errors) == (0, 2, [])
    mint = next(f for f in state["factions"] if f["id"] == "mint")
    assert mint["goal"] == "A new ambition"
    assert " goal " not in mint
    assert mint["resources"]["coin"] == "high"
    assert " coin " not in mint["resources"]
    # ids normalize too, so a padded id dedups against its bare form
    assert state["meta"]["applied_effects"] == ["pad-1", "pad-2"]
    store.validate_state(state)  # raises if the shape can't spend a week


def test_weekless_record_dates_everything_to_the_clock():
    # a record without a week used to store `week: null` events, which
    # validate_state refuses — the next --live exited 2 until hand surgery
    state = fresh_state()
    clock = state["meta"]["clock"]["week"]
    record = {
        "source": "session:session-08",
        "events": [{"actor": "mint", "text": "Weekless but dated."}],
        "effects": [effect()],  # no explicit effect week either
    }
    events, applied, errors = apply_session_record(record, state)
    assert (events, applied, errors) == (1, 1, [])
    weeks = {e["week"] for e in state["events"][-2:]}
    assert weeks == {clock}  # bullet and audit event: never None
    store.validate_state(state)


def test_string_weeks_coerce_never_store_verbatim():
    state = fresh_state()
    record = {
        "source": "session:session-08",
        "week": "8",
        "events": [{"actor": "mint", "text": "A quoted week.",
                    "week": "9"}],
        "effects": [effect(id="wk-1", week=None)],
    }
    events, applied, errors = apply_session_record(record, state)
    assert (events, applied, errors) == (1, 1, [])
    by_text = {e["text"]: e["week"] for e in state["events"][-2:]}
    assert by_text["A quoted week."] == 9  # the event's own week coerces
    assert by_text[effect()["reason"]] == 8  # the record's week coerces
    store.validate_state(state)


def test_coerce_week_reads_ints_and_digit_strings_only():
    assert coerce_week(8) == 8
    assert coerce_week(0) == 0  # week zero exists
    assert coerce_week(" 8 ") == 8
    assert coerce_week(None) is None
    assert coerce_week("eight") is None
    assert coerce_week("") is None
    assert coerce_week(-1) is None
    assert coerce_week(True) is None
    assert coerce_week(4.5) is None


def test_unreadable_weeks_refuse_the_whole_record():
    state = fresh_state()
    before = copy.deepcopy(state)
    record = {
        "source": "session:session-08",
        "week": "eight",
        "events": [{"actor": "mint", "text": "Never lands."}],
        "effects": [effect()],
    }
    _e, _a, errors = apply_session_record(record, state)
    assert errors and "week" in errors[0] and "eight" in errors[0]
    assert state == before  # all-or-nothing: nothing landed
    record["week"] = 7
    record["events"][0]["week"] = True
    _e, _a, errors = apply_session_record(record, state)
    assert errors and "week" in errors[0]
    assert state == before


def test_duplicate_ids_within_one_batch_refuse_naming_both():
    state = fresh_state()
    n_events = len(state["events"])
    second = effect(id=" same-1 ", type="set_field",
                    faction_id="bastard", field="goal",
                    after="A rival ambition.", expected_before=None,
                    reason="The bastard pivots.")
    del second["resource"]
    applied, errors = apply_effects(state, [
        effect(id="same-1", after="low", expected_before=None),
        second,
    ])
    assert applied == 0 and len(errors) == 1
    # the id AND each colliding effect (position + target) are named
    assert "'same-1'" in errors[0]
    assert "effect 1" in errors[0] and "effect 2" in errors[0]
    assert "mint.coin" in errors[0] and "bastard.goal" in errors[0]
    assert len(state["events"]) == n_events  # nothing landed
    assert "applied_effects" not in state.get("meta", {})


def test_same_id_across_two_records_still_dedups():
    state = fresh_state()
    first = {"source": "session:s1", "week": 7, "events": [],
             "effects": [effect(id="dup-x")]}
    assert apply_session_record(first, state)[1] == 1
    second = {"source": "session:s2", "week": 8, "events": [],
              "effects": [effect(id=" dup-x ")]}  # padded: same id
    events, applied, errors = apply_session_record(second, state)
    assert (events, applied, errors) == (0, 0, [])  # dedup, not refusal


def test_cli_quoted_padded_scalars_apply_normalized():
    # the ticket's trigger: stray whitespace in quoted YAML scalars,
    # end to end through the CLI the studio drives
    with tempfile.TemporaryDirectory() as d:
        state_path = temp_state_copy(d)
        record_path = pathlib.Path(d) / "session.yaml"
        record_path.write_text(
            "source: session:session-08\n"
            "week: \"8\"\n"
            "events:\n"
            "- actor: mint\n"
            "  text: Treasury refilled.\n"
            "effects:\n"
            "- id: \" pad-1 \"\n"
            "  type: set_resource\n"
            "  faction_id: mint\n"
            "  resource: \" coin \"\n"
            "  expected_before: low\n"
            "  after: \" high \"\n"
            "  reason: The treasury was refilled.\n"
            "  source: session:session-08\n",
            encoding="utf-8")
        rc = main(["--apply", str(record_path), "--state", str(state_path)])
        assert rc == 0
        after = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        mint = next(f for f in after["factions"] if f["id"] == "mint")
        assert mint["resources"]["coin"] == "high"
        assert " coin " not in mint["resources"]
        assert after["meta"]["applied_effects"] == ["pad-1"]
        evs = [e for e in after["events"] if e.get("effect") == "pad-1"]
        assert len(evs) == 1 and evs[0]["week"] == 8  # "8" coerced, stored int
        store.validate_state(after)


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
        test_session_event_place_ids_are_stored_with_the_event,
        test_session_event_unknown_place_id_refuses,
        test_session_event_place_ids_must_be_a_list,
        test_session_event_without_place_ids_stays_unlocated,
        test_padded_values_apply_normalized_never_padded_keys,
        test_weekless_record_dates_everything_to_the_clock,
        test_string_weeks_coerce_never_store_verbatim,
        test_coerce_week_reads_ints_and_digit_strings_only,
        test_unreadable_weeks_refuse_the_whole_record,
        test_duplicate_ids_within_one_batch_refuse_naming_both,
        test_same_id_across_two_records_still_dedups,
        test_cli_quoted_padded_scalars_apply_normalized,
    ]:
        fn()
    print("all effects tests passed")
