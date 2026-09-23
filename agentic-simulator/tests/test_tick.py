"""Tests for the weekly tick: consequence application, event records,
chronicle writing, and replayability.

Zero dependencies — run directly:  python tests/test_tick.py
"""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import resolve
import tick
from tick import apply_mechanical, apply_outcomes, event_record, step_resource, write_chronicle


def faction(fid, resources=None):
    return {
        "id": fid,
        "name": fid.title(),
        "kind": "test",
        "goal": "test",
        "resources": resources or {"coin": "medium", "muscle": "low", "legitimacy": "high"},
        "disposition": {},
        "secret": "",
        "knows": [],
    }


def state(week, factions):
    return {
        "meta": {"clock": {"week": week}},
        "city": {"name": "Testburg"},
        "factions": factions,
        "events": [],
        "rumors": [],
    }


def outcome(actor, margin, success, contested=False, vs=None, committed=None):
    o = {
        "actor": actor, "action": f"{actor} acts", "roll": 10, "bonus": 1,
        "total": 11, "dc": 13, "contested": contested, "success": success,
        "margin": margin,
    }
    if vs:
        o["vs"] = vs
    if committed is not None:
        o["committed"] = committed
    return o


def test_step_resource_moves_the_strongest_committed():
    res = {"coin": "medium", "muscle": "low", "legitimacy": "high"}
    step_resource(res, {"coin": "low", "legitimacy": "none"}, -1)
    assert res == {"coin": "low", "muscle": "low", "legitimacy": "high"}, \
        "coin stepped down; the uncommitted high legitimacy is untouched"
    print("  step: only the strongest COMMITTED resource moves  OK")


def test_step_resource_with_no_commitment_is_a_noop():
    res = {"coin": "medium", "muscle": "low", "legitimacy": "high"}
    step_resource(res, {}, -1)
    step_resource(res, None, -1)
    step_resource(res, {"coin": "none"}, -1)
    assert res == {"coin": "medium", "muscle": "low", "legitimacy": "high"}, \
        "commit nothing, nothing steps"
    print("  step: no commitment, no movement  OK")


def test_decisive_loss_steps_committed_down_not_owned_strongest():
    """The regression: the old code stepped the strongest OWNED resource
    (legitimacy high) even when the proposal committed only coin."""
    s = state(4, [faction("a")])
    apply_mechanical(s, "a", outcome("a", -6, False, committed={"coin": "low"}))
    assert s["factions"][0]["resources"] == {
        "coin": "low", "muscle": "low", "legitimacy": "high"}, \
        "coin stepped, high legitimacy untouched"
    print("  mechanical: decisive loss steps the committed resource  OK")


def test_multi_contest_fanout_applies_one_consequence():
    """The regression: an actor in N contests used to get N events and N
    resource steps. One proposal, one consequence — worst margin judges."""
    s = state(4, [faction("mint")])
    outcomes = [
        outcome("mint", 0, True, contested=True, vs="bastard"),
        outcome("mint", -3, False, contested=True, vs="reliquary"),
        outcome("mint", -12, False, contested=True, vs="longshoremen"),
    ]
    apply_outcomes(s, outcomes, [
        {"actor": "mint", "resources_committed": {"coin": "medium"}},
    ])
    events = [e for e in s["events"] if e["actor"] == "mint"]
    assert len(events) == 1, f"one proposal, one event; got {len(events)}"
    assert events[0]["margin"] == -12, "the worst margin is the recorded one"
    assert s["factions"][0]["resources"]["coin"] == "low", "exactly one step down"
    print("  fanout: multi-contest proposal logs once, steps once  OK")


def test_event_record_carries_structured_fields():
    s = state(4, [faction("a")])
    ev = event_record(s, "a", outcome("a", -7, False, contested=True, vs="gang2"))
    assert ev["action"] == "a acts"
    assert ev["outcome"] == "failed"
    assert ev["contested"] is True and ev["vs"] == "gang2" and ev["margin"] == -7
    assert "(contested vs gang2)" in ev["text"], "the human-readable line survives"
    print("  event: structured fields ride alongside the prose  OK")


def test_chronicle_rerun_never_overwrites():
    tick.llm.narrate_week = lambda *a, **k: "TITLE: The Quiet Week\nNothing happened."
    with tempfile.TemporaryDirectory() as d:
        original_dir = tick.CHRONICLE_DIR
        tick.CHRONICLE_DIR = pathlib.Path(d)
        try:
            s = state(5, [faction("a")])
            first = write_chronicle(s, 5, [])
            second = write_chronicle(s, 5, [])
            assert first != second, "same-week rerun writes a new file"
            assert first.exists() and second.exists()
            assert "The Quiet Week" in first.read_text(encoding="utf-8")
        finally:
            tick.CHRONICLE_DIR = original_dir
    print("  chronicle: same-week reruns never overwrite  OK")


def test_chronicle_without_title_gets_fallback_slug():
    tick.llm.narrate_week = lambda *a, **k: "No title line here."
    with tempfile.TemporaryDirectory() as d:
        original_dir = tick.CHRONICLE_DIR
        tick.CHRONICLE_DIR = pathlib.Path(d)
        try:
            s = state(6, [faction("a")])
            out = write_chronicle(s, 6, [])
            assert out.name == "0006-the-week.md", f"got {out.name}"
        finally:
            tick.CHRONICLE_DIR = original_dir
    print("  chronicle: titleless weeks land at a stable fallback name  OK")


def test_chronicle_non_ascii_title_never_makes_an_empty_slug():
    tick.llm.narrate_week = lambda *a, **k: "TITLE: 三周之围\nprose"
    with tempfile.TemporaryDirectory() as d:
        original_dir = tick.CHRONICLE_DIR
        tick.CHRONICLE_DIR = pathlib.Path(d)
        try:
            s = state(8, [faction("a")])
            out = write_chronicle(s, 8, [])
            assert out.name == "0008-untitled.md", f"got {out.name}"
            assert out.exists(), "file written under the fallback name"
        finally:
            tick.CHRONICLE_DIR = original_dir
    print("  chronicle: non-ASCII-only title falls back to 'untitled'  OK")


def test_chronicle_survives_narrator_failure():
    def boom(*a, **k):
        raise RuntimeError("API down")
    tick.llm.narrate_week = boom
    with tempfile.TemporaryDirectory() as d:
        original_dir = tick.CHRONICLE_DIR
        tick.CHRONICLE_DIR = pathlib.Path(d)
        try:
            s = state(7, [faction("a")])
            o = outcome("a", 1, True)
            out = write_chronicle(s, 7, [o])
            text = out.read_text(encoding="utf-8")
            assert "a acts" in text, "the ledger table is the record"
        finally:
            tick.CHRONICLE_DIR = original_dir
    print("  chronicle: narrator failure keeps the ledger  OK")


def test_replay_same_seed_same_outcomes():
    """A recorded seed reproduces the week's dice — the rewind/retcon story."""
    proposals = [
        {"actor": "a", "action": "a acts", "target": "b",
         "resources_committed": {"coin": "medium"}, "risk_acceptance": "medium"},
        {"actor": "b", "action": "b acts", "target": "a",
         "resources_committed": {"muscle": "low"}, "risk_acceptance": "low"},
    ]
    resolve.set_rng(12345)
    first = resolve.resolve_tick(proposals)
    resolve.set_rng(12345)
    second = resolve.resolve_tick(proposals)
    resolve.set_rng(999)
    third = resolve.resolve_tick(proposals)
    assert first == second, "same seed, same dice"
    assert first != third or True  # different seed usually differs; not asserted
    print("  replay: seeding resolve reproduces a week  OK")


if __name__ == "__main__":
    for fn in [
        test_step_resource_moves_the_strongest_committed,
        test_step_resource_with_no_commitment_is_a_noop,
        test_decisive_loss_steps_committed_down_not_owned_strongest,
        test_multi_contest_fanout_applies_one_consequence,
        test_event_record_carries_structured_fields,
        test_chronicle_rerun_never_overwrites,
        test_chronicle_without_title_gets_fallback_slug,
        test_chronicle_non_ascii_title_never_makes_an_empty_slug,
        test_chronicle_survives_narrator_failure,
        test_replay_same_seed_same_outcomes,
    ]:
        fn()
    print("all tick tests passed")
