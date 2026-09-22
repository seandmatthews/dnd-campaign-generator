"""Tests for the causality inspector (src/why.py).

Zero dependencies — run directly:  python tests/test_why.py
The mini state is hand-built from the real events in world/state.yaml,
verbatim, so the chains asserted here are the real weeks 1-3.
"""

import contextlib
import io
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from why import explain, why_cli


def mini_state():
    """Weeks 1-3 of world/state.yaml verbatim; factions trimmed to id+name."""
    return {
        "factions": [
            {"id": "mint", "name": "House Ferrand (the Mint)"},
            {"id": "reliquary", "name": "The Reliquary"},
            {"id": "bastard", "name": "The Bastard's Claim (Sera of Ruravaqan)"},
            {"id": "longshoremen", "name": "The Longshoremen"},
            {"id": "marsh_gang", "name": "The Marsh Gang"},
        ],
        "events": [
            {"week": 1, "actor": "world",
             "text": "The Doge of Ruravaqan dies without naming an heir. The bells ring for three days; the creditors arrive on the fourth."},
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
             "text": "Petition the council to rank House Ferrand's debt as senior to the Bastard's inheritance claim and assign the attached customs revenue to the Mint before any inheritance is settled. — succeeded (contested vs bastard), margin +2"},
            {"week": 3, "actor": "reliquary",
             "text": "Read the tithe ledger aloud from the pulpit, name every debtor family and the exact sum owed, and declare interdiction — no rites, no consecrated burial for any debtor house until arrears are paid, and the Doge's public funeral held only on the day House Ferrand settles in full. — succeeded (contested vs marsh_gang), margin +5"},
            {"week": 3, "actor": "bastard",
             "text": "Enter the Doge's manor during the Mint's watcher rotation gap and steal or destroy the instrument House Ferrand's claim rests on. — failed (contested vs mint), margin -2"},
            {"week": 3, "actor": "longshoremen",
             "text": "Strike the harbor — halt all loading, unloading, and customs inspections until the council pays the fleet venture's back wages in full — succeeded, margin +1"},
            {"week": 3, "actor": "marsh_gang",
             "text": "Strike the Reliquary's storerooms by night and carry off the funeral wealth the church has taken in. — failed (contested vs reliquary), margin -5"},
        ],
    }


def chain_state():
    """A synthetic 5-week token chain: each event builds on the week before."""
    return {
        "factions": [{"id": a, "name": a.upper()} for a in ("aaa", "bbb", "ccc", "ddd")],
        "events": [
            {"week": 1, "actor": "world", "text": "The harbor council sealed the charter."},
            {"week": 2, "actor": "aaa", "text": "The charter council revealed the ledger."},
            {"week": 3, "actor": "bbb", "text": "The ledger revealed the vault deed."},
            {"week": 4, "actor": "ccc", "text": "The vault deed buried the crown."},
            {"week": 5, "actor": "ddd", "text": "The crown vault gleamed at dawn."},
        ],
    }


def test_anchor_rendered_first():
    lines = explain(mini_state(), 3, "bastard")
    assert lines[0].startswith("WEEK 3 — The Bastard's Claim (Sera of Ruravaqan): "), lines[0]
    assert "Enter the Doge's manor" in lines[0]
    assert "margin -2" in lines[0]
    print("  anchor: found, rendered first with faction name  OK")


def test_contest_partner_with_margin_label():
    lines = explain(mini_state(), 3, "bastard")
    assert lines[1].lstrip().startswith("contested against"), lines[1]
    assert "House Ferrand (the Mint)" in lines[1], "contest names the opponent"
    assert "margin -2" in lines[1], "margin is the anchor's own"
    assert "Petition the council" in lines[1], "partner's same-week event appears"
    print("  contest: partner event shown with margin label  OK")


def test_buildup_link_for_manor_attempt():
    lines = explain(mini_state(), 3, "bastard")
    built = [l for l in lines if l.lstrip().startswith("built on:")]
    assert len(built) == 1, f"expected one build-on, got {built}"
    assert built[0].startswith("  built on: "), built[0]
    assert "Sera is caught inside the sealed manor" in built[0]
    assert "(week 2, bastard)" in built[0]
    print("  build-up: manor attempt traced to the week-2 catching  OK")


def test_mint_petition_builds_on_customs_attachment():
    lines = explain(mini_state(), 3, "mint")
    assert lines[1].lstrip().startswith("contested against"), lines[1]
    assert "margin +2" in lines[1] and "Enter the Doge's manor" in lines[1]
    built = [l for l in lines if l.lstrip().startswith("built on:")]
    assert len(built) == 1, f"expected one build-on, got {built}"
    assert "The estate council attaches the Doge's customs revenue" in built[0]
    assert "(week 2, mint)" in built[0]
    print("  build-up: petition traced to the week-2 customs attachment  OK")


def test_event_without_history_is_a_bare_line():
    lines = explain(mini_state(), 2, "longshoremen")
    assert lines == ["WEEK 2 — The Longshoremen: The harbor refusal collapses — "
                     "Tannery strikebreakers take the estate's cargo."], lines
    print("  no history: anchor only, no contest or build-on lines  OK")


def test_unknown_event():
    assert explain(mini_state(), 9, "mint") == ["no recorded event: week 9, actor mint"]
    assert explain(mini_state(), 3, "ghost") == ["no recorded event: week 3, actor ghost"]
    print("  unknown: no-recorded-event line  OK")


def test_depth_capped_at_three_links():
    lines = explain(chain_state(), 5, "ddd")
    built = [l for l in lines if l.lstrip().startswith("built on:")]
    assert len(built) == 3, f"expected 3 build-ons, got {len(built)}"
    assert "(week 4, ccc)" in built[0] and "(week 3, bbb)" in built[1] and "(week 2, aaa)" in built[2]
    assert built[0].startswith("  built on:") and built[1].startswith("    built on:") \
        and built[2].startswith("      built on:"), "indent grows one level per link"
    assert "The harbor council sealed the charter" not in "\n".join(lines), "week 1 link is cut off"
    print("  depth: walk stops after 3 build-on links  OK")


def test_cli_exit_codes():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert why_cli(mini_state(), ["why.py", "3", "bastard"]) == 0
    assert "WEEK 3" in buf.getvalue() and "built on:" in buf.getvalue()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert why_cli(mini_state(), ["why.py", "9", "mint"]) == 1
    assert "no recorded event: week 9, actor mint" in buf.getvalue()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert why_cli(mini_state(), ["why.py", "oops", "mint"]) == 1
    print("  cli: 0 for a known event, 1 for unknown or bad args  OK")


def test_cli_json_flag():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert why_cli(mini_state(), ["why.py", "3", "bastard", "--json"]) == 0
    doc = json.loads(buf.getvalue())
    assert doc["week"] == 3 and doc["faction"] == "bastard"
    assert doc["lines"] == explain(mini_state(), 3, "bastard"), \
        "json lines are the exact strings the human format prints"
    assert isinstance(doc["lines"], list) and all(isinstance(l, str) for l in doc["lines"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert why_cli(mini_state(), ["why.py", "--json", "3", "bastard"]) == 0, \
            "--json works anywhere in the args"
    assert json.loads(buf.getvalue()) == doc
    print("  json: --json emits one object with week/faction/lines, flag position free  OK")


def test_cli_json_unknown_event():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert why_cli(mini_state(), ["why.py", "9", "mint", "--json"]) == 1
    doc = json.loads(buf.getvalue())
    assert doc == {"week": 9, "faction": "mint",
                   "lines": ["no recorded event: week 9, actor mint"]}
    print("  json unknown: still exit 1, lines carry the no-recorded-event line  OK")


def test_cli_default_output_unchanged():
    expected = "\n".join(explain(mini_state(), 3, "bastard")) + "\n"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert why_cli(mini_state(), ["why.py", "3", "bastard"]) == 0
    assert buf.getvalue() == expected, "default output is still the rendered block"
    assert "--json" not in buf.getvalue() and "{" != buf.getvalue()[0]
    print("  default: without --json the human block prints exactly as before  OK")


if __name__ == "__main__":
    for fn in [
        test_anchor_rendered_first,
        test_contest_partner_with_margin_label,
        test_buildup_link_for_manor_attempt,
        test_mint_petition_builds_on_customs_attachment,
        test_event_without_history_is_a_bare_line,
        test_unknown_event,
        test_depth_capped_at_three_links,
        test_cli_exit_codes,
        test_cli_json_flag,
        test_cli_json_unknown_event,
        test_cli_default_output_unchanged,
    ]:
        fn()
    print("all why tests passed")
