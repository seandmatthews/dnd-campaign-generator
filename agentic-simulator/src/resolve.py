"""Dice and the provisional action resolver.

Code disposes; agents only propose. Outcomes come from rolls, never from
model judgment, so the world can land off the median — the way a story
written to be a story never would.

Rule version 2 (resolver correctness):
- One documented difficulty: unopposed actions resolve against a fixed
  default DC (below) — the proposer's risk_acceptance is DESCRIPTIVE only
  and never sets the DC. A model cannot buy an easier roll by declaring
  low risk. Difficulty overrides belong to trusted rules/DM input via the
  `rules` argument, not to proposals.
- Unknown targets are refused, not rewarded: the caller validates identity
  (llm.propose), and resolve_tick re-checks against the state — a typo can
  never become an uncontested success.
- A targeted faction that did not act this week (scheduler held it, or its
  proposal call failed) still defends: one deterministic defensive roll
  from its state, no LLM call. Defense bonus rule (one sentence): a
  defender on home ground rolls d20 + their CURRENT muscle bonus +2 —
  distinct from offensive committed-resource bonuses, and a defense result
  never executes or rewards an offensive action for the defender.
- Every outcome records rule_version; tick saves normalized proposals,
  base-state identity, and the seed per week so a week can be replayed
  through --replay with no network calls.
"""

import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from store import places_of

RULE_VERSION = 2

# The RNG source: the `random` module itself by default, so random.seed()
# keeps driving it. tick sets a per-week seeded Random via set_rng() and
# records the seed in meta.rng_seeds — a week is replayable from its seed.
_rng = random


def set_rng(seed):
    """Determinism dial: pass an int seed (replay a recorded week) or a
    random.Random instance."""
    global _rng
    _rng = random.Random(seed) if isinstance(seed, int) else seed


def d(sides):
    return _rng.randint(1, sides)


def d20(advantage=None):
    rolls = [d(20)] if advantage not in ("advantage", "disadvantage") else [d(20), d(20)]
    return min(rolls) if advantage == "disadvantage" else max(rolls)


# Difficulty: one fixed default for supported unopposed actions. A
# conservative implementation default, not balanced game design — tune it
# through trusted rules (the `rules` argument) after playtesting, never
# through proposals. risk_acceptance stays on the record as flavor.
DEFAULT_DC = 12
_RESOURCE_BONUS = {"none": 0, "low": 1, "medium": 2, "high": 3}
DEFENSE_BONUS = 2  # home ground; see the module docstring's defense rule
_DEFENSE_ACTION = "defend (did not act this week)"


def _bonus(resources_committed):
    """The committed-resource bonus, capped at +5 — shared by the contested
    and uncontested paths."""
    return min(
        5,
        sum(
            _RESOURCE_BONUS.get(v, 0)
            for v in (resources_committed or {}).values()
        ),
    )


def _muscle_bonus(faction):
    """The defender's CURRENT muscle grade — what they have, not what a
    proposal committed (an inactive defender proposed nothing)."""
    return _RESOURCE_BONUS.get((faction.get("resources") or {}).get(
        "muscle", "none"), 0)


def resolve_action(action, rules=None):
    """Resolve one proposed action. Returns an outcome record; does not
    mutate state — the caller (tick) applies it and logs it. `rules` is
    trusted input (DM/rules layer) and may override default_dc."""
    bonus = _bonus(action.get("resources_committed"))
    dc = DEFAULT_DC
    if rules and rules.get("default_dc") is not None:
        dc = int(rules["default_dc"])
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
        "rule_version": RULE_VERSION,
    }


def _known_targets(state):
    """The typed target set the resolver accepts, straight from the state:
    faction ids, 'world', 'self', and every place's districts (any place,
    not just the primary — a district target must resolve wherever the
    faction that proposed it lives)."""
    ids = {f["id"] for f in (state or {}).get("factions", [])} | {"world", "self"}
    districts = set()
    for p in places_of(state or {}):
        districts |= {str(x) for x in (p.get("districts") or [])}
    return ids, districts


def consolidate_outcomes(outcomes, proposals):
    """One outcome per ACTING faction — the shared consolidation used both
    by tick's one-consequence rule and by project progress: the worst
    margin wins the judging, and on EQUAL margins a loss defeats a success
    (a tied defensive win cannot hide a tied attacking loss), recording
    order breaking any remaining tie. Defensive outcomes — an inactive
    faction pulled into a contest — never consolidate: the defender
    proposed nothing and takes no action."""
    proposers = {p["actor"] for p in (proposals or [])}
    worst = {}
    for o in outcomes or []:
        if o.get("defensive") or o["actor"] not in proposers:
            continue
        cur = worst.get(o["actor"])
        if cur is None or _worse(o, cur):
            worst[o["actor"]] = o
    return worst


def _worse(a, b):
    """Should outcome `a` replace `b` as an actor's consolidated outcome?"""
    if a["margin"] != b["margin"]:
        return a["margin"] < b["margin"]
    if a["success"] != b["success"]:
        return not a["success"]  # a loss beats a success at equal margin
    return False


def resolve_tick(proposals, state=None):
    """Resolve a full week of proposals in one pass.

    Contested rule (one sentence): if your action targets another faction
    that acted this week, both sides roll d20 + committed-resource bonus and
    the higher total wins — the loser's action fails, and margin is the gap
    between totals. Ties go to the defender.

    Overextension rule (one sentence): a faction in more than one contest
    rolls −2 per additional contest, in all of them — besieged on all
    fronts, beleaguered on all fronts. Two factions targeting each other
    fight ONE contest, not two. Attacking an inactive faction counts as a
    front; defending does not overextend the defender.

    Inactive defense (rule version 2): a target that exists in the state
    but did not propose this week still contests — d20 + current muscle
    bonus +2, deterministic from the week's seed, no LLM call. Ties go to
    the defender. The defender's outcome is marked defensive: the caller
    must not treat it as an action the defender took.

    Target identity is validated against the state when one is passed: a
    proposal naming an unknown faction/district raises ValueError instead
    of resolving uncontested — a typo never becomes an easy win.
    """
    by_actor = {p["actor"]: p for p in proposals}
    if state is not None:
        ids, districts = _known_targets(state)
        for p in proposals:
            target = p.get("target") or "self"  # _validate's default
            if target in districts:
                continue
            if target in ids:
                continue
            raise ValueError(
                f"unknown target {target!r} from {p['actor']!r} — the "
                "proposal must be corrected, not rewarded with an "
                "uncontested roll")

    if state is not None:
        faction_map = {f["id"]: f for f in state.get("factions", [])}
    else:
        # no state: only mutual contests between proposers are decidable
        faction_map = {}

    # collect contests: an edge attacker->target in proposal order; a mutual
    # pair (A->B and B->A) collapses to one contest. Each attack on an
    # inactive faction is its own contest against that faction's defense —
    # no attacker skips a contest because a rival attacked the same target.
    contests = []  # (attacker, defender, defender_proposal|None)
    seen_pairs = set()
    for p in proposals:
        attacker = p["actor"]
        target = p.get("target")
        if target == attacker or target not in faction_map:
            continue  # self, world, district, or (unvalidated) unknown
        if target in by_actor:
            key = frozenset((attacker, target))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            contests.append((attacker, target, by_actor[target]))
        else:
            contests.append((attacker, target, faction_map[target]))

    contest_count = {}
    for attacker, defender, _ in contests:
        contest_count[attacker] = contest_count.get(attacker, 0) + 1
        if defender in by_actor:  # defenses do not overextend the defender
            contest_count[defender] = contest_count.get(defender, 0) + 1

    def roll_for(actor):
        p = by_actor[actor]
        bonus = _bonus(p.get("resources_committed"))
        penalty = -2 * (contest_count.get(actor, 1) - 1)
        roll = d20()
        return {
            "roll": roll,
            "bonus": bonus,
            "penalty": penalty,
            "total": roll + bonus + penalty,
        }

    def defensive_roll(faction):
        roll = d20()
        bonus = _muscle_bonus(faction) + DEFENSE_BONUS
        return {"roll": roll, "bonus": bonus, "penalty": 0,
                "total": roll + bonus}

    outcomes_list = []
    for attacker, defender, _ in contests:
        a = roll_for(attacker)
        if defender in by_actor:  # the defender acted: their proposal rolls
            dside = roll_for(defender)
        else:  # inactive: deterministic defense from their state, no LLM
            dside = defensive_roll(faction_map[defender])
        attacker_wins = a["total"] > dside["total"]  # ties go to the defender
        winner, loser = (attacker, defender) if attacker_wins else (defender, attacker)
        for actor, won in ((winner, True), (loser, False)):
            own = a if actor is attacker else dside
            other = dside if actor is attacker else a
            inactive = defender not in by_actor and actor == defender
            outcomes_list.append({
                "actor": actor,
                "action": by_actor[actor]["action"] if actor in by_actor
                          else _DEFENSE_ACTION,
                "roll": own["roll"],
                "bonus": own["bonus"],
                "penalty": own["penalty"],
                "total": own["total"],
                "dc": None,
                "contested": True,
                "defensive": inactive,
                "vs": defender if actor is attacker else attacker,
                "success": won,
                "margin": own["total"] - other["total"],
                "rule_version": RULE_VERSION,
            })
    contested_actors = {actor for attacker, defender, _ in contests
                        for actor in (attacker, defender)}
    for p in proposals:
        if p["actor"] not in contested_actors:
            o = resolve_action(p, state.get("meta", {}).get("rules")
                               if state else None)
            o["penalty"] = 0
            outcomes_list.append(o)
    return outcomes_list
