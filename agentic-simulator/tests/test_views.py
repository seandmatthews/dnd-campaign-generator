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


def test_facts_visibility_public_restricted_and_dm_only():
    """Phase 4: views carry only the facts a faction may see — public facts
    and facts addressed to THIS faction; dm_only, rival-addressed, and
    superseded facts never appear (the view is what the prompt embeds)."""
    state = mini_state()
    state["knowledge"] = [
        {"id": "fact-1", "text": "The bridge burned.",
         "visibility": "public", "known_to": []},
        {"id": "fact-2", "text": "Mint secret: the debt is forged.",
         "visibility": "restricted", "known_to": ["mint"]},
        {"id": "fact-3", "text": "Reliquary plan: audit the marsh ledgers.",
         "visibility": "restricted", "known_to": ["reliquary"]},
        {"id": "fact-4", "text": "Old wording of the bridge fact.",
         "visibility": "public", "known_to": [], "status": "superseded"},
        {"id": "fact-5", "text": "DM only note.", "visibility": "dm_only",
         "known_to": []},
    ]
    mine = views.faction_view(state, "mint")
    texts = " ".join(f["text"] for f in mine.get("facts_you_know", []))
    assert "The bridge burned." in texts            # public reaches everyone
    assert "the debt is forged" in texts            # addressed to mint
    assert "audit the marsh ledgers" not in texts   # a rival's restricted fact
    assert "Old wording" not in texts               # superseded facts are out
    assert "DM only note" not in texts              # dm_only stays with the DM

    theirs = views.faction_view(state, "reliquary")
    theirs_text = " ".join(f["text"] for f in theirs.get("facts_you_know", []))
    assert "The bridge burned." in theirs_text
    assert "the debt is forged" not in theirs_text  # mint's secret does not leak

    bare = mini_state()  # a campaign with no knowledge section still works
    assert "facts_you_know" not in views.faction_view(bare, "mint")


def test_rumors_window_keeps_recent_and_counts_the_omitted():
    """The prompt-growth fix: the view carries rumors heard inside
    RUMOR_WINDOW_WEEKS plus an honest `older_rumors_not_shown` count (the
    briefing-cap pattern). state.yaml keeps everything — spread, why, and
    the studio's rumor board read the full record; only the prompt shrinks."""
    state = mini_state()
    state["meta"]["clock"]["week"] = 20
    state["rumors"] = [
        {   # heard long ago (origin saw it at birth): outside the window
            "id": "rumor_0001", "born_week": 4, "origin": "mint",
            "claim": "An old claim, long stale.",
            "truth": True, "fidelity": "dead",
            "transmissions": [], "heard_by": ["mint"],
        },
        {   # heard last week: news, stays
            "id": "rumor_0002", "born_week": 19, "origin": "reliquary",
            "claim": "A fresh claim still circulating.",
            "truth": True, "fidelity": "eyewitness",
            "transmissions": [], "heard_by": ["mint", "reliquary"],
        },
    ]
    view = faction_view(state, "mint")
    claims = [r["claim"] for r in view["rumors_you_have_heard"]]
    assert claims == ["A fresh claim still circulating."], \
        f"only in-window rumors ride in the view: {claims}"
    assert view["older_rumors_not_shown"] == 1, \
        "the view says how many rumors were left out"
    assert len(state["rumors"]) == 2, "state keeps the full record"

    # nothing truncated -> no omission key at all (a young world is unchanged)
    young = faction_view(mini_state(), "mint")
    assert "older_rumors_not_shown" not in young
    assert [r["claim"] for r in young["rumors_you_have_heard"]] == \
        ["The Mint bought the harbor debt."]
    print("  window: stale rumors leave the view; the omitted count is honest  OK")


def test_rumors_window_boundary_week_still_rides():
    """The boundary is inclusive: a rumor heard at week - window + 1 (the
    oldest week inside the window) is still in the view; one week older
    drops out and increments the omitted count."""
    state = mini_state()
    week = 20
    state["meta"]["clock"]["week"] = week
    edge = week - views.RUMOR_WINDOW_WEEKS + 1
    state["rumors"] = [{
        "id": "rumor_0001", "born_week": edge, "origin": "mint",
        "claim": "Heard at the window's oldest week.",
        "truth": True, "fidelity": "hearsay",
        "transmissions": [], "heard_by": ["mint"],
    }]
    view = faction_view(state, "mint")
    assert [r["heard_week"] for r in view["rumors_you_have_heard"]] == [edge]
    assert "older_rumors_not_shown" not in view
    # one week older and it drops out with the count
    state["rumors"][0]["born_week"] = edge - 1
    view = faction_view(state, "mint")
    assert "rumors_you_have_heard" not in view
    assert view["older_rumors_not_shown"] == 1
    print("  window: the oldest in-window week rides; one older drops  OK")


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


def test_species_rides_views_when_present():
    """Ancestry is observable the way a kind is: a rival's `species` shows
    up in the others list, and the owner's whole-faction view carries it
    for free (the view deepcopies `you`). Legacy factions without one omit
    the key entirely — never a null in the prompt."""
    state = mini_state()
    state["factions"][0]["species"] = "dwarven"   # the mint
    state["factions"][2]["species"] = "troll"     # the marsh gang
    view = faction_view(state, "reliquary")
    others = {o["id"]: o for o in view["others"]}
    assert others["mint"]["species"] == "dwarven", others["mint"]
    assert others["marsh_gang"]["species"] == "troll", others["marsh_gang"]
    assert "species" not in view["you"], \
        "the viewer has no species of its own here"
    mine = faction_view(state, "mint")
    assert mine["you"]["species"] == "dwarven", "you carries the full faction"
    assert "species" not in others_of(faction_view(state, "marsh_gang"),
                                      "reliquary"), \
        "a faction without species is omitted, not null"
    print("  views: species rides others + you when present, omitted when not  OK")


def others_of(view, faction_id):
    return {o["id"]: o for o in view["others"]}[faction_id]


def test_faction_kind_may_be_omitted():
    """The studio's authoring form only writes `kind` when the DM fills it
    in, so a hand-named sandbox can hold kindless factions — the view (and
    with it every live week) must degrade to 'unknown', never KeyError."""
    state = mini_state()
    for f in state["factions"]:
        f.pop("kind", None)
    view = faction_view(state, "mint")
    assert all(o["kind"] == "unknown" for o in view["others"]), view["others"]
    print("  views: a kindless faction degrades to 'unknown', no KeyError  OK")


def test_faction_name_and_resources_may_be_omitted():
    """validate_state checks only `id`, so a hand-edited faction can lack
    name/resources too — the crash class this used to be is a week dying
    in faction_view AFTER earlier factions spent their LLM calls."""
    state = mini_state()
    for f in state["factions"]:
        f.pop("resources", None)
        f.pop("name", None)
    view = faction_view(state, "mint")
    assert len(view["others"]) == len(state["factions"]) - 1
    for o in view["others"]:
        assert o["name"], "a nameless faction falls back to its id"
        assert o["apparent_strength"] is not None, \
            "missing resources degrade like 'none' muscle"
    print("  views: a nameless, resourceless faction degrades, no KeyError  OK")


def test_state_default_points_at_campaign_data_root():
    import store
    assert views.STATE_FILE == store.DATA_ROOT / "engine-home" / "state.yaml", \
        "the default is the campaign-data engine home, not the cwd"
    print("  anchor: STATE_FILE resolves from __file__, cwd-independent  OK")


if __name__ == "__main__":
    for fn in [
        test_cli_prints_the_faction_view_as_yaml,
        test_cli_unknown_faction_exits_2,
        test_cli_default_path_reads_project_state,
        test_cli_usage_and_missing_state_errors,
        test_state_default_points_at_campaign_data_root,
        test_facts_visibility_public_restricted_and_dm_only,
        test_rumors_window_keeps_recent_and_counts_the_omitted,
        test_rumors_window_boundary_week_still_rides,
        test_faction_kind_may_be_omitted,
        test_faction_name_and_resources_may_be_omitted,
        test_species_rides_views_when_present,
    ]:
        fn()
    print("all views tests passed")
