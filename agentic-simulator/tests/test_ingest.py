"""Smoke tests for session-notes ingestion.

Zero dependencies — run directly:  python tests/test_ingest.py
Reads tests/fixtures/ruravaqan-state.yaml (a frozen copy of the Ruravaqan
world the expectations were written against); apply() runs against temp
copies only. Never world/state.yaml — that is live scratch the studio
overwrites on every week run, and reading it made this suite red whenever
the engine home held a different campaign (found 2026-09-23).
"""

import contextlib
import io
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import ingest
from ingest import apply, extract, main, read_pending, write_pending

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = yaml.safe_load(
    (ROOT / "tests" / "fixtures" / "ruravaqan-state.yaml").read_text(encoding="utf-8"))


def notes(*lines):
    return "\n".join(lines) + "\n"


def temp_state_copy(directory):
    """The real state structure, dumped to a temp file apply() may write."""
    path = pathlib.Path(directory) / "state.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(STATE, f, sort_keys=False, allow_unicode=True, width=100)
    return path


def test_bullets_become_proposals_and_prose_is_ignored():
    text = notes(
        "# Session 12",
        "The DM forgot the initiative order again.",  # prose: ignored
        "- The Mint buys the harbor debt.",           # "- " bullet
        "* House Ferrand forecloses on the pier.",    # "* " bullet
        "2. Sera petitions the council.",             # numbered bullet
        "",                                           # empty line: ignored
        "- ",                                         # empty bullet: ignored
        "plain line, no marker",                      # ignored
    )
    props = extract(text, STATE)
    assert [p["text"] for p in props] == [
        "The Mint buys the harbor debt.",
        "House Ferrand forecloses on the pier.",
        "Sera petitions the council.",
    ]
    print("  bullets only: -/* and numbered lines become proposals, prose ignored  OK")


def test_actor_resolution_hits_all_five_factions():
    cases = [
        ("- House Ferrand calls in the customs debt.", "mint"),
        ("- The Mint's watchers patrol the manor.", "mint"),
        ("- The Reliquary names debtor families from the pulpit.", "reliquary"),
        ("- Sera reads her mother's letters.", "bastard"),
        ("- The Bastard demands a seat on the council.", "bastard"),
        ("- The Longshoremen hold the strike another week.", "longshoremen"),
        ("- The Marsh Gang watches the funeral route.", "marsh_gang"),
    ]
    props = extract(notes(*(line for line, _ in cases)), STATE)
    assert len(props) == len(cases)
    for p, (line, actor) in zip(props, cases):
        assert p["actor"] == actor, f"{line!r}: got {p['actor']!r}, want {actor!r}"
    print("  actor hits: Ferrand/mint, Reliquary, Sera/Bastard, Longshoremen, Marsh Gang  OK")


def test_unmatched_lines_get_world():
    props = extract(
        notes("- The council ranks blood below paper.", "* A fog rolls in off the harbor."),
        STATE,
    )
    assert len(props) == 2
    assert all(p["actor"] == "world" for p in props)
    print("  unmatched: no faction words -> actor world  OK")


def test_proposed_week_is_current_plus_one():
    props = extract(notes("- Anything at all."), STATE)
    assert props[0]["week"] == STATE["meta"]["clock"]["week"] + 1, "proposals date the next week to be simulated"
    print("  week: proposals dated current clock week + 1  OK")


def test_write_read_round_trip():
    props = extract(notes("- The Mint buys the debt.", "- A storm strands the fleet."), STATE)
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "pending.yml"
        write_pending(props, path)
        back = read_pending(path)
    assert back["week"] == props[0]["week"]
    assert back["proposals"] == [{"actor": p["actor"], "text": p["text"]} for p in props]
    print("  round trip: write_pending -> read_pending preserves actor and text  OK")


def test_apply_appends_to_temp_copy_and_counts():
    props = extract(notes("- The Mint buys the debt.", "- A storm strands the fleet."), STATE)
    week = props[0]["week"]
    with tempfile.TemporaryDirectory() as d:
        path = temp_state_copy(d)
        pending = {"week": week, "proposals": [{"actor": p["actor"], "text": p["text"]} for p in props]}
        n = apply(pending, path)
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert n == 2, f"apply returned {n}, want 2"
    assert len(after["events"]) == len(STATE["events"]) + 2
    assert after["meta"]["clock"] == STATE["meta"]["clock"], "clock untouched"
    for evt, p in zip(after["events"][-2:], props):
        assert evt == {"week": week, "actor": p["actor"], "text": p["text"]}
    print("  apply: appends to a copy, returns the count, clock untouched  OK")


def test_apply_refuses_unknown_actor_without_writing():
    with tempfile.TemporaryDirectory() as d:
        path = temp_state_copy(d)
        original = path.read_bytes()
        pending = {"week": 4, "proposals": [{"actor": "council", "text": "The council rules."}]}
        n = apply(pending, path)
        assert n == -1, f"apply returned {n}, want -1"
        assert path.read_bytes() == original, "refusal must not write"
    print("  guard: unknown actor refused with -1, state unwritten  OK")


def test_merged_event_keys_are_exactly_week_actor_text():
    with tempfile.TemporaryDirectory() as d:
        path = temp_state_copy(d)
        pending = {"week": 4, "proposals": [{"actor": "world", "text": "A storm."}]}
        assert apply(pending, path) == 1
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(after["events"][-1].keys()) == {"week", "actor", "text"}
    print("  shape: merged events carry exactly week/actor/text  OK")


def test_cli_state_flag_applies_into_target_and_leaves_default_alone():
    with tempfile.TemporaryDirectory() as d:
        target = temp_state_copy(d)
        pending = pathlib.Path(d) / "pending.yml"
        props = extract(notes("- The Mint buys the debt."), STATE)
        write_pending(props, pending)
        default_before = (ROOT / "world" / "state.yaml").read_bytes()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--apply", str(pending), "--state", str(target)])
        assert rc == 0, "apply with --state exits 0"
        assert buf.getvalue() == "merged 1 events\n"
        after = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert after["events"][-1]["actor"] == "mint", "event landed in the target file"
        assert (ROOT / "world" / "state.yaml").read_bytes() == default_before, \
            "the default world/state.yaml must be untouched"
    print("  --state: apply lands in the given file, default state.yaml untouched  OK")


def test_cli_state_flag_position_and_equals_form():
    with tempfile.TemporaryDirectory() as d:
        target_a = temp_state_copy(d)
        target_b = pathlib.Path(d) / "b.yaml"
        target_b.write_text(target_a.read_text(encoding="utf-8"), encoding="utf-8")
        pending = pathlib.Path(d) / "pending.yml"
        props = extract(notes("- A storm strands the fleet."), STATE)
        write_pending(props, pending)
        assert main(["--state", str(target_a), "--apply", str(pending)]) == 0, \
            "--state may come before --apply"
        assert main(["--apply", str(pending), f"--state={target_b}"]) == 0, \
            "--state=PATH works too"
        assert yaml.safe_load(target_a.read_text(encoding="utf-8"))["events"][-1]["actor"] == "world"
        assert yaml.safe_load(target_b.read_text(encoding="utf-8"))["events"][-1]["actor"] == "world"
    print("  --state: flag order free, --state=PATH accepted  OK")


def test_cli_without_state_flag_targets_the_fixed_default():
    """Back-compat: no --state means exactly the old fixed default — verified by
    pointing ingest.STATE_FILE at a temp copy, never at the real file."""
    with tempfile.TemporaryDirectory() as d:
        target = temp_state_copy(d)
        pending = pathlib.Path(d) / "pending.yml"
        props = extract(notes("- A fog settles over the harbor."), STATE)
        write_pending(props, pending)
        original = ingest.STATE_FILE
        ingest.STATE_FILE = pathlib.Path(target)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["--apply", str(pending)])
        finally:
            ingest.STATE_FILE = original
        assert rc == 0, buf.getvalue()
        after = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert after["events"][-1]["actor"] == "world", "default path got the apply"
    print("  default: no --state still routes at the fixed world/state.yaml  OK")


if __name__ == "__main__":
    for fn in [
        test_bullets_become_proposals_and_prose_is_ignored,
        test_actor_resolution_hits_all_five_factions,
        test_unmatched_lines_get_world,
        test_proposed_week_is_current_plus_one,
        test_write_read_round_trip,
        test_apply_appends_to_temp_copy_and_counts,
        test_apply_refuses_unknown_actor_without_writing,
        test_merged_event_keys_are_exactly_week_actor_text,
        test_cli_state_flag_applies_into_target_and_leaves_default_alone,
        test_cli_state_flag_position_and_equals_form,
        test_cli_without_state_flag_targets_the_fixed_default,
    ]:
        fn()
    print("all ingest tests passed")
