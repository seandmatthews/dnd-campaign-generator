"""Smoke tests for the rumor subsystem.

Zero dependencies — run directly:  python tests/test_rumors.py
"""

import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from rumors import spawn_from_events, spread, heard


def small_state(faction_ids=("a", "b", "c", "d"), week=0):
    return {
        "meta": {"clock": {"week": week}},
        "factions": [{"id": fid} for fid in faction_ids],
        "events": [],
        "rumors": [],
    }


def fresh_rumor(week, origin, fidelity="eyewitness", heard_by=None):
    return {
        "id": "rumor_9001",
        "born_week": week,
        "origin": origin,
        "claim": "test claim",
        "truth": True,
        "fidelity": fidelity,
        "transmissions": [],
        "heard_by": list(heard_by if heard_by is not None else [origin]),
    }


def run_week(state, new_events):
    """The wiring tick.py will use: the clock advances, live rumors spread in
    the new week, then the week's events land and spawn rumors — which start
    spreading next week."""
    state["meta"]["clock"]["week"] += 1
    state["events"].extend(new_events)
    spread(state, state["rumors"])
    state["rumors"].extend(spawn_from_events(state))


def test_spawn_one_rumor_per_faction_event():
    state = small_state(week=2)
    state["events"] = [
        {"week": 1, "actor": "a", "text": "old news from last week"},
        {"week": 2, "actor": "b", "text": "B bribes the harbormaster — failed, margin -1"},
        {"week": 2, "actor": "world", "text": "A storm rakes the harbor."},
        {"week": 2, "actor": "c", "text": "C buys grain — succeeded, margin +1"},
    ]
    spawned = spawn_from_events(state)
    assert [r["id"] for r in spawned] == ["rumor_0001", "rumor_0002"], "one per faction event"
    assert [r["origin"] for r in spawned] == ["b", "c"], "no rumor for the world event"
    assert all(r["born_week"] == 2 and r["fidelity"] == "eyewitness" and r["truth"] is True
               for r in spawned)
    assert [r["heard_by"] for r in spawned] == [["b"], ["c"]], "the actor saw it happen"
    assert spawned[0]["claim"] == "B bribes the harbormaster — failed, margin -1"
    assert all(r["transmissions"] == [] for r in spawned)
    assert state["rumors"] == [], "spawn must not mutate state — caller appends"
    print("  spawn: one rumor per resolved faction event, none for world  OK")


def test_spawn_ids_continue_existing_rumors():
    state = small_state(week=1)
    state["rumors"] = [{"id": "rumor_0001"}]
    state["events"] = [{"week": 1, "actor": "a", "text": "A raises the watch — succeeded, margin +2"}]
    spawned = spawn_from_events(state)
    assert [r["id"] for r in spawned] == ["rumor_0002"], "ids continue the sequence"
    print("  spawn: ids continue the existing rumor sequence  OK")


def test_first_spread_drops_grade_to_alphabetical_first():
    state = small_state(week=5)
    r = fresh_rumor(1, "d")
    spread(state, [r])
    assert r["fidelity"] == "reliable", "one grade down per telling"
    assert r["heard_by"] == ["d", "a"], "alphabetically-first not-yet-heard faction is told"
    assert r["transmissions"] == [{
        "week": 5, "from": "d", "to": "a",
        "fidelity_before": "eyewitness", "fidelity_after": "reliable",
    }]
    assert r["last_spread_week"] == 5, "spread stamps the week it ran"
    print("  first spread: eyewitness->reliable, told to first non-heard  OK")


def test_spread_idempotent_within_week():
    state = small_state(week=5)
    r = fresh_rumor(1, "d")
    spread(state, [r])
    once = copy.deepcopy(r)
    spread(state, [r])
    spread(state, [r])
    assert r == once, "last_spread_week guard: repeated calls in one week change nothing"
    print("  idempotent: repeated spread in one week is a no-op  OK")


def test_three_spreads_kill_a_rumor():
    state = small_state(faction_ids=("a", "b", "c", "d", "e"))
    r = fresh_rumor(1, "e")
    rumors = [r]
    for week in (2, 3, 4):
        state["meta"]["clock"]["week"] = week
        spread(state, rumors)
    assert r["fidelity"] == "dead", "eyewitness->reliable->hearsay->dead"
    assert r["heard_by"] == ["e", "a", "b"], "the killing telling adds no listener"
    assert len(r["transmissions"]) == 3
    state["meta"]["clock"]["week"] = 5
    spread(state, rumors)
    assert len(r["transmissions"]) == 3 and r["heard_by"] == ["e", "a", "b"], \
        "dead rumors stop moving"
    print("  three spreads: eyewitness->reliable->hearsay->dead, then still  OK")


def test_dead_telling_does_not_add_listener():
    state = small_state(faction_ids=("a", "b"), week=7)
    r = fresh_rumor(1, "a", fidelity="hearsay")
    spread(state, [r])
    assert r["fidelity"] == "dead"
    assert r["heard_by"] == ["a"], "a dead telling is a failed retelling"
    assert r["transmissions"] == [{
        "week": 7, "from": "a", "to": "b",
        "fidelity_before": "hearsay", "fidelity_after": "dead",
    }]
    assert heard(state, "b") == [], "the dead-told faction holds nothing"
    print("  dead telling: attempt recorded, listener never holds it  OK")


def test_no_audience_no_decay():
    state = small_state()
    r = fresh_rumor(1, "a", fidelity="hearsay", heard_by=["a", "b", "c", "d"])
    spread(state, [r])
    assert r["fidelity"] == "hearsay" and r["transmissions"] == [], \
        "nobody left to tell: no retelling, no decay"
    print("  no audience: nobody to tell, no retelling, no decay  OK")


def test_heard_only_held_rumors_with_source_hints():
    state = small_state(week=5)
    state["rumors"] = [
        {"id": "rumor_0001", "born_week": 1, "origin": "a", "claim": "claim one", "truth": True,
         "fidelity": "reliable", "transmissions": [], "heard_by": ["a"]},
        {"id": "rumor_0002", "born_week": 2, "origin": "c", "claim": "claim two", "truth": True,
         "fidelity": "reliable",
         "transmissions": [{"week": 4, "from": "c", "to": "b",
                            "fidelity_before": "eyewitness", "fidelity_after": "reliable"}],
         "heard_by": ["c", "b"]},
    ]
    assert heard(state, "b") == [
        {"claim": "claim two", "fidelity": "reliable", "heard_week": 4, "source_hint": "c"},
    ], "only rumors you hold, hinted by the teller"
    assert heard(state, "a") == [
        {"claim": "claim one", "fidelity": "eyewitness", "heard_week": 1, "source_hint": "the street"},
    ], "the origin heard it the week it was born"
    assert heard(state, "d") == []
    print("  heard: only held rumors; street for origin, teller otherwise  OK")


def test_full_pipeline_three_weeks():
    """Hand-worked: four factions; week 1 has two events (one faction, one
    world), weeks 2 and 3 one faction event each. Expected rumors embedded
    below exactly as the rules produce them:
      week 2: r1 told a->b (eyewitness->reliable); r2 born (b) from a FAILED
              action — quiet at its origin: it does not spread
      week 3: r1 told a->c (reliable->hearsay); r2 stamps its spread week but
              has no willing teller; r3 born (c), spreads starting week 4."""
    state = small_state()
    run_week(state, [
        {"week": 1, "actor": "a", "text": "A raises the watch — succeeded, margin +2"},
        {"week": 1, "actor": "world", "text": "A storm rakes the harbor."},
    ])
    run_week(state, [
        {"week": 2, "actor": "b", "text": "B bribes the harbormaster — failed, margin -1"},
    ])
    run_week(state, [
        {"week": 3, "actor": "c", "text": "C buys grain — succeeded, margin +1"},
    ])

    expected = [
        {
            "id": "rumor_0001",
            "born_week": 1,
            "origin": "a",
            "claim": "A raises the watch — succeeded, margin +2",
            "truth": True,
            "fidelity": "hearsay",
            "transmissions": [
                {"week": 2, "from": "a", "to": "b",
                 "fidelity_before": "eyewitness", "fidelity_after": "reliable"},
                {"week": 3, "from": "a", "to": "c",
                 "fidelity_before": "reliable", "fidelity_after": "hearsay"},
            ],
            "heard_by": ["a", "b", "c"],
            "last_spread_week": 3,
        },
        {
            "id": "rumor_0002",
            "born_week": 2,
            "origin": "b",
            "claim": "B bribes the harbormaster — failed, margin -1",
            "truth": True,
            "fidelity": "eyewitness",
            "transmissions": [],
            "heard_by": ["b"],
            "quiet": ["b"],
            "last_spread_week": 3,
        },
        {
            "id": "rumor_0003",
            "born_week": 3,
            "origin": "c",
            "claim": "C buys grain — succeeded, margin +1",
            "truth": True,
            "fidelity": "eyewitness",
            "transmissions": [],
            "heard_by": ["c"],
        },
    ]
    assert state["rumors"] == expected, "three hand-worked weeks"

    assert heard(state, "a") == [
        {"claim": "A raises the watch — succeeded, margin +2", "fidelity": "eyewitness",
         "heard_week": 1, "source_hint": "the street"},
    ], "origin first; the failed bribe stays at its origin, a never hears it"
    assert heard(state, "c") == [
        {"claim": "A raises the watch — succeeded, margin +2", "fidelity": "hearsay",
         "heard_week": 3, "source_hint": "a"},
        {"claim": "C buys grain — succeeded, margin +1", "fidelity": "eyewitness",
         "heard_week": 3, "source_hint": "the street"},
    ], "heard_week tie breaks by rumor id"
    assert heard(state, "d") == [], "d has heard nothing yet"
    print("  pipeline: three hand-worked weeks match the embedded example  OK")


def test_structured_events_spawn_clean_claims_and_quiet_failures():
    """Structured events (tick's event_record shape): the rumor quotes the
    ACTION only — no margin, no contested-vs — and a failed action is quiet
    at its origin while the rival who contested it hears it at once."""
    state = small_state(week=4)
    state["events"] = [
        {"week": 4, "actor": "b", "text": "B burgles A's vault — failed (contested vs a), margin -7",
         "action": "B burgles A's vault", "outcome": "failed", "contested": True,
         "vs": "a", "margin": -7},
        {"week": 4, "actor": "a", "text": "A buys grain — succeeded, margin +1",
         "action": "A buys grain", "outcome": "succeeded", "contested": False,
         "margin": 1},
    ]
    spawned = spawn_from_events(state)
    assert spawned[0]["claim"] == "B burgles A's vault", "claim carries no resolution"
    assert spawned[0]["quiet"] == ["b"], "the failed burglar does not retell it"
    assert spawned[0]["heard_by"] == ["b", "a"], "the rival who contested it saw it too"
    assert "quiet" not in spawned[1], "successes spread normally"
    # and the quiet rumor moves only through the rival, never the burglar
    state["rumors"] = spawned
    state["meta"]["clock"]["week"] = 5
    spread(state, state["rumors"])
    assert spawned[0]["transmissions"] == [
        {"week": 5, "from": "a", "to": "c",
         "fidelity_before": "eyewitness", "fidelity_after": "reliable"},
    ], "the rival who contested it does the talking — not the origin"
    assert spawned[1]["transmissions"][0]["from"] == "a", "the success travels"
    print("  structured: clean claims, quiet failures, rival hears  OK")


if __name__ == "__main__":
    tests = [
        test_spawn_one_rumor_per_faction_event,
        test_spawn_ids_continue_existing_rumors,
        test_first_spread_drops_grade_to_alphabetical_first,
        test_spread_idempotent_within_week,
        test_three_spreads_kill_a_rumor,
        test_dead_telling_does_not_add_listener,
        test_no_audience_no_decay,
        test_heard_only_held_rumors_with_source_hints,
        test_full_pipeline_three_weeks,
        test_structured_events_spawn_clean_claims_and_quiet_failures,
    ]
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"FAILED {fn.__name__}: {e}")
            sys.exit(1)
    print("all rumor tests passed")
