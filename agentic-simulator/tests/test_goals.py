"""Tests for goal review: ledger-grounded goal-update proposals.

Zero dependencies — run directly:  python tests/test_goals.py
"""

import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import goals
from goals import GOAL_MAX, propose_updates


def faction(fid, goal, name=None):
    return {
        "id": fid,
        "name": name or fid.title(),
        "kind": "test",
        "goal": goal,
        "resources": {"coin": "medium", "muscle": "low", "legitimacy": "high"},
        "disposition": {},
        "secret": "",
        "knows": [],
    }


def state():
    return {
        "city": {"name": "Provis"},
        "meta": {"clock": {"week": 4, "season": "late autumn"}},
        "factions": [
            faction("wardens", "rebuild the bridge and the toll with it"),
            faction("ferrymen", "keep the crossing broken, own the river traffic"),
        ],
        "events": [
            {"week": 3, "actor": "wardens", "text": "The bridge reopened — succeeded, margin +7",
             "outcome": "succeeded"},
            {"week": 4, "actor": "world", "text": "The council decreed the regency ends at week 8"},
        ],
        "rumors": [{"id": "rumor_0001", "claim": "The ferry guild is hiring blades",
                    "fidelity": "hearsay", "born_week": 3,
                    "heard_by": ["ferrymen"], "transmissions": []}],
    }


def reply(doc):
    return "```yaml\n" + yaml.safe_dump(doc, allow_unicode=True) + "```"


def test_valid_update_kept_with_cited_reason():
    ask = lambda prompt: reply({"updates": [  # noqa: E731
        {"faction_id": "wardens", "goal": "hold the bridge until the regency ends at week 8",
         "reason": "week 4: the council decreed the regency ends at week 8"},
    ]})
    updates, findings = propose_updates(state(), ask)
    assert len(updates) == 1 and not findings
    u = updates[0]
    assert u["faction_id"] == "wardens"
    assert u["goal"].startswith("hold the bridge")
    assert u["current_goal"] == "rebuild the bridge and the toll with it"
    assert u["name"] == "Wardens"


def test_invalid_updates_dropped_with_findings():
    ask = lambda prompt: reply({"updates": [  # noqa: E731
        {"faction_id": "ghost", "goal": "x", "reason": "y"},           # unknown id
        {"faction_id": "wardens", "goal": "rebuild the bridge and the toll with it",
         "reason": "unchanged"},                                       # unchanged
        {"faction_id": "ferrymen", "goal": "", "reason": "y"},         # empty
        {"faction_id": "ferrymen", "goal": "one\ntwo", "reason": "y"},  # multi-line
        {"faction_id": "ferrymen", "goal": "g" * (GOAL_MAX + 1), "reason": "y"},
        {"faction_id": "ferrymen", "goal": "a new goal", "reason": ""},  # no reason
    ]})
    updates, findings = propose_updates(state(), ask)
    assert updates == []
    assert len(findings) == 6


def test_duplicate_faction_keeps_first():
    ask = lambda prompt: reply({"updates": [  # noqa: E731
        {"faction_id": "wardens", "goal": "first goal", "reason": "r1"},
        {"faction_id": "wardens", "goal": "second goal", "reason": "r2"},
    ]})
    updates, findings = propose_updates(state(), ask)
    assert len(updates) == 1 and updates[0]["goal"] == "first goal"
    assert any("duplicate" in f for f in findings)


def test_empty_updates_is_a_fine_answer():
    ask = lambda prompt: reply({"updates": []})
    updates, findings = propose_updates(state(), ask)
    assert updates == [] and findings == []


def test_unparseable_reply_reports_a_finding_not_a_crash():
    updates, findings = propose_updates(state(), lambda prompt: "no yaml here")
    assert updates == []
    assert findings and "updates list" in findings[0]


def test_nested_fence_and_broken_yaml_report_findings_not_crashes():
    # a goal value containing ``` truncates the fenced block mid-string —
    # the reply must become a finding, never a ParserError traceback
    nested = ('```yaml\nupdates:\n  - faction_id: wardens\n'
              '    goal: "seize the ```py``` archive"\n'
              '    reason: r\n```')
    for raw in (nested, "updates: [unclosed", ""):
        updates, findings = propose_updates(state(), lambda prompt, r=raw: r)
        assert updates == [], raw
        assert findings and "updates list" in findings[0], raw


def test_alias_bomb_reply_is_refused_not_expanded():
    bomb = ("a: &x [1]\n" + "".join(f"b{i}: [&x, &x, &x]\n" for i in range(9))
            + "c: [*x, *x, *x]")
    updates, findings = propose_updates(state(), lambda prompt: "```yaml\n" + bomb + "\n```")
    assert updates == [] and findings


def test_control_characters_are_stripped_from_goal_and_reason():
    ask = lambda prompt: reply({"updates": [  # noqa: E731
        {"faction_id": "wardens", "goal": "hold\tthe\x00bridge",
         "reason": "week 3:\x1b the bridge reopened"}],
    })
    updates, findings = propose_updates(state(), ask)
    assert not findings
    assert updates[0]["goal"] == "hold the bridge"
    assert updates[0]["reason"] == "week 3: the bridge reopened"


def test_non_scalar_goal_and_reason_are_dropped():
    ask = lambda prompt: reply({"updates": [  # noqa: E731
        {"faction_id": "wardens", "goal": {"nested": "dict"}, "reason": "r"},
        {"faction_id": "ferrymen", "goal": "a new goal", "reason": ["list"]},
    ]})
    updates, findings = propose_updates(state(), ask)
    assert updates == []
    assert any("goal is not text" in f for f in findings)
    assert any("reason is not text" in f for f in findings)


def test_bare_list_reply_is_accepted():
    ask = lambda prompt: reply([  # noqa: E731
        {"faction_id": "ferrymen", "goal": "escort grain convoys past the bridge",
         "reason": "week 3: the bridge reopened, the blockade is moot"},
    ])
    updates, findings = propose_updates(state(), ask)
    assert len(updates) == 1 and not findings


def test_digest_carries_roster_ledger_and_rumors():
    d = goals.digest(state())
    assert d["clock"]["week"] == 4
    assert [f["id"] for f in d["factions"]] == ["wardens", "ferrymen"]
    assert d["ledger"] and d["ledger"][0]["week"] == 3
    assert d["rumors"][0]["claim"].startswith("The ferry guild")


def test_cli_writes_updates_file_exit_zero():
    import goals as goals_mod
    with tempfile.TemporaryDirectory() as td:
        sp = pathlib.Path(td) / "state.yaml"
        sp.write_text(yaml.safe_dump(state(), allow_unicode=True), encoding="utf-8")
        op = pathlib.Path(td) / "updates.yaml"
        orig_complete = goals_mod.complete
        goals_mod.complete = lambda prompt: reply({"updates": [
            {"faction_id": "wardens", "goal": "garrison the bridge through winter",
             "reason": "week 3: the bridge reopened"}]})
        try:
            rc = goals_mod.main(["--state", str(sp), "--out", str(op)])
        finally:
            goals_mod.complete = orig_complete
        assert rc == 0
        doc = yaml.safe_load(op.read_text(encoding="utf-8"))
        assert doc["updates"][0]["faction_id"] == "wardens"
        assert doc["findings"] == []


def test_cli_budget_exhaustion_exit_3_writes_nothing():
    import goals as goals_mod
    from llm import BudgetExceeded
    with tempfile.TemporaryDirectory() as td:
        sp = pathlib.Path(td) / "state.yaml"
        sp.write_text(yaml.safe_dump(state(), allow_unicode=True), encoding="utf-8")
        op = pathlib.Path(td) / "updates.yaml"
        orig_complete = goals_mod.complete
        goals_mod.complete = lambda prompt: (_ for _ in ()).throw(
            BudgetExceeded("per-run LLM ceiling reached"))
        try:
            rc = goals_mod.main(["--state", str(sp), "--out", str(op)])
        finally:
            goals_mod.complete = orig_complete
        assert rc == 3
        assert not op.exists()


def test_cli_refuses_an_invalid_state_before_any_spend():
    with tempfile.TemporaryDirectory() as td:
        sp = pathlib.Path(td) / "state.yaml"
        sp.write_text("meta: {clock: {week: 4.5}}\n", encoding="utf-8")
        op = pathlib.Path(td) / "updates.yaml"
        rc = goals.main(["--state", str(sp), "--out", str(op)])
        assert rc == 1 and not op.exists()


if __name__ == "__main__":
    for fn in [
        test_valid_update_kept_with_cited_reason,
        test_invalid_updates_dropped_with_findings,
        test_duplicate_faction_keeps_first,
        test_empty_updates_is_a_fine_answer,
        test_unparseable_reply_reports_a_finding_not_a_crash,
        test_nested_fence_and_broken_yaml_report_findings_not_crashes,
        test_alias_bomb_reply_is_refused_not_expanded,
        test_control_characters_are_stripped_from_goal_and_reason,
        test_non_scalar_goal_and_reason_are_dropped,
        test_bare_list_reply_is_accepted,
        test_digest_carries_roster_ledger_and_rumors,
        test_cli_writes_updates_file_exit_zero,
        test_cli_budget_exhaustion_exit_3_writes_nothing,
        test_cli_refuses_an_invalid_state_before_any_spend,
    ]:
        fn()
    print("all goals tests passed")
