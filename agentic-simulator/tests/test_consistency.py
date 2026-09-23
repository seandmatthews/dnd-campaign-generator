"""Tests for the pre-resolution consistency checker.

Zero dependencies — run directly:  python tests/test_consistency.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from consistency import check


def _mini_state(clock_week=3, longshoremen_knows=None):
    """Miniature of world/state.yaml: the real week-2 events verbatim, one
    week-3 event, real faction names, secrets and knows lists."""
    knows = [
        "exactly how many ships come in each week",
        "the Mint's men have been asking after harbor revenue",
        "the council gave the customs house to the Mint; Tannery men took the estate's loading",
        "the strike holds — no loading, no customs inspections, until the council pays (week 3)",
    ] if longshoremen_knows is None else longshoremen_knows
    return {
        "meta": {"clock": {"week": clock_week}},
        "city": {
            "name": "Ruravaqan",
            "districts": ["the Mint quarter", "the Reliquary",
                          "the Docks", "the Tanneries"],
        },
        "factions": [
            {"id": "mint", "name": "House Ferrand (the Mint)",
             "secret": "the Doge's debt instrument was backdated by a junior "
                       "clerk, who is quietly blackmailing the house",
             "knows": [
                 "the full size of the Doge's debts",
                 "the Bastard claims parentage but appears to lack written proof",
                 "the council has attached the Doge's customs revenue to their claim (week 2)",
                 "the Bastard was caught searching the manor — she wanted papers",
                 "strikebreakers from the Tanneries now load the estate's cargo",
                 "the council ranks their debt senior to any inheritance claim (week 3)",
                 "the Bastard went for the instrument itself — the watchers held",
                 "the harbor is struck and the customs house cannot inspect (week 3)",
             ]},
            {"id": "reliquary", "name": "The Reliquary",
             "secret": "the Doge was quietly buried a day after he died; the "
                       "planned funeral casket is empty",
             "knows": ["what the Doge confessed on his deathbed"]},
            {"id": "bastard", "name": "The Bastard's Claim (Sera of Ruravaqan)",
             "secret": "her mother's letters survive — the last names a "
                       "different father; Sera has not read them all",
             "knows": ["the layout of the Doge's manor"]},
            {"id": "longshoremen", "name": "The Longshoremen",
             "secret": "the fleet sank because of corner-cutting their own "
                       "steward signed off on",
             "knows": knows},
        ],
        "events": [
            {"week": 2, "actor": "mint",
             "text": "The estate council attaches the Doge's customs revenue "
                     "to the Mint's claim."},
            {"week": 2, "actor": "reliquary",
             "text": "A grand funeral procession moves through the Docks; "
                     "alms flow toward the arrears."},
            {"week": 2, "actor": "bastard",
             "text": "Sera is caught inside the sealed manor by the Mint's "
                     "watchers; released, but marked."},
            {"week": 2, "actor": "longshoremen",
             "text": "The harbor refusal collapses — Tannery strikebreakers "
                     "take the estate's cargo."},
            {"week": 2, "actor": "marsh_gang",
             "text": "Two Marsh Gang robbers are taken during the procession; "
                     "the gang blames the church."},
            {"week": 3, "actor": "longshoremen",
             "text": "The harbor strike holds — no loading, no customs "
                     "inspections, until the council pays."},
        ],
    }


def _prop(action, actor="longshoremen"):
    return {"actor": actor, "target": "self", "action": action,
            "risk_acceptance": "medium", "resources_committed": {}}


def test_clean_proposal_is_silent():
    state = _mini_state()
    out = check(_prop("Send clerks to audit the harbor ledgers and petition "
                      "the council for priority."), state, "longshoremen")
    assert out == [], out
    print("  clean proposal: no warnings  OK")


def test_unknown_faction_warns():
    state = _mini_state()
    out = check(_prop("Send coin to the Guild of Bellfounders so they cast "
                      "the great bell before winter."), state, "longshoremen")
    assert len(out) == 1 and out[0]["kind"] == "unknown_actor", out
    assert "Bellfounders" in out[0]["detail"]
    print("  unknown actor: invented faction flagged  OK")


def test_contradicted_week_claim_warns():
    state = _mini_state()
    action = "Petition to overturn the week 2 attachment went to the Bastard."
    out = check(_prop(action), state, "longshoremen")
    assert len(out) == 1 and out[0]["kind"] == "unwitnessed_event", out
    assert "week 2" in out[0]["detail"] and "went to the Bastard" in out[0]["detail"]
    print("  unwitnessed: week-2 claim matches no recorded event  OK")


def test_known_rumor_is_silent():
    rumor = "rumor from the docks: the attachment went to the Bastard"
    state = _mini_state(longshoremen_knows=[rumor])
    action = "Petition to overturn the week 2 attachment went to the Bastard."
    out = check(_prop(action), state, "longshoremen")
    assert out == [], out
    print("  self-knowledge: claim backed by a knows entry  OK")


def test_paraphrase_of_recorded_event_is_silent():
    state = _mini_state()
    action = "Because the week three harbor strike went into force, we hold " \
             "the gates shut another week."
    out = check(_prop(action), state, "longshoremen")
    assert out == [], out
    print("  paraphrase: 2+ shared content words witness the claim  OK")


def test_last_week_resolves_to_completed_week():
    action = "With the harbor strike held last week, we keep our coin in the vault."
    assert check(_prop(action), _mini_state(clock_week=3), "longshoremen") == []
    assert check(_prop(action), _mini_state(clock_week=2), "longshoremen") != []
    print("  last week: resolves to meta.clock.week  OK")


def test_last_week_explicit_overrides_the_bumped_clock():
    """The regression: tick.py checks proposals AFTER advancing the clock, so
    'last week' resolved to the week being simulated and every legitimate
    reference to the just-completed week warned. The caller passes the
    pre-bump clock explicitly."""
    action = "With the harbor strike held last week, we keep our coin in the vault."
    # tick mid-week-4: the clock is bumped to 4; week 3 (which recorded the
    # harbor strike) is the just-completed one the proposal refers to
    state = _mini_state(clock_week=4)
    assert check(_prop(action), state, "longshoremen", last_week=3) == []
    # without the override, the bumped clock's empty week-4 bucket warns
    # (documented default for callers that have not advanced yet)
    assert check(_prop(action), state, "longshoremen") != []
    print("  last week: explicit pre-bump week wins over the bumped clock  OK")


def test_foreign_secret_warns():
    state = _mini_state()
    action = ("Spread word that the Doge was buried the day he died and the "
              "funeral casket is empty — use it to break the Bastard's claim.")
    out = check(_prop(action, actor="mint"), state, "mint")
    assert len(out) == 1 and out[0]["kind"] == "foreign_secret", out
    assert "Reliquary" in out[0]["detail"]
    print("  foreign secret: leaked reliquary secret flagged  OK")


def test_own_secret_is_silent():
    state = _mini_state()
    action = ("Quietly settle with the junior clerk who backdated the debt "
              "instrument before he talks — the blackmail ends now.")
    out = check(_prop(action, actor="mint"), state, "mint")
    assert out == [], out
    print("  own secret: no self-informant warning  OK")


def test_warnings_sorted():
    state = _mini_state()
    action = ("Tell the Guild of Bellfounders the week 2 attachment went to "
              "the Bastard, and spread it that the funeral casket is empty.")
    out = check(_prop(action), state, "longshoremen")
    kinds = [w["kind"] for w in out]
    assert kinds == ["foreign_secret", "unknown_actor", "unwitnessed_event"], out
    assert out == sorted(out, key=lambda w: (w["kind"], w["detail"])), out
    assert all(set(w) == {"kind", "detail"} for w in out)
    print("  sorted: warnings ordered by kind then detail  OK")


if __name__ == "__main__":
    for fn in [
        test_clean_proposal_is_silent,
        test_unknown_faction_warns,
        test_contradicted_week_claim_warns,
        test_known_rumor_is_silent,
        test_paraphrase_of_recorded_event_is_silent,
        test_last_week_resolves_to_completed_week,
        test_last_week_explicit_overrides_the_bumped_clock,
        test_foreign_secret_warns,
        test_own_secret_is_silent,
        test_warnings_sorted,
    ]:
        fn()
    print("all consistency tests passed")
