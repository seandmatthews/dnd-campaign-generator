"""Tests for the activation scheduler.

Zero dependencies — run directly:  python tests/test_scheduler.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from scheduler import activation, holds


def faction(fid, name, resources=None, disposition=None):
    return {
        "id": fid,
        "name": name,
        "kind": "test",
        "goal": "test",
        "resources": resources or {"coin": "low", "muscle": "low", "legitimacy": "low"},
        "disposition": disposition or {},
        "secret": "",
        "knows": [],
    }


def state(week, factions, events=None):
    return {
        "meta": {"clock": {"week": week}},
        "factions": factions,
        "events": events or [],
    }


def test_targeted_last_week_activates():
    bank = faction("bank", "The Bank")
    crew = faction("crew", "The Crew", disposition={"bank": "neutral"})
    st = state(2, [bank, crew], [
        {"week": 1, "actor": "bank", "text": "The Bank opens its ledgers."},
        {"week": 2, "actor": "bank",
         "text": "The Bank seizes the crew's warehouse — succeeded (contested vs crew), margin +3"},
    ])
    a = activation(st)
    assert a["crew"]["act"] is True
    assert a["crew"]["score"] == 2, f"targeted alone scores exactly 2: {a['crew']}"
    assert a["crew"]["reasons"] == ["targeted last week"]
    print("  targeted last week: contested target activates at exactly 2  OK")


def test_hostility_and_pressure_scores_exactly_two():
    def week_state(crew, bank_toward_crew="neutral"):
        bank = faction("bank", "The Bank", disposition={"crew": bank_toward_crew})
        return state(2, [bank, crew], [
            {"week": 1, "actor": "bank", "text": "The Bank counts its coin."},
            {"week": 2, "actor": "crew",
             "text": "Quiet moves among the dockworkers — succeeded, margin +1"},
        ])

    poor = {"coin": "none", "muscle": "low", "legitimacy": "low"}
    outgoing = faction("crew", "The Crew", disposition={"bank": "hostile"}, resources=poor)
    a = activation(week_state(outgoing))
    assert a["crew"]["score"] == 2 and a["crew"]["act"] is True
    assert a["crew"]["reasons"] == ["hostility", "pressure"]

    incoming = faction("crew", "The Crew", disposition={"bank": "neutral"}, resources=poor)
    b = activation(week_state(incoming, bank_toward_crew="hostile"))
    assert b["crew"]["score"] == 2 and b["crew"]["act"] is True
    assert "hostility" in b["crew"]["reasons"], "incoming hostility must count"
    print("  hostility + pressure: exactly 2, outgoing or incoming  OK")


def test_quiet_faction_holds_and_decays():
    bank = faction("bank", "The Bank", disposition={"guild": "friendly"})
    guild = faction("guild", "The Guild", disposition={"bank": "neutral"},
                    resources={"coin": "medium", "muscle": "medium", "legitimacy": "low"})
    events = [
        {"week": 1, "actor": "guild", "text": "The Guild swears its oaths at the harbor altar."},
        {"week": 2, "actor": "bank", "text": "The Bank calls in old debts — succeeded, margin +1"},
        {"week": 3, "actor": "bank", "text": "The Bank sponsors a feast — succeeded, margin +2"},
        {"week": 4, "actor": "bank", "text": "The Bank buys grain — succeeded, margin +1"},
    ]
    scores = []
    for week in (2, 3, 4, 5):
        st = state(week, [bank, guild], events)
        a = activation(st)
        scores.append(a["guild"]["score"])
        assert a["guild"]["act"] is False, f"guild must hold at week {week}"
        assert a["guild"]["reasons"] == [f"quiet for {min(week - 1, 3)} week(s)"], a["guild"]
    assert scores == [-1, -2, -3, -3], f"decay must cap at -3: {scores}"
    print("  quiet decay: -1, -2, -3, then capped, holding throughout  OK")


def test_idle_counter_needs_full_absence():
    bank = faction("bank", "The Bank")
    guild = faction("guild", "The Guild",
                    resources={"coin": "medium", "muscle": "medium", "legitimacy": "medium"})
    base = [
        {"week": 1, "actor": "guild", "text": "The Guild swears its oaths at the harbor altar."},
        {"week": 2, "actor": "bank", "text": "The Bank counts its coin."},
    ]
    # week 3 presence via vs-target; week 4 (last week) quiet. Weeks 1-2 must not extend the streak.
    vs_events = base + [
        {"week": 3, "actor": "bank",
         "text": "The Bank raids the guild stores — succeeded (contested vs guild), margin +2"},
        {"week": 4, "actor": "bank", "text": "The Bank counts its coin again."},
    ]
    a = activation(state(4, [bank, guild], vs_events))
    assert a["guild"]["score"] == -1 and a["guild"]["act"] is False
    assert a["guild"]["reasons"] == ["quiet for 1 week(s)"], a["guild"]

    # same shape, but week-3 presence is a bare text mention (not actor, not vs)
    text_events = base + [
        {"week": 3, "actor": "bank", "text": "The Bank parades the guild's seized banner."},
        {"week": 4, "actor": "bank", "text": "The Bank counts its coin again."},
    ]
    b = activation(state(4, [bank, guild], text_events))
    assert b["guild"]["score"] == -1
    assert b["guild"]["reasons"] == ["quiet for 1 week(s)"], b["guild"]
    print("  idle counter: only weeks with the faction fully absent count  OK")


def test_founding_week_activates_everyone():
    quiet_one = faction("guild", "The Guild",
                        resources={"coin": "high", "muscle": "high", "legitimacy": "high"})
    angry_one = faction("crew", "The Crew", disposition={"bank": "hostile"},
                        resources={"coin": "none", "muscle": "low", "legitimacy": "low"})
    bystander = faction("bank", "The Bank")
    st = state(1, [quiet_one, angry_one, bystander], [])
    a = activation(st)
    assert all(a[f]["act"] for f in ("guild", "crew", "bank")), a
    assert all("founding week" in r["reasons"] for r in a.values())
    assert a["guild"]["score"] == 2, "founding +2 alone for the quiet and funded"
    assert a["crew"]["score"] == 4, "founding + hostility + pressure"
    assert holds(st, a) == []
    print("  founding week: everyone acts  OK")


def test_week_zero_studio_sandbox_activates_everyone():
    """clock week 0 is what studio's create-simulation writes — the first
    paid week must not be a guaranteed hold-hold (found week 2026-09-23:
    "A Week Without Resolutions")."""
    guild = faction("guild", "The Guild",
                    resources={"coin": "low", "muscle": "low", "legitimacy": "low"})
    crew = faction("crew", "The Crew",
                   resources={"coin": "low", "muscle": "low", "legitimacy": "low"})
    st = state(0, [guild, crew], [])
    a = activation(st)
    assert a["guild"]["act"] is True and a["crew"]["act"] is True, a
    assert a["guild"]["reasons"] == ["founding week"], a["guild"]
    assert a["guild"]["score"] == 2
    assert holds(st, a) == []
    print("  week 0 (fresh studio sandbox): founding week fires, everyone acts  OK")


def test_act_boundary_is_exactly_two():
    bank = faction("bank", "The Bank")
    poor = faction("crew", "The Crew", resources={"coin": "none", "muscle": "low", "legitimacy": "low"})
    # pressure only (+1), present last week as actor (so no idle discount), never touched
    st = state(2, [bank, poor], [
        {"week": 1, "actor": "bank", "text": "The Bank counts its coin."},
        {"week": 2, "actor": "crew", "text": "Nets mended by lantern light — succeeded, margin +1"},
    ])
    a = activation(st)
    assert a["crew"]["score"] == 1, f"pressure alone is 1: {a['crew']}"
    assert a["crew"]["act"] is False, "a score-1 faction must hold"
    assert a["crew"]["reasons"] == ["pressure"]

    # touch them and the boundary tips: 1 + 2 = 3
    st_touched = state(2, [bank, poor], [
        {"week": 1, "actor": "bank", "text": "The Bank counts its coin."},
        {"week": 2, "actor": "crew", "text": "Nets mended by lantern light — succeeded, margin +1"},
        {"week": 2, "actor": "bank",
         "text": "The Bank seizes the crew's nets — succeeded (contested vs crew), margin +1"},
    ])
    b = activation(st_touched)
    assert b["crew"]["score"] == 3 and b["crew"]["act"] is True
    print("  boundary: score 1 holds; touching it tips to 3 and acts  OK")


def test_holds_returns_exactly_the_holders():
    bank = faction("bank", "The Bank")
    holder = faction("guild", "The Guild",
                     resources={"coin": "medium", "muscle": "medium", "legitimacy": "medium"})
    mover = faction("crew", "The Crew", disposition={"bank": "hostile"},
                    resources={"coin": "none", "muscle": "low", "legitimacy": "low"})
    st = state(2, [bank, holder, mover], [
        {"week": 1, "actor": "guild", "text": "The Guild swears its oaths."},
        {"week": 2, "actor": "crew",
         "text": "Quiet moves among the dockworkers — succeeded, margin +1"},
    ])
    a = activation(st)
    assert a["crew"]["act"] is True and a["guild"]["act"] is False and a["bank"]["act"] is False
    assert holds(st, a) == ["bank", "guild"], "exactly the non-activating ids, roster order"
    print("  holds: exactly the non-activating ids  OK")


# Hand-copied factions and week-1..3 events from world/state.yaml (clock week 3).
_FIXTURE_FACTIONS = [
    faction("mint", "House Ferrand (the Mint)",
            resources={"coin": "high", "muscle": "low", "legitimacy": "high"},
            disposition={"reliquary": "cold", "bastard": "hostile",
                         "longshoremen": "transactional", "marsh_gang": "unaware"}),
    faction("reliquary", "The Reliquary",
            resources={"coin": "medium", "muscle": "low", "legitimacy": "high"},
            disposition={"mint": "hostile", "bastard": "sympathetic",
                         "longshoremen": "warm", "marsh_gang": "unaware"}),
    faction("bastard", "The Bastard's Claim (Sera of Ruravaqan)",
            resources={"coin": "none", "muscle": "low", "legitimacy": "low"},
            disposition={"mint": "hostile", "reliquary": "warm",
                         "longshoremen": "friendly", "marsh_gang": "unaware"}),
    faction("longshoremen", "The Longshoremen",
            resources={"coin": "none", "muscle": "medium", "legitimacy": "medium"},
            disposition={"mint": "hostile", "reliquary": "warm",
                         "bastard": "sympathetic", "marsh_gang": "wary"}),
    faction("marsh_gang", "The Marsh Gang",
            resources={"coin": "none", "muscle": "none", "legitimacy": "none"},
            disposition={"mint": "unaware", "reliquary": "hostile",
                         "bastard": "unaware", "longshoremen": "wary"}),
]

_FIXTURE_EVENTS = [
    {"week": 1, "actor": "world",
     "text": "The Doge of Ruravaqan dies without naming an heir. The bells ring for three "
             "days; the creditors arrive on the fourth."},
    {"week": 2, "actor": "mint",
     "text": "The estate council attaches the Doge's customs revenue to the Mint's claim."},
    {"week": 2, "actor": "reliquary",
     "text": "A grand funeral procession moves through the Docks; alms flow toward the arrears."},
    {"week": 2, "actor": "bastard",
     "text": "Sera is caught inside the sealed manor by the Mint's watchers; released, but marked."},
    {"week": 2, "actor": "longshoremen",
     "text": "The harbor refusal collapses — Tannery strikebreakers take the estate's cargo."},
    {"week": 2, "actor": "marsh_gang",
     "text": "Two Marsh Gang robbers are taken during the procession; the gang blames the church."},
    {"week": 3, "actor": "mint",
     "text": "Petition the council to rank House Ferrand's debt as senior to the Bastard's "
             "inheritance claim and assign the attached customs revenue to the Mint before any "
             "inheritance is settled. — succeeded (contested vs bastard), margin +2"},
    {"week": 3, "actor": "reliquary",
     "text": "Read the tithe ledger aloud from the pulpit, name every debtor family and the exact "
             "sum owed, and declare interdiction — no rites, no consecrated burial for any debtor "
             "house until arrears are paid, and the Doge's public funeral held only on the day "
             "House Ferrand settles in full. — succeeded (contested vs marsh_gang), margin +5"},
    {"week": 3, "actor": "bastard",
     "text": "Enter the Doge's manor during the Mint's watcher rotation gap and steal or destroy "
             "the instrument House Ferrand's claim rests on. — failed (contested vs mint), margin -2"},
    {"week": 3, "actor": "longshoremen",
     "text": "Strike the harbor — halt all loading, unloading, and customs inspections until the "
             "council pays the fleet venture's back wages in full — succeeded, margin +1"},
    {"week": 3, "actor": "marsh_gang",
     "text": "Strike the Reliquary's storerooms by night and carry off the funeral wealth the "
             "church has taken in. — failed (contested vs reliquary), margin -5"},
]


def test_real_world_fixture():
    """The live world/state.yaml factions and events, hand-copied."""
    st = state(3, _FIXTURE_FACTIONS, _FIXTURE_EVENTS)
    a = activation(st)
    assert set(a) == {"mint", "reliquary", "bastard", "longshoremen", "marsh_gang"}
    for fid, r in a.items():
        assert r["act"] is True, f"{fid} should act at week 3: {r}"
    for fid in ("mint", "reliquary", "bastard", "marsh_gang"):
        assert "targeted last week" in a[fid]["reasons"], fid
    assert a["longshoremen"]["score"] == 2, "untouched by week 3 despite acting"
    assert a["longshoremen"]["reasons"] == ["hostility", "pressure"]
    assert holds(st, a) == []

    # Same events, clock at week 2: reliquary (uncontested, funded, hostile) sits out.
    st2 = state(2, _FIXTURE_FACTIONS, _FIXTURE_EVENTS)
    a2 = activation(st2)
    assert a2["reliquary"]["score"] == 1 and a2["reliquary"]["act"] is False
    assert holds(st2, a2) == ["reliquary"], "week-3 events must not leak into the week-2 run"
    print("  real fixture: week 3 all act; at week 2 reliquary holds  OK")


if __name__ == "__main__":
    for fn in [
        test_targeted_last_week_activates,
        test_hostility_and_pressure_scores_exactly_two,
        test_quiet_faction_holds_and_decays,
        test_idle_counter_needs_full_absence,
        test_founding_week_activates_everyone,
        test_week_zero_studio_sandbox_activates_everyone,
        test_act_boundary_is_exactly_two,
        test_holds_returns_exactly_the_holders,
        test_real_world_fixture,
    ]:
        fn()
    print("all scheduler tests passed")
