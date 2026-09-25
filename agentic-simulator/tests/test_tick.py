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


def test_live_refuses_cityless_state_before_any_spend():
    """The regression: a city-less sandbox (studio used to allow one) crashed
    inside faction_view AFTER the scheduler ran — the guard now fails fast,
    before any LLM call, so no credits are spent on a week that cannot land."""
    s = state(0, [faction("a"), faction("b")])
    del s["city"]

    def boom(*a, **k):
        raise AssertionError("llm.propose must not be reached")

    tick.llm.propose = boom
    try:
        tick.run_live(s)
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("run_live must refuse a city-less state")
    print("  live: a city-less state is refused before any spend  OK")


def test_live_refuses_a_float_week_before_any_spend():
    """The studio's preflight refuses a float clock too, but standalone
    tick.py --live is its own path: week: 4.0 (a hand edit, a bad merge)
    used to crash f\"{week:04d}\" in write_chronicle AFTER the faction
    calls' spend, and re-crash on every retry."""
    s = state(4.0, [faction("a"), faction("b")])

    def boom(*a, **k):
        raise AssertionError("llm.propose must not be reached")

    tick.llm.propose = boom
    try:
        tick.run_live(s)
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("run_live must refuse a non-integer clock")
    print("  live: a float week is refused before any spend  OK")


def test_run_live_happy_path_writes_state_chronicle_and_replay():
    """The full wiring (previously untested): scheduler gate, per-faction
    proposals, dice, events, rumor spawn, chronicle, save_state, and the
    replay trail with its recorded seed — the replay identity the studio's
    week staging rides on."""
    import contextlib as _cl
    import io as _io
    import yaml as _yaml

    root = pathlib.Path(tempfile.mkdtemp(prefix="tick-live-"))
    saved = (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR,
             tick.llm.propose, tick.llm.narrate_week, tick.save_state)
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    tick.CHRONICLE_DIR = root / "chronicle"
    # save_state's default path is store.STATE_FILE (the real engine home):
    # force the patched path, or the test writes scratch over the live home
    real_save_state = tick.save_state
    tick.save_state = lambda doc, path=None: real_save_state(
        doc, path or tick.STATE_FILE)
    # last week (week 4) touched both factions: both activate and propose.
    # ids are real-length on purpose — the scheduler drops single-letter
    # tokens, so a faction id "a" would silently hold
    s = state(4, [faction("mint"), faction("rival")])
    s["events"] = [{"week": 4, "actor": "mint", "text": "mint moved on rival",
                    "contested": True, "vs": "rival", "outcome": "succeeded",
                    "margin": 1}]
    tick.STATE_FILE.write_text(_yaml.safe_dump(s), encoding="utf-8")
    tick.llm.propose = lambda view, template, actor: {
        "actor": actor, "action": "mint trades with rival" if actor == "mint"
        else "rival trades with mint",
        "target": "rival" if actor == "mint" else "mint",
        "resources_committed": {"coin": "low"},
        "risk_acceptance": "medium",
    }
    tick.llm.narrate_week = lambda *a, **k: "TITLE: The Trading Week\nThey traded."
    try:
        buf = _io.StringIO()
        with _cl.redirect_stdout(buf):
            tick.run_live(s)
        out = buf.getvalue()
        # all asserts INSIDE try: the finally restores the module globals,
        # and these paths are only valid while they still point at root
        after = _yaml.safe_load(tick.STATE_FILE.read_text(encoding="utf-8"))
        assert after["meta"]["clock"]["week"] == 5, "the clock advanced one week"
        actors = {e["actor"] for e in after["events"]}
        assert actors == {"mint", "rival"}, "each proposing faction got its event"
        seed = after["meta"]["rng_seeds"].get("5")
        assert seed is not None, "the week's RNG seed is recorded (replay identity)"
        chron = list(tick.CHRONICLE_DIR.glob("0005-*.md"))
        assert chron and "The Trading Week" in chron[0].read_text(encoding="utf-8")
        replay = root / "world" / "replay" / "week-5.yaml"
        doc = _yaml.safe_load(replay.read_text(encoding="utf-8"))
        assert doc["seed"] == seed and doc["week"] == 5
        assert {p["actor"] for p in doc["proposals"]} == {"mint", "rival"}
        assert "HOLDS" not in out, "both factions proposed; nobody held"
    finally:
        tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR, tick.llm.propose, \
            tick.llm.narrate_week, tick.save_state = saved
    print("  live: happy path writes state, chronicle, and the replay trail  OK")


def test_run_live_held_faction_format_is_the_studio_contract():
    """The studio's week staging parses `  <id>  HOLDS (<reasons>)` stdout
    lines and the chronicle's `Held (scheduler): a, b` line as its data —
    a cosmetic reword would silently change which weeks are judged held.
    Pin both formats here (the studio side pins the regex)."""
    import contextlib as _cl
    import io as _io
    import re as _re
    import yaml as _yaml

    root = pathlib.Path(tempfile.mkdtemp(prefix="tick-holds-"))
    saved = (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR,
             tick.llm.propose, tick.llm.narrate_week, tick.save_state)
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    tick.CHRONICLE_DIR = root / "chronicle"
    # save_state's default path is store.STATE_FILE (the real engine home):
    # force the patched path, or the test writes scratch over the live home
    real_save_state = tick.save_state
    tick.save_state = lambda doc, path=None: real_save_state(
        doc, path or tick.STATE_FILE)
    # week 1, no history, no hostility/pressure: the quiet case — all hold
    s = state(1, [faction("a"), faction("b")])
    tick.STATE_FILE.write_text(_yaml.safe_dump(s), encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("a held faction must not spend an LLM call")

    tick.llm.propose = boom
    tick.llm.narrate_week = lambda *a, **k: "A quiet week."
    try:
        buf = _io.StringIO()
        with _cl.redirect_stdout(buf):
            tick.run_live(s)
        out = buf.getvalue()
        held_lines = _re.findall(r"^\s{2}([a-z_]+)\s+HOLDS \((.+)\)$",
                                 out, _re.MULTILINE)
        assert {fid for fid, _ in held_lines} == {"a", "b"}, \
            f"held lines not in the studio-parsed format: {out!r}"
        chron = next(tick.CHRONICLE_DIR.glob("0002-*.md"))  # clock bumped: week 2
        text = chron.read_text(encoding="utf-8")
        held_line = next((ln for ln in text.splitlines()
                          if ln.startswith("Held (scheduler):")), None)
        assert held_line is not None, "the chronicle carries the Held line"
        held_names = {t for t in _re.split(r"[,\s]+",
                                           held_line.split(":", 1)[1].strip())
                      if t}
        assert held_names == {"a", "b"}
    finally:
        tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR, tick.llm.propose, \
            tick.llm.narrate_week, tick.save_state = saved
    print("  live: HOLDS stdout + chronicle formats match the studio's parsers  OK")


def test_chronicle_slug_is_capped():
    """A disobedient 60-word TITLE line must not push the filename past
    Windows MAX_PATH and kill the week AFTER its spend."""
    tick.llm.narrate_week = lambda *a, **k: \
        "TITLE: " + " ".join(f"word{i}" for i in range(60)) + "\nprose"
    with tempfile.TemporaryDirectory() as d:
        original_dir = tick.CHRONICLE_DIR
        tick.CHRONICLE_DIR = pathlib.Path(d)
        try:
            s = state(9, [faction("a")])
            out = write_chronicle(s, 9, [])
            assert len(out.stem) <= 4 + 1 + 80, f"stem too long: {out.name}"
            assert out.exists(), "file written under the capped slug"
        finally:
            tick.CHRONICLE_DIR = original_dir
    print("  chronicle: oversized titles cap the filename slug  OK")


def test_replay_same_seed_same_outcomes():
    """A recorded seed reproduces the week's dice — the rewind/retcon story."""
    proposals = [
        {"actor": "a", "action": "a acts", "target": "b",
         "resources_committed": {"coin": "medium"}, "risk_acceptance": "medium"},
        {"actor": "b", "action": "b acts", "target": "a",
         "resources_committed": {"muscle": "low"}, "risk_acceptance": "low"},
    ]
    mini_state = {"meta": {"clock": {"week": 4}}, "city": {"name": "T"},
                  "factions": [{"id": "a", "resources": {}},
                               {"id": "b", "resources": {}}], "events": []}
    resolve.set_rng(12345)
    first = resolve.resolve_tick(proposals, mini_state)
    resolve.set_rng(12345)
    second = resolve.resolve_tick(proposals, mini_state)
    assert first == second, "same seed, same dice"
    assert first[0]["contested"], "the mutual contest is actually exercised"
    # a different seed changes the dice: 20 draws per seed, and two equal
    # sequences would be a ~1-in-20**18 coincidence, not a flake
    resolve.set_rng(12345)
    base = [resolve.d(20) for _ in range(20)]
    resolve.set_rng(999)
    other = [resolve.d(20) for _ in range(20)]
    assert base != other, "different seeds, different dice"
    print("  replay: seeding resolve reproduces a week  OK")


def test_save_replay_and_replay_verify_round_trip():
    """save_replay persists seed + normalized proposals + rule version;
    replay_verify re-resolves offline and confirms the record (and refuses
    a foreign rule version instead of comparing apples to bolts)."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="tick-replay-"))
    saved_root, saved_state = tick.ROOT, tick.STATE_FILE
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    try:
        import yaml as _yaml
        state = {"meta": {"clock": {"week": 4}}, "city": {"name": "T"},
                 "factions": [{"id": "a", "resources": {"muscle": "low"}},
                              {"id": "b", "resources": {}}],
                 "events": []}
        tick.STATE_FILE.write_text(_yaml.safe_dump(state), encoding="utf-8")
        proposals = [{"actor": "a", "action": "a acts", "target": "b",
                      "resources_committed": {"coin": "medium"},
                      "risk_acceptance": "medium"}]
        resolve.set_rng(777)
        outcomes = resolve.resolve_tick(proposals, state)
        path = tick.save_replay(4, 777, proposals, outcomes,
                                "deadbeef" * 8)
        ok, message = tick.replay_verify(path)
        assert ok, message
        doc = _yaml.safe_load(path.read_text(encoding="utf-8"))
        doc["rule_version"] = 99
        path.write_text(_yaml.safe_dump(doc), encoding="utf-8")
        ok, message = tick.replay_verify(path)
        assert not ok and "rule version" in message
    finally:
        tick.ROOT, tick.STATE_FILE = saved_root, saved_state
    print("  replay trail: save -> verify reproduces; version mismatch refuses  OK")


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
        test_live_refuses_cityless_state_before_any_spend,
        test_live_refuses_a_float_week_before_any_spend,
        test_run_live_happy_path_writes_state_chronicle_and_replay,
        test_run_live_held_faction_format_is_the_studio_contract,
        test_chronicle_slug_is_capped,
        test_replay_same_seed_same_outcomes,
        test_save_replay_and_replay_verify_round_trip,
    ]:
        fn()
    print("all tick tests passed")
