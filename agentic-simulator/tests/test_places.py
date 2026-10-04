"""Tests for multi-place simulation: the `places` schema, faction `place`
assignment, and what each level sees.

Two tiers in one state — a burg's factions and world-level factions —
share the flat roster, the scheduler, the resolver, and one chronicle.
Zero dependencies — run directly:  python tests/test_places.py
"""

import contextlib
import io
import pathlib
import shutil
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import resolve
import scheduler
import tick
from goals import digest
from store import faction_place, place_words, places_of, validate_state
from views import faction_view


def faction(fid, place=None, name=None):
    f = {
        "id": fid,
        "name": name or fid.title(),
        "kind": "test",
        "goal": "test",
        "resources": {"coin": "medium", "muscle": "low", "legitimacy": "high"},
        "disposition": {},
    }
    if place:
        f["place"] = place
    return f


def legacy_state(week=4):
    """The single-city shape every pre-places campaign has on disk."""
    return {
        "meta": {"clock": {"week": week}},
        "city": {"name": "Testburg", "population": 12000,
                 "districts": ["the Mint quarter"]},
        "factions": [faction("mint"), faction("rival")],
        "events": [], "rumors": [],
    }


def multi_state(week=4):
    """Two tiers: a burg, a second burg, and the world they sit in."""
    return {
        "meta": {"clock": {"week": week}},
        "places": [
            {"id": "weirham", "name": "Weirham", "kind": "burg",
             "districts": ["the Staithe"]},
            {"name": "Harmouth"},
            {"id": "sweth", "name": "the Kingdom of Sweth", "kind": "nation"},
        ],
        "factions": [
            faction("weir_wardens", place="weirham", name="The Weir Wardens"),
            faction("harmouth_guild", place="harmouth", name="The Harmouth Guild"),
            faction("sweth_crown", place="sweth", name="The Crown of Sweth"),
            faction("mint", name="The Mint"),
        ],
        "events": [], "rumors": [],
    }


def test_places_of_legacy_city_derives_one_place():
    places = places_of(legacy_state())
    assert places == [{"name": "Testburg", "population": 12000,
                       "districts": ["the Mint quarter"],
                       "id": "testburg", "kind": "city"}], \
        "the legacy city becomes the primary place; the file is untouched"
    print("  places_of: the legacy city shape derives one primary place  OK")


def test_places_of_explicit_list_fills_missing_ids_and_kinds():
    places = places_of(multi_state())
    assert [p["id"] for p in places] == ["weirham", "harmouth", "sweth"]
    assert places[1]["id"] == "harmouth", "an idless place derives from its name"
    assert places[1]["kind"] == "place", "an explicit list defaults kind to place"
    assert places[0]["kind"] == "burg", "a declared kind is kept"
    print("  places_of: explicit list fills missing ids and kinds  OK")


def test_places_of_empty_states_yield_no_places():
    assert places_of({}) == []
    s = {"places": [], "city": {"name": "X"}}
    assert places_of(s)[0]["name"] == "X", \
        "an empty places list falls back to the legacy city, not to nothing"
    print("  places_of: no places is an empty list, not a guess  OK")


def test_validate_state_accepts_both_shapes_and_refuses_bad_references():
    validate_state(legacy_state())   # legacy: must not raise
    validate_state(multi_state())    # multi-place: must not raise
    s = multi_state()
    s["factions"][0]["place"] = "nowhere"
    try:
        validate_state(s)
    except ValueError as e:
        assert "nowhere" in str(e) and "weirham" in str(e), \
            f"a typo'd place is refused with the valid ids named: {e}"
    else:
        raise AssertionError("an unknown faction.place must be refused")
    s = multi_state()
    s["places"].append({"name": "Weirham", "kind": "burg"})
    try:
        validate_state(s)
    except ValueError as e:
        assert "weirham" in str(e), f"duplicate place ids are refused: {e}"
    else:
        raise AssertionError("two places collapsing to one id must be refused")
    s = legacy_state()
    del s["city"]
    try:
        validate_state(s)
    except ValueError as e:
        assert "places" in str(e), f"a placeless state is refused: {e}"
    else:
        raise AssertionError("a state with no places at all must be refused")
    print("  validate: both shapes spend; bad place references are refused  OK")


def test_faction_place_resolves_and_defaults_to_primary():
    s = multi_state()
    assert faction_place(s, {"id": "x", "place": "harmouth"})["id"] == "harmouth"
    assert faction_place(s, {"id": "x"})["id"] == "weirham", \
        "a faction naming no place operates from the primary place"
    assert faction_place(s, None)["id"] == "weirham", \
        "an odd (non-dict) faction degrades to the primary, never KeyErrors"
    assert faction_place({}, {"id": "x"}) == {}, \
        "a state with no places yields an empty place, not a crash"
    print("  faction_place: explicit place wins; blank means primary  OK")


def test_place_words_cover_every_place_and_optionally_districts():
    s = multi_state()
    words = place_words(s)
    assert {"weirham", "harmouth", "sweth", "kingdom"} <= words
    assert "staithe" in words, "district words are included by default"
    names_only = place_words(s, include_districts=False)
    assert "staithe" not in names_only, \
        "ingest keeps district words matchable (the Mint is a faction's name)"
    print("  place_words: every place; districts only on request  OK")


def test_views_show_your_place_and_rival_places():
    s = multi_state()
    v = faction_view(s, "weir_wardens")
    assert v["your_place"]["id"] == "weirham"
    assert v["your_place"]["districts"] == ["the Staithe"], \
        "your own place carries its full detail"
    assert [p["id"] for p in v["other_places"]] == ["harmouth", "sweth"], \
        "other places appear only as public identities"
    assert "districts" not in v["other_places"][0]
    by_id = {o["id"]: o for o in v["others"]}
    assert by_id["sweth_crown"]["place"] == "the Kingdom of Sweth"
    assert by_id["harmouth_guild"]["place"] == "Harmouth"
    # a world-tier faction sees the burgs as summaries; its own place is thin
    crown = faction_view(s, "sweth_crown")
    assert crown["your_place"]["kind"] == "nation"
    assert "districts" not in crown["your_place"]
    assert {p["id"] for p in crown["other_places"]} == {"weirham", "harmouth"}
    # the legacy shape still views fine — one place, no others section
    legacy = faction_view(legacy_state(), "mint")
    assert legacy["your_place"]["name"] == "Testburg"
    assert "other_places" not in legacy
    print("  views: your place in full, other places as identities  OK")


def test_scheduler_place_mentions_do_not_activate_similar_factions():
    s = multi_state()
    # last week an event merely MENTIONED Weirham (the place) — the Weir
    # Wardens are named after it, but a place mention is not being targeted
    s["events"] = [{"week": 4, "actor": "sweth_crown",
                    "text": "The Crown of Sweth mustered outside Weirham.",
                    "outcome": "succeeded", "contested": False, "margin": 2}]
    act = scheduler.activation(s)
    assert act["weir_wardens"]["act"] is False, \
        "a mention of the place must not activate the faction named after it"
    assert act["sweth_crown"]["act"] is True, "the actor acts again"
    # a vs-target still activates across levels: the crown was targeted
    s["events"] = [{"week": 4, "actor": "weir_wardens",
                    "text": "The wardens refused the crown's levy.",
                    "contested": True, "vs": "sweth_crown",
                    "outcome": "succeeded", "margin": 1}]
    act = scheduler.activation(s)
    assert act["sweth_crown"]["act"] is True, \
        "a burg faction targeting a world faction touches it"
    print("  scheduler: place mentions stay inert; vs-targets cross levels  OK")


def test_resolver_accepts_district_targets_from_any_place():
    s = multi_state()
    ids, districts = resolve._known_targets(s)
    assert "the Staithe" in districts, "the primary place's districts resolve"
    assert ids == {"weir_wardens", "harmouth_guild", "sweth_crown", "mint",
                   "world", "self"}
    legacy_ids, legacy_districts = resolve._known_targets(legacy_state())
    assert legacy_ids == {"mint", "rival", "world", "self"}
    assert legacy_districts == {"the Mint quarter"}
    print("  resolve: district targets from every place resolve  OK")


def test_goal_digest_carries_all_places():
    s = multi_state()
    assert digest(s)["places"] == ["Weirham", "Harmouth",
                                   "the Kingdom of Sweth"]
    assert digest(legacy_state())["places"] == ["Testburg"]
    print("  goals: the digest names every simulated place  OK")


def test_place_label_names_every_place():
    assert tick.place_label_of(multi_state()) == \
        "Weirham, Harmouth, the Kingdom of Sweth"
    assert tick.place_label_of(legacy_state()) == "Testburg"
    print("  label: the chronicle label joins every place  OK")


def test_run_live_multi_place_week_writes_one_chronicle():
    """The two-tier proof: burg factions and a world faction in one week —
    cross-level contest, one clock, one chronicle labeled with the places."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="tick-places-"))
    saved = (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR,
             tick.REPLAY_DIR, tick.llm.propose, tick.llm.narrate_week,
             tick.save_state, resolve._rng)
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    tick.CHRONICLE_DIR = root / "chronicle"
    tick.REPLAY_DIR = root / "replay"
    real_save_state = tick.save_state
    tick.save_state = lambda doc, path=None: real_save_state(
        doc, path or tick.STATE_FILE)
    s = multi_state(week=4)
    # week 4 already happened: the crown levied the wardens and lost, so
    # BOTH contest participants are "targeted last week" and must act —
    # while the two untouched factions (the other burg's guild, the city
    # mint) sit the week out. That asymmetry IS the two-tier mechanic.
    s["events"] = [{"week": 4, "actor": "sweth_crown",
                    "text": "The Crown of Sweth levied Weirham's weir tolls.",
                    "contested": True, "vs": "weir_wardens",
                    "outcome": "failed", "margin": -2}]
    tick.STATE_FILE.write_text(yaml.safe_dump(s), encoding="utf-8")

    def fake_propose(view, template, actor):
        if actor == "sweth_crown":
            return {"actor": actor,
                    "action": "The Crown of Sweth levies Weirham's weir tolls",
                    "target": "weir_wardens",
                    "resources_committed": {"coin": "medium"},
                    "risk_acceptance": "medium"}
        return {"actor": actor,
                "action": f"{actor} holds its ground quietly",
                "target": "self",
                "resources_committed": {},
                "risk_acceptance": "low"}

    tick.llm.propose = fake_propose
    tick.llm.narrate_week = lambda *a, **k: "TITLE: The Levy Week\nThey levied."
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tick.run_live(s)
        out = buf.getvalue()
        after = yaml.safe_load(tick.STATE_FILE.read_text(encoding="utf-8"))
        assert after["meta"]["clock"]["week"] == 5
        actors = {e["actor"] for e in after["events"]}
        assert {"sweth_crown", "weir_wardens"} <= actors, \
            f"the contested pair from both tiers acted (got {actors})"
        assert "harmouth_guild" not in actors and "mint" not in actors, \
            "untouched factions in other places held"
        assert "harmouth_guild" in out and "HOLDS" in out, \
            "the holds are visible in the week's stdout (the studio contract)"
        contested = [e for e in after["events"] if e.get("vs") == "weir_wardens"]
        assert contested, "the crown's levy contested the wardens cross-level"
        chron = list(tick.CHRONICLE_DIR.glob("0005-*.md"))
        assert chron and "The Levy Week" in chron[0].read_text(encoding="utf-8")
        ok, msg = tick.replay_verify(tick.REPLAY_DIR / "week-5.yaml")
        assert ok, f"the multi-place week must replay: {msg}"
    finally:
        # restore resolve's RNG too: run_live set_rng()'d a per-week Random
        # instance, and a leaked instance silently deafens later tests'
        # random.seed() (it bit test_resolve, which runs right after this
        # file alphabetically)
        (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR, tick.REPLAY_DIR,
         tick.llm.propose, tick.llm.narrate_week, tick.save_state,
         resolve._rng) = saved
        shutil.rmtree(root, ignore_errors=True)
    print("  live: a two-tier week resolves, chronicles, and replays  OK")


if __name__ == "__main__":
    for fn in [
        test_places_of_legacy_city_derives_one_place,
        test_places_of_explicit_list_fills_missing_ids_and_kinds,
        test_places_of_empty_states_yield_no_places,
        test_validate_state_accepts_both_shapes_and_refuses_bad_references,
        test_faction_place_resolves_and_defaults_to_primary,
        test_place_words_cover_every_place_and_optionally_districts,
        test_views_show_your_place_and_rival_places,
        test_scheduler_place_mentions_do_not_activate_similar_factions,
        test_resolver_accepts_district_targets_from_any_place,
        test_goal_digest_carries_all_places,
        test_place_label_names_every_place,
        test_run_live_multi_place_week_writes_one_chronicle,
    ]:
        fn()
    print("all places tests passed")
