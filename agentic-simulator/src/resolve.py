"""Dice and the provisional action resolver.

Code disposes; agents only propose. Outcomes come from rolls, never from
model judgment, so the world can land off the median — the way a story
written to be a story never would.
"""

import random


def d(sides):
    return random.randint(1, sides)


def d20(advantage=None):
    rolls = [d(20)] if advantage not in ("advantage", "disadvantage") else [d(20), d(20)]
    return min(rolls) if advantage == "disadvantage" else max(rolls)


# Provisional difficulty ladder: risk acceptance sets the DC, committed
# resources set the bonus. Replace when a better model is agreed — whatever
# replaces it must stay explainable in one sentence.
_RISK_DC = {"low": 10, "medium": 13, "high": 16}
_RESOURCE_BONUS = {"none": 0, "low": 1, "medium": 2, "high": 3}


def resolve_action(action):
    """Resolve one proposed action. Returns an outcome record; does not
    mutate state — the caller (tick) applies it and logs it."""
    bonus = min(
        5,
        sum(
            _RESOURCE_BONUS.get(v, 0)
            for v in action.get("resources_committed", {}).values()
        ),
    )
    dc = _RISK_DC.get(action.get("risk_acceptance", "medium"), 13)
    roll = d20()
    total = roll + bonus
    return {
        "actor": action["actor"],
        "action": action["action"],
        "roll": roll,
        "bonus": bonus,
        "total": total,
        "dc": dc,
        "contested": False,
        "success": total >= dc,
        "margin": total - dc,
    }


def resolve_tick(proposals):
    """Resolve a full week of proposals in one pass.

    Contested rule (one sentence): if your action targets another faction
    that acted this week, both sides roll d20 + committed-resource bonus and
    the higher total wins — the loser's action fails, and margin is the gap
    between totals. Ties go to the defender.

    Overextension rule (one sentence): a faction in more than one contest
    rolls −2 per additional contest, in all of them — besieged on all
    fronts, beleaguered on all fronts. Two factions targeting each other
    fight ONE contest, not two. Actions that target a faction that did not
    act, or yourself, are uncontested and resolve against the risk DC.
    """
    by_actor = {p["actor"]: p for p in proposals}

    # collect contests: an edge attacker->target in proposal order; a mutual
    # pair (A->B and B->A) collapses to one contest
    contests = []  # (attacker, defender) — defender is the targeted side
    seen_pairs = set()
    for p in proposals:
        attacker = p["actor"]
        target = p.get("target")
        if target not in by_actor or target == attacker:
            continue
        pair = frozenset((attacker, target))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        contests.append((attacker, target))

    contest_count = {}
    for attacker, defender in contests:
        contest_count[attacker] = contest_count.get(attacker, 0) + 1
        contest_count[defender] = contest_count.get(defender, 0) + 1

    def roll_for(actor):
        p = by_actor[actor]
        bonus = min(
            5,
            sum(
                _RESOURCE_BONUS.get(v, 0)
                for v in p.get("resources_committed", {}).values()
            ),
        )
        penalty = -2 * (contest_count.get(actor, 1) - 1)
        roll = d20()
        return {
            "roll": roll,
            "bonus": bonus,
            "penalty": penalty,
            "total": roll + bonus + penalty,
        }

    outcomes_list = []
    for attacker, defender in contests:
        a, d = roll_for(attacker), roll_for(defender)
        attacker_wins = a["total"] > d["total"]  # ties go to the defender
        winner, loser = (attacker, defender) if attacker_wins else (defender, attacker)
        for actor, won in ((winner, True), (loser, False)):
            own = a if actor is attacker else d
            other = d if actor is attacker else a
            outcomes_list.append({
                "actor": actor,
                "action": by_actor[actor]["action"],
                "roll": own["roll"],
                "bonus": own["bonus"],
                "penalty": own["penalty"],
                "total": own["total"],
                "dc": None,
                "contested": True,
                "vs": defender if actor is attacker else attacker,
                "success": won,
                "margin": own["total"] - other["total"],
            })
    contested_actors = {actor for pair in contests for actor in pair}
    for p in proposals:
        if p["actor"] not in contested_actors:
            o = resolve_action(p)
            o["penalty"] = 0
            outcomes_list.append(o)
    return outcomes_list
