"""Regression tests for the 2026-10-02 hand-edit tolerance wave (ticket 18)
and the read-path shape gate (ticket 65).

state.yaml is hand-edited by design and validate_state is the gate; each
test pins a plausible hand edit that used to crash the week (one of them
after the full provider spend, re-burned on every retry) or silently
misbehave: numeric/duplicate faction ids, a scalar alias value, a scalar
generic_words, a non-numeric meta.rules.default_dc, a null disposition,
a faction without a name — and, for the read paths validate_state never
gates, a null factions entry, a scalar meta.applied_effects, and a
missing meta.clock (all refused by name since ticket 65).

Zero dependencies — run directly:  python tests/test_hand_edit_tolerance.py
"""

import contextlib
import io
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import consistency
import effects
import ingest
import llm
import resolve
import scheduler
import store
import tick
import views
import why
from store import read_shape_errors, validate_state


def faction(fid, **over):
    f = {
        "id": fid, "name": str(fid).title(), "kind": "test", "goal": "test",
        "resources": {"coin": "medium", "muscle": "low"},
        "disposition": {}, "secret": "", "knows": [],
    }
    f.update(over)
    return f


def state(week, factions, **top):
    s = {
        "meta": {"clock": {"week": week}},
        "city": {"name": "Testburg"},
        "factions": factions,
        "events": [],
        "rumors": [],
    }
    s.update(top)
    return s


def with_rules(s, rules):
    out = dict(s)
    out["meta"] = {**s["meta"], "rules": rules}
    return out


def test_validate_state_refuses_each_hand_edit_shape_naming_the_key():
    """The seven gaps of the review wave, each refused at the gate with
    the offending key named (and, for ids, the quoted fix shown)."""
    base = state(4, [faction("mint"), faction("rival")])
    cases = [
        (state(4, [faction(123), faction("rival")]),
         ["faction id 123 must be text", "id: '123'"]),
        (state(4, [faction(12.5)]),
         ["faction id 12.5 must be text"]),
        (state(4, [faction("mint"), faction("mint")]),
         ["two factions share the id 'mint'"]),
        ({**base, "aliases": {"mint": "ferrand"}},
         ["aliases for faction 'mint' must be a list of match words"]),
        ({**base, "aliases": ["ferrand"]},
         ["aliases must be a mapping"]),
        ({**base, "generic_words": "council harbor"},
         ["generic_words must be a list of words"]),
        (with_rules(base, {"default_dc": "twelve"}),
         ["meta.rules.default_dc is 'twelve', not a number"]),
        (with_rules(base, {"default_dc": True}),
         ["meta.rules.default_dc is True, not a number"]),
        (with_rules(base, "twelve"),
         ["meta.rules must be a mapping"]),
    ]
    for doc, expected in cases:
        try:
            validate_state(doc)
        except ValueError as e:
            for piece in expected:
                assert piece in str(e), f"{piece!r} not named in: {e}"
        else:
            raise AssertionError(f"validate_state accepted {doc!r}")
    print("  validate: each hand-edit gap is refused with the key named  OK")


def test_validate_state_accepts_the_documented_shapes():
    """The refusal names the fix, so the fix must actually pass: list
    aliases, list generic_words, and a default_dc the resolver can int()
    (an int or a numeric string)."""
    s = state(4, [faction("mint"), faction("rival")],
              aliases={"mint": ["ferrand"], "rival": []},
              generic_words=["council", "harbor"])
    validate_state(with_rules(s, {"default_dc": 12}))
    validate_state(with_rules(s, {"default_dc": "12"}))
    validate_state(with_rules(s, {"default_dc": None}))  # absent-equivalent
    print("  validate: the documented shapes all pass the gate  OK")


def test_live_refuses_the_hand_edit_shapes_before_any_spend():
    """Numeric ids used to crash the scheduler's token set mid-week and
    'twelve' crashed the first roll AFTER the spend; duplicate ids and the
    scalar shapes misbehaved silently. Each must exit 2 at the gate, the
    piece named, llm.propose never reached."""

    def boom(*a, **k):
        raise AssertionError("llm.propose must not be reached")

    saved_propose = tick.llm.propose
    tick.llm.propose = boom
    cases = [
        (state(0, [faction(123), faction("rival")]), "must be text"),
        (state(4, [faction("mint"), faction("mint")]), "share the id"),
        (with_rules(state(4, [faction("mint"), faction("rival")]),
                    {"default_dc": "twelve"}), "default_dc"),
        ({**state(4, [faction("mint")]), "aliases": {"mint": "ferrand"}},
         "aliases for faction 'mint'"),
        ({**state(4, [faction("mint")]), "generic_words": "council harbor"},
         "generic_words"),
    ]
    try:
        for s, expected in cases:
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    tick.run_live(s)
            except SystemExit as e:
                assert e.code == 2, f"{expected}: exit {e.code}"
                assert expected in buf.getvalue(), \
                    f"{expected!r} not named in: {buf.getvalue()!r}"
            else:
                raise AssertionError(
                    f"run_live accepted a state it must refuse ({expected})")
    finally:
        tick.llm.propose = saved_propose
    print("  live: every hand-edit gap is refused before any spend  OK")


def test_numeric_id_state_resolves_consistently_past_the_gate():
    """The seam the numeric-id finding exposed: llm._validate canonicalizes
    targets to str(fid) while the resolver collected raw ids, so resolution
    raised 'unknown target' AFTER the week's spend. A numeric id that
    bypasses validate_state (a hand-built state, a replayed week) must
    still flow scheduler -> propose -> resolve with no TypeError and no
    post-spend ValueError — everything the pipeline exchanges is text."""
    s = state(4, [
        {"id": 123, "name": "The Numerate Guild", "kind": "guild",
         "goal": "count twice", "resources": {"coin": "low"},
         "disposition": {}, "knows": []},
        {"id": 456, "name": "The Second Ledger", "kind": "guild",
         "goal": "account for it", "resources": {"coin": "low"},
         "disposition": {}, "knows": []},
    ])
    s["events"] = [{"week": 4, "actor": 123,
                    "text": "the Numerate Guild moved on the Second Ledger",
                    "contested": True, "vs": 456, "outcome": "succeeded",
                    "margin": 1}]
    act = scheduler.activation(s)  # used to TypeError at len(<int id>)
    assert set(act) == {123, 456} and all(v["act"] for v in act.values()), \
        "both factions activate (founding-week touch)"

    def fake_complete(prompt, **k):
        # the model replies with an UNQUOTED number — YAML parses it as an
        # int, exactly the hand-edit/plreply shape that split the seam
        target = 456 if "Numerate" in prompt else 123
        return (f"```yaml\naction: move on the rival ledgers\n"
                f"target: {target}\nrisk_acceptance: medium\n```")

    saved_complete = llm.complete
    llm.complete = fake_complete
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            p1 = llm.propose(views.faction_view(s, 123),
                             "{{FACTION_NAME}} acts", 123)
            p2 = llm.propose(views.faction_view(s, 456),
                             "{{FACTION_NAME}} acts", 456)
    finally:
        llm.complete = saved_complete
    assert p1["target"] == "456" and p2["target"] == "123", \
        "targets canonicalize to text against raw ids"
    assert p1["actor"] == "123" and p2["actor"] == "456", \
        "actors leave validation as text too"
    saved_rng = resolve._rng
    resolve.set_rng(20261002)
    try:
        outcomes = resolve.resolve_tick([p1, p2], s)  # used to raise ValueError
    finally:
        # restore the RNG source for the suites that seed the global random
        # module (test_resolve's defense determinism) — set_rng swaps it in
        # for the whole process, and a leftover private Random breaks them
        resolve.set_rng(saved_rng)
    assert {o["actor"] for o in outcomes} == {"123", "456"}, \
        "the mutual contest resolved for both sides, in text"
    assert all(o["contested"] for o in outcomes), outcomes
    print("  seam: a numeric-id state runs propose->resolve consistently  OK")


def test_scalar_alias_still_attributes_notes_not_letters():
    """`aliases: {mint: ferrand}` (a scalar) used to join the alias
    letter-by-letter, so a 'Ferrand...' bullet matched nothing and was
    attributed to world. validate_state now refuses the shape for --live;
    the ingest read degrades to the one word anyway (extract runs on
    states the gate never saw)."""
    s = state(4, [{"id": "mint", "name": "The Mint", "goal": "coin",
                   "resources": {"coin": "low"}}],
              aliases={"mint": "ferrand"})
    props = ingest.extract("- Ferrand calls the customs debt in.\n", s)
    assert [p["actor"] for p in props] == ["mint"], \
        "the scalar alias matches as itself, never as its letters"
    print("  ingest: a scalar alias attributes by its word, not letters  OK")


def test_scalar_generic_words_filter_words_not_letters():
    """`generic_words: 'guildmaster port'` (a scalar) used to become its
    LETTERS, replacing the default filter with noise — 'The Guildmaster
    speaks' warned as an unknown actor. The read takes the words."""
    s = state(4, [faction("mint")])
    s["generic_words"] = "guildmaster port"
    warns = consistency.check(
        {"action": "The Guildmaster speaks with the Port officers."}, s, "mint")
    assert not [w for w in warns if w["kind"] == "unknown_actor"], warns
    print("  consistency: a scalar generic_words filters words, not letters  OK")


def test_null_disposition_degrades_in_the_view_not_a_traceback():
    """`disposition:` (null) on a rival used to AttributeError the views
    CLI and with it the studio's view route (the neighboring resources
    read already had or {}). The posture degrades to 'unknown'."""
    s = state(4, [faction("mint"), faction("rival", disposition=None)])
    view = views.faction_view(s, "mint")
    rival = {o["id"]: o for o in view["others"]}["rival"]
    assert rival["posture_toward_you"] == "unknown", rival
    # and the CLI itself exits 0 on that state, printing the view
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "state.yaml"
        path.write_text(yaml.safe_dump(s, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = views.main(["mint", "--state", str(path)])
    assert rc == 0, err.getvalue()
    assert "posture_toward_you: unknown" in out.getvalue()
    print("  views: a null disposition degrades to 'unknown', no traceback  OK")


def test_nameless_faction_shows_its_id_in_why():
    """A faction without `name` used to KeyError the why CLI and with it
    the studio's why route — the anchor (and a contest opponent) now shows
    the id instead."""
    s = {
        "factions": [{"id": "mint"}],  # no name at all
        "events": [
            {"week": 2, "actor": "mint",
             "text": "mint pressed the debt — succeeded "
                     "(contested vs bastard), margin +2",
             "vs": "bastard", "margin": 2},
        ],
    }
    lines = why.explain(s, 2, "mint")
    assert lines[0].startswith("WEEK 2 — mint: "), lines[0]
    assert lines[1].lstrip().startswith("contested against bastard"), lines[1]
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "state.yaml"
        path.write_text(yaml.safe_dump(s, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # main() takes the full argv (why_cli drops argv[0], exactly
            # as tick.py and the studio's subprocess call pass it)
            rc = why.main(["why.py", "2", "mint", "--state", str(path)])
    assert rc == 0, err.getvalue()
    assert "WEEK 2 — mint:" in out.getvalue()
    print("  why: a nameless faction shows its id, never a KeyError  OK")


def test_session_paths_refuse_null_faction_and_scalar_applied_effects():
    """AC (ticket 65): the session/apply paths validate_state never gated
    served hand-edited states with split personalities — `factions: [null]`
    TypeError'd apply_session_record's id-set build (raw traceback), while
    a scalar meta.applied_effects became its CHARACTERS in the dedup set
    and silently skipped the effect whose id is one of those characters
    ("applied 0 effect(s)", exit 0). Both now refuse loudly, naming the
    shape, on the library path and through the CLI."""
    record = {"source": "session:t", "week": 4, "events": [],
              "effects": [{"id": "a", "type": "set_resource",
                           "faction_id": "a", "resource": "coin",
                           "after": "high", "reason": "r",
                           "source": "session:t"}]}
    null_factions = state(4, [None])
    errs = effects.apply_session_record(dict(record), null_factions)[2]
    assert errs and any("factions entry 1" in e for e in errs), errs
    scalar_applied = state(4, [faction("a")])
    scalar_applied["meta"]["applied_effects"] = "a"  # the scalar hand edit
    errs = effects.apply_session_record(dict(record), scalar_applied)[2]
    assert errs and any("applied_effects" in e for e in errs), errs
    # and the refusal is LOUD through the CLI too — exit 1, "refusing:",
    # the shape named — where the scalar shape used to answer exit 0
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "state.yaml"
        path.write_text(yaml.safe_dump(scalar_applied, sort_keys=False,
                                       allow_unicode=True), encoding="utf-8")
        record_path = pathlib.Path(d) / "session.yaml"
        record_path.write_text(yaml.safe_dump(record, sort_keys=False),
                               encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = effects.main(["--apply", str(record_path),
                               "--state", str(path)])
    assert rc == 1, f"scalar applied_effects must refuse, got {rc}"
    assert "refusing:" in err.getvalue(), err.getvalue()
    assert "applied_effects" in err.getvalue(), err.getvalue()
    assert "valid:" not in out.getvalue(), out.getvalue()
    # the gate is not over-eager: the same record applies cleanly when the
    # state carries the documented shapes
    good = state(4, [faction("a")])
    events, applied, errs = effects.apply_session_record(dict(record), good)
    assert (events, applied, errs) == (0, 1, []), (events, applied, errs)
    assert good["factions"][0]["resources"]["coin"] == "high"
    print("  effects: null faction and scalar applied_effects refuse by "
          "name, never no-op  OK")


def test_missing_clock_refuses_the_read_paths_naming_it():
    """AC (ticket 65): tick's inspect print tolerated a missing meta for
    one line ("week ?") and then KeyError'd inside faction_view; ingest's
    extract and apply hard-indexed meta.clock.week outright. All the read
    paths now refuse through the shared gate, exit nonzero, naming
    meta.clock.week — and a null faction entry refuses views the same
    way (its view walk used to TypeError)."""
    clockless = state(4, [faction("mint")])
    del clockless["meta"]
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "state.yaml"
        path.write_text(yaml.safe_dump(clockless, sort_keys=False,
                                       allow_unicode=True), encoding="utf-8")
        assert read_shape_errors(clockless), "the gate itself must flag it"

        # tick's inspect path (reads the DEFAULT state file — relocate)
        saved_state_file = store.STATE_FILE
        store.STATE_FILE = path
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = tick.main([])
        finally:
            store.STATE_FILE = saved_state_file
        assert rc == 2, f"tick inspect must refuse a clockless state, got {rc}"
        assert "meta.clock.week" in err.getvalue(), err.getvalue()

        # the views CLI (explicit --state)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = views.main(["mint", "--state", str(path)])
        assert rc == 1, f"views must refuse a clockless state, got {rc}"
        assert "meta.clock.week" in err.getvalue(), err.getvalue()

        # ingest's notes path (extract dates the clock's week + 1)
        notes = pathlib.Path(d) / "notes.md"
        notes.write_text("- Ferrand calls the customs debt in.\n",
                         encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ingest.main([str(notes), "--state", str(path)])
        assert rc == 1, f"ingest extract must refuse, got {rc}"
        assert "refusing:" in err.getvalue() and \
            "meta.clock.week" in err.getvalue(), err.getvalue()

        # ingest's apply path — refused BEFORE the clock hard-index, and
        # the state file leaves unchanged
        before = path.read_text(encoding="utf-8")
        pending = pathlib.Path(d) / "pending.yml"
        pending.write_text("week: 5\nproposals:\n  - {week: 5, actor: world,"
                           " text: a quiet night}\n", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ingest.main(["--apply", str(pending), "--state", str(path)])
        assert rc == 1, f"ingest apply must refuse, got {rc}"
        assert "refusing:" in err.getvalue() and \
            "meta.clock.week" in err.getvalue(), err.getvalue()
        assert path.read_text(encoding="utf-8") == before, \
            "a refusal writes nothing"

        # a null faction entry refuses the views path by name too
        null_factions = state(4, [None])
        path.write_text(yaml.safe_dump(null_factions, sort_keys=False),
                        encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = views.main(["mint", "--state", str(path)])
        assert rc == 1, f"views must refuse a null faction, got {rc}"
        assert "factions entry 1" in err.getvalue(), err.getvalue()
    print("  read paths: a missing meta.clock refuses by name, no "
          "KeyError  OK")


if __name__ == "__main__":
    for fn in [
        test_validate_state_refuses_each_hand_edit_shape_naming_the_key,
        test_validate_state_accepts_the_documented_shapes,
        test_live_refuses_the_hand_edit_shapes_before_any_spend,
        test_numeric_id_state_resolves_consistently_past_the_gate,
        test_scalar_alias_still_attributes_notes_not_letters,
        test_scalar_generic_words_filter_words_not_letters,
        test_null_disposition_degrades_in_the_view_not_a_traceback,
        test_nameless_faction_shows_its_id_in_why,
        test_session_paths_refuse_null_faction_and_scalar_applied_effects,
        test_missing_clock_refuses_the_read_paths_naming_it,
    ]:
        fn()
    print("all hand-edit-tolerance tests passed")
