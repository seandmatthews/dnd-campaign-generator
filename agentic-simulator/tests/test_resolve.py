"""Smoke tests for the resolver's contested-action rule.

Zero dependencies — run directly:  python tests/test_resolve.py

Rule version 2 (resolver correctness): the resolver verifies target
identity against the state (a typo raises, never an easy win), difficulty
is a fixed default DC (risk_acceptance is descriptive only), and an
inactive faction still defends — d20 + current muscle bonus +2, no LLM.
"""

import copy
import random
import sys
import pathlib
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import resolve
from resolve import DEFAULT_DC, RULE_VERSION, resolve_action, resolve_tick


def approx(action, target=None, **over):
    p = {"actor": action, "action": f"{action} acts", "resources_committed": {}, "risk_acceptance": "medium"}
    if target:
        p["target"] = target
    p.update(over)
    return p


def state_for(*ids, muscle="low", districts=()):
    """A minimal state naming the factions the proposals reference — the
    resolver verifies target identity against it."""
    return {
        "meta": {"clock": {"week": 4}},
        "city": {"name": "Testburg", "districts": list(districts)},
        "factions": [{"id": fid, "resources": {"muscle": muscle}} for fid in ids],
        "events": [],
    }


def test_uncontested_resolves_against_dc():
    random.seed(42)
    out = resolve_tick([approx("a"), approx("b")], state_for("a", "b"))
    assert all(o["contested"] is False for o in out)
    assert all(o["success"] == (o["total"] >= o["dc"]) for o in out)
    assert all(o["dc"] == DEFAULT_DC for o in out), "the default DC is fixed"
    print("  uncontested: resolves vs the fixed default DC  OK")


def test_risk_acceptance_never_sets_the_dc():
    """Rule version 2: a model cannot buy an easier roll by declaring low
    risk — identical supported actions have identical DCs whatever the
    proposer claims about their appetite."""
    random.seed(13)
    for risk in ("low", "medium", "high"):
        out = resolve_tick([approx("a", risk_acceptance=risk)],
                           state_for("a"))
        assert out[0]["dc"] == DEFAULT_DC, f"risk={risk} moved the DC"
    rules = {"default_dc": 15}  # trusted rules/DM override, not proposals
    assert resolve_action(approx("a"), rules)["dc"] == 15
    print("  difficulty: fixed default DC; risk_acceptance is descriptive  OK")


def test_targeting_an_actor_contests_both():
    random.seed(7)
    out = resolve_tick([approx("a", target="b"), approx("b"), approx("c")],
                       state_for("a", "b", "c"))
    by = {o["actor"]: o for o in out}
    assert by["a"]["contested"] and by["b"]["contested"], "both sides of a targeting pair are contested"
    assert by["c"]["contested"] is False, "bystander is uncontested"
    assert by["a"]["vs"] == "b" and by["b"]["vs"] == "a"
    assert by["a"]["margin"] == -by["b"]["margin"], "margins mirror across the pair"
    assert sum(o["success"] for o in out if o["actor"] in ("a", "b")) == 1, "exactly one winner"
    print("  contested pair: opposed rolls, one winner, mirrored margins  OK")


def test_ties_go_to_defender():
    random.seed(1)
    # force a tie by giving both sides the same fixed roll via many trials
    for _ in range(2000):
        out = resolve_tick([approx("a", target="b"), approx("b")],
                           state_for("a", "b"))
        by = {o["actor"]: o for o in out}
        if by["a"]["total"] == by["b"]["total"]:
            assert by["a"]["success"] is False and by["b"]["success"] is True, "tie must favor the defender"
            print("  tie: defender wins  OK")
            return
    raise AssertionError("no tie occurred in 2000 trials — statistically impossible")


def test_unknown_target_raises_never_resolves_easy():
    """Rule version 2: a typo in a target must not become an uncontested
    success — the resolver refuses it outright."""
    random.seed(3)
    try:
        resolve_tick([approx("a", target="grmbl"), approx("b")],
                     state_for("a", "b"))
    except ValueError as e:
        assert "unknown target" in str(e)
        print("  typo target: refused, not rewarded  OK")
    else:
        raise AssertionError("unknown target was not refused")


def test_inactive_defender_still_opposes():
    """Rule version 2: a faction that exists in the state but did not act
    (scheduler held it, proposal call failed) defends deterministically —
    d20 + current muscle bonus +2, no LLM, and its outcome is marked
    defensive."""
    random.seed(19)
    state = state_for("a", "b", muscle="high")  # b exists but does not act
    out = resolve_tick([approx("a", target="b")], state)
    by = {o["actor"]: o for o in out}
    assert by["a"]["contested"] and by["a"]["vs"] == "b", \
        "the attack is contested even though the defender held"
    d = by["b"]
    assert d["contested"] and d.get("defensive") is True, \
        "the defender's outcome is marked defensive"
    assert d["action"] == "defend (did not act this week)"
    assert d["bonus"] == 3 + 2, "defense bonus = current muscle (high=3) + 2"
    assert d["penalty"] == 0, "defending does not overextend the defender"
    # deterministic: the same seed replays the same defense
    resolve.set_rng(19)
    again = {o["actor"]: o for o in resolve_tick([approx("a", target="b")],
                                                 state)}
    assert again["b"]["total"] == d["total"]
    print("  inactive defense: contested, deterministic, muscle + home ground  OK")


def test_defense_is_not_an_offensive_action():
    """A defense result never executes or rewards an invented offensive
    action for the defender: apply_outcomes applies nothing to it."""
    import tick
    state = state_for("a", "b")
    proposals = [approx("a", target="b")]
    out = resolve_tick([approx("a", target="b")], state)
    by = {o["actor"]: o for o in out}
    assert by["b"].get("defensive")
    before_events = len(state["events"])
    tick.apply_outcomes(state, out, proposals)
    assert len(state["events"]) == before_events + 1  # only the attacker's
    assert state["events"][-1]["actor"] == "a"
    print("  defense: no invented action, no reward for the defender  OK")


def test_two_attackers_on_one_inactive_defender():
    """Each attacker fights their own contest against the defense — nobody
    gets an uncontested pass because a rival attacked the same target."""
    random.seed(23)
    out = resolve_tick([approx("a", target="c"), approx("b", target="c")],
                       state_for("a", "b", "c"))
    by_actor = {}
    for o in out:
        by_actor.setdefault(o["actor"], []).append(o)
    for attacker in ("a", "b"):
        rows = by_actor[attacker]
        assert len(rows) == 1 and rows[0]["contested"] and rows[0]["vs"] == "c", \
            f"{attacker} fights its own contest against the inactive defender"
    defenses = by_actor["c"]
    assert len(defenses) == 2 and all(d.get("defensive") for d in defenses), \
        "the defender stood against both attacks"
    assert all(d["penalty"] == 0 for d in defenses), \
        "defending twice does not overextend the defender"
    print("  two attackers, one held defender: both contested  OK")


def test_self_target_is_uncontested():
    random.seed(5)
    out = resolve_tick([approx("a", target="a")], state_for("a"))
    assert out[0]["contested"] is False
    print("  self target: uncontested  OK")


def test_district_target_is_uncontested():
    random.seed(6)
    out = resolve_tick([approx("a", target="harbor")],
                       state_for("a", districts=["harbor"]))
    assert out[0]["contested"] is False
    print("  district target: explicitly supported, uncontested  OK")


def test_dry_run_week_replay():
    """The dry-run week's proposals, with the Gang now formally targeting the
    Reliquary — the collision that had to be narrated by hand before."""
    random.seed(11)
    week = [
        approx("mint", risk_acceptance="medium", resources_committed={"coin": "medium", "legitimacy": "low"}),
        approx("reliquary", resources_committed={"legitimacy": "high"}),
        approx("bastard", risk_acceptance="high", resources_committed={"coin": "low", "muscle": "low"}),
        approx("longshoremen", resources_committed={"muscle": "high"}),
        approx("marsh_gang", target="reliquary", risk_acceptance="high", resources_committed={"muscle": "low"}),
    ]
    state = state_for("mint", "reliquary", "bastard", "longshoremen",
                      "marsh_gang")
    out = resolve_tick(week, state)
    by = {o["actor"]: o for o in out}
    assert by["marsh_gang"]["contested"] and by["marsh_gang"]["vs"] == "reliquary"
    assert by["reliquary"]["contested"] and by["reliquary"]["vs"] == "marsh_gang"
    if by["reliquary"]["success"]:
        assert not by["marsh_gang"]["success"]
        print(f"  replay: reliquary {by['reliquary']['total']} beat gang {by['marsh_gang']['total']} — procession holds  OK")
    else:
        print(f"  replay: gang {by['marsh_gang']['total']} upset reliquary {by['reliquary']['total']} — procession robbed! OK")


def test_commit_clamp():
    """You cannot commit what you do not have."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    from llm import _validate
    p = _validate(
        {"action": "ambush the carts", "resources_committed": {"muscle": "high", "coin": "medium", "shadow": "high"}},
        "marsh_gang",
        actual_resources={"coin": "none", "muscle": "low", "legitimacy": "none"},
    )
    assert p["resources_committed"] == {"muscle": "low"}, f"clamp failed: {p['resources_committed']}"
    assert "shadow" not in p["resources_committed"]
    print("  commit clamp: no committing beyond stock  OK")


def test_validate_refuses_unknown_target():
    """llm._validate refuses an unknown target instead of passing it along
    (propose() retries with the correction; twice fails -> the faction
    holds rather than rolling against a typo)."""
    from llm import _validate
    try:
        _validate({"action": "x", "target": "The Trebuchet Works"},
                  "mint", known_ids=["mint", "reliquary", "world"],
                  districts=["harbor"])
    except ValueError as e:
        assert "unknown target" in str(e)
        print("  llm target identity: unknown names are refused  OK")
    else:
        raise AssertionError("unknown target passed validation")
    # canonicalization still works for exact (case-insensitive) matches
    p = _validate({"action": "x", "target": "Reliquary"},
                  "mint", known_ids=["mint", "reliquary", "world"],
                  districts=["harbor"])
    assert p["target"] == "reliquary" and p["target_kind"] == "faction"
    p = _validate({"action": "x", "target": "harbor"}, "mint",
                  known_ids=["mint", "world"], districts=["harbor"])
    assert p["target_kind"] == "district"


def test_overextension_penalty():
    """Besieged on all fronts: a faction in two contests rolls −2 in both;
    its attackers roll clean. Uncontested actions take no penalty."""
    random.seed(9)
    out = resolve_tick([approx("a", target="c"), approx("b", target="c"), approx("c")],
                       state_for("a", "b", "c"))
    by = {o["actor"]: o for o in out}
    assert by["c"]["contested"] and by["c"]["penalty"] == -2, "defender in 2 contests is penalized"
    assert by["a"]["penalty"] == 0 and by["b"]["penalty"] == 0, "single-contest attackers are clean"
    assert by["c"]["total"] == by["c"]["roll"] + by["c"]["bonus"] - 2
    print("  overextension: −2 per extra contest, in all of them  OK")


def test_mutual_targeting_is_one_contest():
    random.seed(4)
    out = resolve_tick([approx("a", target="b"), approx("b", target="a")],
                       state_for("a", "b"))
    by = {o["actor"]: o for o in out}
    assert by["a"]["contested"] and by["b"]["contested"]
    assert by["a"]["penalty"] == 0 and by["b"]["penalty"] == 0, "a mutual pair is ONE contest each"
    assert by["a"]["vs"] == "b" and by["b"]["vs"] == "a"
    assert sum(o["success"] for o in out) == 1
    print("  mutual pair: one contest, no overextension  OK")


def test_besieged_stack():
    """Three attackers, one defender: the defender eats −4 in every fight;
    each attacker fights its own single contest clean."""
    random.seed(2)
    out = resolve_tick([
        approx("a", target="c"), approx("b", target="c"),
        approx("d", target="c"), approx("c"),
    ], state_for("a", "b", "c", "d"))
    by = {o["actor"]: o for o in out}
    assert by["c"]["contested"] and by["c"]["penalty"] == -4, "three contests: −4"
    assert by["c"]["total"] == by["c"]["roll"] + by["c"]["bonus"] - 4
    for attacker in ("a", "b", "d"):
        assert by[attacker]["contested"] and by[attacker]["penalty"] == 0, \
            f"{attacker} fights one clean contest"
    print("  besieged stack: −4 on the defender, clean attackers  OK")


def test_replay_without_network_is_identical():
    """Fixed normalized proposals + the same seed: re-resolving yields the
    identical mechanical result — no model, no network."""
    week = [
        approx("mint", target="reliquary",
               resources_committed={"coin": "medium"}),
        approx("reliquary", resources_committed={"legitimacy": "high"}),
        approx("marsh_gang", target="harbor", risk_acceptance="high",
               resources_committed={"muscle": "low"}),
    ]
    state = state_for("mint", "reliquary", "marsh_gang",
                      districts=["harbor"])
    resolve.set_rng(424242)
    first = resolve_tick(week, state)
    resolve.set_rng(424242)
    second = resolve_tick(week, state)
    assert first == second, "same seed + same proposals must replay identically"
    assert all(o["rule_version"] == RULE_VERSION for o in first)
    print("  replay: same seed + normalized proposals -> identical outcomes  OK")


if __name__ == "__main__":
    for fn in [
        test_uncontested_resolves_against_dc,
        test_risk_acceptance_never_sets_the_dc,
        test_targeting_an_actor_contests_both,
        test_ties_go_to_defender,
        test_unknown_target_raises_never_resolves_easy,
        test_inactive_defender_still_opposes,
        test_defense_is_not_an_offensive_action,
        test_two_attackers_on_one_inactive_defender,
        test_self_target_is_uncontested,
        test_district_target_is_uncontested,
        test_commit_clamp,
        test_validate_refuses_unknown_target,
        test_overextension_penalty,
        test_mutual_targeting_is_one_contest,
        test_besieged_stack,
        test_replay_without_network_is_identical,
        test_dry_run_week_replay,
    ]:
        fn()
    print("all resolver tests passed")
