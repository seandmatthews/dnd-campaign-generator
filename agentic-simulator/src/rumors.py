"""Rumors as first-class state: born from events, passed faction to faction.

One sentence: every retelling of a rumor drops its fidelity one grade, and
dead rumors stop moving. Claims are never rewritten — the text travels
intact and the grade of the telling is what decays (text distortion is a
later layer). All five factions share one city, so there is no travel time;
determinism only: ties break alphabetically by faction id.

Visibility (one sentence): a rumor quotes the action, never its resolution —
margins and contested-vs stay in the event ledger — and you do not retell
your own failure: a rumor born from a failed action is quiet at its origin
(a rival who contested it saw it too and does the talking).
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import events

_GRADES = ("eyewitness", "reliable", "hearsay", "dead")


def spawn_from_events(state):
    """One rumor per just-resolved event with a faction actor — the actor saw
    it happen (world events have no witness to tell). Returns the new rumor
    records; does not mutate state — the caller appends them to
    state["rumors"]. Ids continue the existing sequence. A failed action's
    rumor starts quiet at its origin; a contested rival heard it too."""
    week = state["meta"]["clock"]["week"]
    base = len(state.get("rumors", []))
    spawned = []
    for event in state.get("events", []):
        if event["week"] != week or event["actor"] == "world":
            continue
        rumor = {
            "id": "rumor_%04d" % (base + len(spawned) + 1),
            "born_week": week,
            "origin": event["actor"],
            "claim": events.claim_text(event),
            "truth": True,
            "fidelity": "eyewitness",
            "transmissions": [],
            "heard_by": [event["actor"]],
        }
        if events.succeeded(event) is False:
            rumor["quiet"] = [event["actor"]]
            rival = events.vs_of(event)
            if rival and rival != event["actor"]:
                rumor["heard_by"].append(rival)
        spawned.append(rumor)
    return spawned


def spread(state, rumors):
    """One sentence: each week, each live rumor is told once — its earliest-
    alphabetical holder willing to tell (the origin of a failed action is
    not) tells the alphabetically-first faction that has not heard it — and
    the telling drops fidelity one grade. Idempotent per week via
    last_spread_week; a telling that lands at "dead" is recorded but the
    listener does not count as having heard it. No willing teller, no
    retelling, no decay."""
    week = state["meta"]["clock"]["week"]
    faction_ids = sorted(f["id"] for f in state["factions"])
    for rumor in sorted(rumors, key=lambda r: r["id"]):
        if rumor.get("last_spread_week") == week:
            continue
        rumor["last_spread_week"] = week
        if rumor["fidelity"] == "dead":
            continue
        willing = [f for f in sorted(rumor["heard_by"])
                   if f not in rumor.get("quiet", [])]
        if not willing:
            continue
        candidates = [fid for fid in faction_ids if fid not in rumor["heard_by"]]
        if not candidates:
            continue
        before = rumor["fidelity"]
        after = _GRADES[_GRADES.index(before) + 1]
        rumor["fidelity"] = after
        rumor["transmissions"].append({
            "week": week,
            "from": willing[0],
            "to": candidates[0],
            "fidelity_before": before,
            "fidelity_after": after,
        })
        if after != "dead":
            rumor["heard_by"].append(candidates[0])


def heard(state, faction_id):
    """What faction_id holds by the current week: the raw claim, graded at
    the fidelity it reached them — "the street" if they held it the week it
    was born. Sorted by heard_week then rumor id. Feeds views.py."""
    views = []
    for rumor in sorted(state.get("rumors", []), key=lambda r: r["id"]):
        if faction_id not in rumor["heard_by"]:
            continue
        tx = next((t for t in rumor["transmissions"] if t["to"] == faction_id), None)
        if tx is None:  # origin: saw it happen, knows it at full fidelity
            views.append({
                "claim": rumor["claim"],
                "fidelity": "eyewitness",
                "heard_week": rumor["born_week"],
                "source_hint": "the street",
            })
        else:
            views.append({
                "claim": rumor["claim"],
                "fidelity": tx["fidelity_after"],
                "heard_week": tx["week"],
                "source_hint": tx["from"],
            })
    views.sort(key=lambda v: v["heard_week"])  # stable: rumor-id order within a week
    return views
