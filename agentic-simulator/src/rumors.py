"""Rumors as first-class state: born from events, passed faction to faction.

One sentence: every retelling of a rumor drops its fidelity one grade, and
dead rumors stop moving. Claims are never rewritten — the text travels
intact and the grade of the telling is what decays (text distortion is a
later layer). All five factions share one city, so there is no travel time;
determinism only: ties break alphabetically by faction id.
"""

_GRADES = ("eyewitness", "reliable", "hearsay", "dead")


def spawn_from_events(state):
    """One rumor per just-resolved event with a faction actor — the actor saw
    it happen (world events have no witness to tell). Returns the new rumor
    records; does not mutate state — the caller appends them to
    state["rumors"]. Ids continue the existing sequence."""
    week = state["meta"]["clock"]["week"]
    base = len(state.get("rumors", []))
    spawned = []
    for event in state.get("events", []):
        if event["week"] != week or event["actor"] == "world":
            continue
        spawned.append({
            "id": "rumor_%04d" % (base + len(spawned) + 1),
            "born_week": week,
            "origin": event["actor"],
            "claim": event["text"],
            "truth": True,
            "fidelity": "eyewitness",
            "transmissions": [],
            "heard_by": [event["actor"]],
        })
    return spawned


def spread(state, rumors):
    """One sentence: each week, each live rumor is told once — its earliest-
    alphabetical holder tells the alphabetically-first faction that has not
    heard it — and the telling drops fidelity one grade. Idempotent per week
    via last_spread_week; a telling that lands at "dead" is recorded but the
    listener does not count as having heard it. No audience, no retelling,
    no decay."""
    week = state["meta"]["clock"]["week"]
    faction_ids = sorted(f["id"] for f in state["factions"])
    for rumor in sorted(rumors, key=lambda r: r["id"]):
        if rumor.get("last_spread_week") == week:
            continue
        rumor["last_spread_week"] = week
        if rumor["fidelity"] == "dead":
            continue
        candidates = [fid for fid in faction_ids if fid not in rumor["heard_by"]]
        if not candidates:
            continue
        before = rumor["fidelity"]
        after = _GRADES[_GRADES.index(before) + 1]
        rumor["fidelity"] = after
        rumor["transmissions"].append({
            "week": week,
            "from": min(rumor["heard_by"]),
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
