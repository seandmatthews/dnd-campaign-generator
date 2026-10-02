"""Regression tests for the 2026-10-01 seam-fix wave (architecture review).

Each test pins a failure that used to surface as a raw traceback at the
worst possible moment: a KeyError before the validator's own message could
fire, a crash after the LLM spend, an all-failed week saved as a quiet
week, and silent rewrites of a model's proposal.

Zero dependencies — run directly:  python tests/test_seam_fixes.py
"""

import contextlib
import io
import pathlib
import shutil
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import effects
import llm
import store
import tick
import views
import why
import rumors


def faction(fid):
    return {
        "id": fid, "name": fid.title(), "kind": "test", "goal": "test",
        "resources": {"coin": "medium", "muscle": "low"},
        "disposition": {}, "secret": "", "knows": [],
    }


def state(week, factions):
    return {
        "meta": {"clock": {"week": week}},
        "city": {"name": "Testburg"},
        "factions": factions,
        "events": [],
        "rumors": [],
    }


def test_an_effect_without_an_id_is_refused_not_a_keyerror():
    """apply_effects' dedup pass used to direct-index effect["id"] BEFORE
    validate_effect ran — the carefully written "missing id" refusal was
    unreachable and the DM saw a raw KeyError instead."""
    s = state(4, [faction("mint")])
    applied, errors = effects.apply_effects(
        s, [{"type": "set_resource", "faction_id": "mint",
             "resource": "coin", "after": "low", "reason": "the party paid"}])
    assert applied == 0, "an id-less effect must not apply"
    assert any("missing id" in e for e in errors), errors
    print("  effects: a missing id is the designed refusal, not a KeyError  OK")


def test_run_live_all_failed_proposals_abort_before_any_write():
    """A provider outage that kills every acting faction's proposal used
    to resolve as an all-holds week: clock advanced, state saved, exit 0 —
    canon indistinguishable from a quiet week. It must abort like budget
    exhaustion: nothing written, WeekAborted raised."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="tick-allfail-"))
    saved = (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR,
             tick.llm.propose, tick.llm.narrate_week, tick.save_state)
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    tick.CHRONICLE_DIR = root / "chronicle"
    real_save_state = tick.save_state
    tick.save_state = lambda doc, path=None: real_save_state(
        doc, path or tick.STATE_FILE)
    s = state(0, [faction("mint"), faction("rival")])  # founding week: both act
    tick.STATE_FILE.write_text(yaml.safe_dump(s), encoding="utf-8")

    def dead_agent(view, template, actor):
        raise RuntimeError(f"provider 503 for {actor}")

    tick.llm.propose = dead_agent
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            tick.run_live(s)
    except tick.WeekAborted as e:
        assert "mint" in str(e) and "rival" in str(e), \
            "the abort names the factions whose proposals failed"
        after = yaml.safe_load(tick.STATE_FILE.read_text(encoding="utf-8"))
        assert after["meta"]["clock"]["week"] == 0, "the clock did not advance"
        assert not list(tick.CHRONICLE_DIR.glob("*")), "no chronicle was written"
        assert not (root / "world" / "replay").exists(), "no replay was written"
    else:
        raise AssertionError("an all-failed week must abort, not write canon")
    finally:
        (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR, tick.llm.propose,
         tick.llm.narrate_week, tick.save_state) = saved
        shutil.rmtree(root, ignore_errors=True)
    print("  tick: an all-failed week aborts before any write  OK")


def test_run_live_engine_bug_crashes_loudly_not_as_a_hold():
    """The blanket per-faction except used to convert KeyError/AttributeError
    (engine defects, state-schema drift) into 'HOLDS (proposal failed: ...)'
    — the bug surfaced as model misbehavior and burned the budget. A bug
    class must crash the week before more spend."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="tick-bug-"))
    saved = (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR,
             tick.llm.propose, tick.llm.narrate_week, tick.save_state)
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    tick.CHRONICLE_DIR = root / "chronicle"
    real_save_state = tick.save_state
    tick.save_state = lambda doc, path=None: real_save_state(
        doc, path or tick.STATE_FILE)
    s = state(0, [faction("mint")])
    tick.STATE_FILE.write_text(yaml.safe_dump(s), encoding="utf-8")

    def buggy(view, template, actor):
        raise KeyError("rumor schema drifted")

    tick.llm.propose = buggy
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            tick.run_live(s)
    except KeyError:
        pass  # the bug class escaped the hold funnel — exactly the contract
    else:
        raise AssertionError("an engine bug must crash the week, not hold")
    finally:
        (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR, tick.llm.propose,
         tick.llm.narrate_week, tick.save_state) = saved
        shutil.rmtree(root, ignore_errors=True)
    print("  tick: engine bugs crash loudly instead of holding  OK")


def test_clamped_commitments_are_recorded_where_the_dm_sees_them():
    """Resources clamped to stock, unknown resources dropped, invalid risk
    coerced — all used to happen silently. Every rewrite now rides the
    proposal as llm_notes (printed by the tick, kept in the replay)."""
    p = llm._validate(
        {"action": "bribe the dockers", "target": "self",
         "resources_committed": {"coin": "high", "gold": "low"},
         "risk_acceptance": "reckless"},
        "mint", actual_resources={"coin": "low"})
    assert p["resources_committed"] == {"coin": "low"}, \
        "the clamp itself is unchanged"
    notes = " | ".join(p["llm_notes"])
    assert "gold" in notes, "a dropped unknown resource is named"
    assert "clamped" in notes and "coin" in notes, "a clamped resource is named"
    assert "risk_acceptance" in notes, "a coerced risk is named"
    q = llm._validate({"action": "act", "target": "self",
                       "risk_acceptance": "medium"}, "mint", {"coin": "low"})
    assert "llm_notes" not in q, "a clean proposal carries no notes"
    print("  llm: every silent rewrite is recorded as a note  OK")


def test_clis_report_corrupt_or_missing_state_readably():
    """views/why/ingest/goals caught only OSError — a corrupt (hand-edited)
    state file surfaced as a raw yaml traceback; tick caught only
    YAMLError, so a MISSING file tracebacked. All paths must be a one-line
    error and a non-zero exit."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="corrupt-"))
    bad = d / "state.yaml"
    bad.write_text("factions: [  unclosed", encoding="utf-8")
    assert views.main(["mint", "--state", str(bad)]) == 1
    assert why.main(["4", "mint", "--state", str(bad)]) == 1
    rec = d / "session.yaml"
    rec.write_text("events: []\neffects: []\n", encoding="utf-8")
    assert effects.main(["--check", str(rec), "--state", str(bad)]) == 1
    saved_state = store.STATE_FILE
    store.STATE_FILE = bad
    try:
        assert tick.main([]) == 2, "inspect mode: corrupt state, readable error"
        assert tick.main(["--live"]) == 2, "live mode never sees the bad state"
    finally:
        store.STATE_FILE = saved_state
    store.STATE_FILE = d / "absent.yaml"
    try:
        assert tick.main([]) == 2, "a missing state file is an error, not a traceback"
    finally:
        store.STATE_FILE = saved_state
    shutil.rmtree(d, ignore_errors=True)
    print("  cli: corrupt/missing state is a readable error everywhere  OK")


def test_effects_cli_empty_state_file_is_a_readable_error():
    """An empty state.yaml loaded to None used to AttributeError inside
    apply_session_record — now it is a one-line refusal."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="empty-"))
    empty = d / "state.yaml"
    empty.write_text("", encoding="utf-8")
    rec = d / "session.yaml"
    rec.write_text("events: []\neffects: []\n", encoding="utf-8")
    assert effects.main(["--check", str(rec), "--state", str(empty)]) == 1
    shutil.rmtree(d, ignore_errors=True)
    print("  effects: an empty state file is refused readably  OK")


def test_rumor_grades_are_single_sourced():
    assert rumors._GRADES is store.RUMOR_FIDELITY, \
        "spread() decays and validate_state() refuses against ONE ladder"
    print("  store/rumors: the fidelity ladder has one owner  OK")


if __name__ == "__main__":
    for fn in [
        test_an_effect_without_an_id_is_refused_not_a_keyerror,
        test_run_live_all_failed_proposals_abort_before_any_write,
        test_run_live_engine_bug_crashes_loudly_not_as_a_hold,
        test_clamped_commitments_are_recorded_where_the_dm_sees_them,
        test_clis_report_corrupt_or_missing_state_readably,
        test_effects_cli_empty_state_file_is_a_readable_error,
        test_rumor_grades_are_single_sourced,
    ]:
        fn()
    print("all seam-fix tests passed")
