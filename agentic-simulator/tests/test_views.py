"""Tests for the views CLI (src/views.py).

Zero dependencies — run directly:  python tests/test_views.py
faction_view() runs on a hand-built mini state; the CLI runs against a temp
state file passed with --state. The default-path test points store.STATE_FILE
at a frozen fixture copy — world/state.yaml itself is live scratch the
studio rewrites on every week run, so the suite must not depend on it
(found 2026-09-23: the suite went red whenever the engine home held a
different campaign than the one these expectations were written for).
"""

import contextlib
import io
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import store
import views
from views import faction_view, main

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "ruravaqan-state.yaml"


def mini_state():
    """Three factions, a clock, and one rumor reliquary heard second-hand."""
    return {
        "meta": {"clock": {"week": 4, "season": "storm"}},
        "city": {"name": "Testburg", "districts": ["harbor", "marsh"]},
        "factions": [
            {"id": "mint", "name": "House Ferrand (the Mint)", "kind": "guild",
             "resources": {"muscle": "medium", "coin": "high"},
             "goal": "rank the debt senior",
             "disposition": {"reliquary": "hostile"}},
            {"id": "reliquary", "name": "The Reliquary", "kind": "church",
             "resources": {"muscle": "low"},
             "goal": "collect the arrears",
             "disposition": {"mint": "hostile"}},
            {"id": "marsh_gang", "name": "The Marsh Gang", "kind": "gang",
             "resources": {"muscle": "high"}, "goal": "steal"},
        ],
        "events": [],
        "rumors": [{
            "id": "rumor_0001", "born_week": 3, "origin": "mint",
            "claim": "The Mint bought the harbor debt.",
            "truth": True, "fidelity": "hearsay",
            "transmissions": [{"week": 4, "from": "mint", "to": "reliquary",
                               "fidelity_before": "reliable",
                               "fidelity_after": "hearsay"}],
            "heard_by": ["mint", "reliquary"],
        }],
    }


def temp_state_file(state, directory):
    path = pathlib.Path(directory) / "state.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(state, f, sort_keys=False, allow_unicode=True, width=100)
    return path


def test_cli_prints_the_faction_view_as_yaml():
    with tempfile.TemporaryDirectory() as d:
        path = temp_state_file(mini_state(), d)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["mint", "--state", str(path)])
    assert rc == 0, err.getvalue()
    parsed = yaml.safe_load(out.getvalue())
    assert parsed == faction_view(mini_state(), "mint"), \
        "CLI output round-trips to exactly faction_view()"
    assert parsed["you"]["id"] == "mint" and parsed["week"] == 4
    assert parsed["rumors_you_have_heard"][0]["fidelity"] == "eyewitness", \
        "the originator heard its own rumor at full fidelity"
    print("  cli: --state run prints YAML equal to faction_view()  OK")


def test_cli_unknown_faction_exits_2():
    with tempfile.TemporaryDirectory() as d:
        path = temp_state_file(mini_state(), d)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["ghost", "--state", str(path)])
    assert rc == 2, "unknown faction exits 2"
    assert "ghost" in err.getvalue() and "unknown" in err.getvalue()
    assert out.getvalue() == "", "nothing is written to stdout"
    print("  guard: unknown faction -> stderr message, exit 2, empty stdout  OK")


def test_cli_default_path_reads_project_state():
    """No --state routes through store's default STATE_FILE — verified by
    pointing that default at a fixture copy, never at the live scratch."""
    with tempfile.TemporaryDirectory() as d:
        target = pathlib.Path(d) / "state.yaml"
        target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        original = store.STATE_FILE
        store.STATE_FILE = target
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = main(["mint"])
        finally:
            store.STATE_FILE = original
    assert rc == 0, err.getvalue()
    real = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    parsed = yaml.safe_load(out.getvalue())
    assert parsed == faction_view(real, "mint"), \
        "with no --state the view comes from store's default state file"
    print("  default: no --state reads store's STATE_FILE default  OK")


def test_cli_usage_and_missing_state_errors():
    with tempfile.TemporaryDirectory() as d:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            assert main([]) == 2, "no faction id is a usage error"
            assert main(["mint", "reliquary"]) == 2, "two ids is a usage error"
            assert main(["mint", "--state"]) == 2, "--state without a value is a usage error"
            assert main(["mint", "--state", str(pathlib.Path(d) / "nope.yaml")]) == 1, \
                "unreadable state file exits 1"
    print("  errors: usage -> 2, unreadable state -> 1  OK")


def test_state_default_points_at_project_root():
    assert views.STATE_FILE == ROOT / "world" / "state.yaml", \
        "the default is anchored to the project root, not the cwd"
    print("  anchor: STATE_FILE resolves from __file__, cwd-independent  OK")


if __name__ == "__main__":
    for fn in [
        test_cli_prints_the_faction_view_as_yaml,
        test_cli_unknown_faction_exits_2,
        test_cli_default_path_reads_project_state,
        test_cli_usage_and_missing_state_errors,
        test_state_default_points_at_project_root,
    ]:
        fn()
    print("all views tests passed")
