"""Smoke tests for the resolver's contested-action rule.

Zero dependencies — run directly:  python tests/test_resolve.py
"""

import random
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from resolve import resolve_action, resolve_tick


def approx(action, target=None, **over):
    p = {"actor": action, "action": f"{action} acts", "resources_committed": {}, "risk_acceptance": "medium"}
    if target:
        p["target"] = target
    p.update(over)
    return p


def test_uncontested_resolves_against_dc():
    random.seed(42)
    out = resolve_tick([approx("a"), approx("b")])
    assert all(o["contested"] is False for o in out)
    assert all(o["success"] == (o["total"] >= o["dc"]) for o in out)
    print("  uncontested: resolves vs risk DC  OK")


def test_targeting_an_actor_contests_both():
    random.seed(7)
    out = resolve_tick([approx("a", target="b"), approx("b"), approx("c")])
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
        out = resolve_tick([approx("a", target="b"), approx("b")])
        by = {o["actor"]: o for o in out}
        if by["a"]["total"] == by["b"]["total"]:
            assert by["a"]["success"] is False and by["b"]["success"] is True, "tie must favor the defender"
            print("  tie: defender wins  OK")
            return
    raise AssertionError("no tie occurred in 2000 trials — statistically impossible")


def test_targeting_a_nonactor_is_uncontested():
    random.seed(3)
    out = resolve_tick([approx("a", target="ghost"), approx("b")])
    by = {o["actor"]: o for o in out}
    assert by["a"]["contested"] is False, "targeting a faction that did not act is not a contest"
    print("  nonactor target: uncontested  OK")


def test_self_target_is_uncontested():
    random.seed(5)
    out = resolve_tick([approx("a", target="a")])
    assert out[0]["contested"] is False
    print("  self target: uncontested  OK")


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
    out = resolve_tick(week)
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


def test_overextension_penalty():
    """Besieged on all fronts: a faction in two contests rolls −2 in both;
    its attackers roll clean. Uncontested actions take no penalty."""
    random.seed(9)
    out = resolve_tick([approx("a", target="c"), approx("b", target="c"), approx("c")])
    by = {o["actor"]: o for o in out}
    assert by["c"]["contested"] and by["c"]["penalty"] == -2, "defender in 2 contests is penalized"
    assert by["a"]["penalty"] == 0 and by["b"]["penalty"] == 0, "single-contest attackers are clean"
    assert by["c"]["total"] == by["c"]["roll"] + by["c"]["bonus"] - 2
    print("  overextension: −2 per extra contest, in all of them  OK")


def test_mutual_targeting_is_one_contest():
    random.seed(4)
    out = resolve_tick([approx("a", target="b"), approx("b", target="a")])
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
    ])
    by = {o["actor"]: o for o in out}
    assert by["c"]["contested"] and by["c"]["penalty"] == -4, "three contests: −4"
    assert by["c"]["total"] == by["c"]["roll"] + by["c"]["bonus"] - 4
    for attacker in ("a", "b", "d"):
        assert by[attacker]["contested"] and by[attacker]["penalty"] == 0, \
            f"{attacker} fights one clean contest"
    print("  besieged stack: −4 on the defender, clean attackers  OK")


if __name__ == "__main__":
    for fn in [
        test_uncontested_resolves_against_dc,
        test_targeting_an_actor_contests_both,
        test_ties_go_to_defender,
        test_targeting_a_nonactor_is_uncontested,
        test_self_target_is_uncontested,
        test_commit_clamp,
        test_overextension_penalty,
        test_mutual_targeting_is_one_contest,
        test_besieged_stack,
        test_dry_run_week_replay,
    ]:
        fn()
    print("all resolver tests passed")
