"""Faction projects: the pure rules, the week application, and the CLI.

Acceptance tests from docs/plans/faction-projects.md, engine side.
Zero dependencies — run directly:  python tests/test_projects.py
"""

import copy
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import effects
import projects
import resolve
import scheduler
import tick

try:
    import pytest

    @pytest.fixture(autouse=True)
    def _restore_resolver_rng():
        """set_rng replaces resolve._rng with a Random INSTANCE; this file
        must not leak that into sibling test modules whose determinism
        contract is `random.seed()` driving the module itself."""
        saved = resolve._rng
        yield
        resolve._rng = saved
except ImportError:  # direct run: nothing after us in the process
    pass

from projects import (
    apply_operations,
    apply_week,
    completion_pass,
    normalize_project,
    projects_of,
    validate_action,
    week_deltas,
)


def faction(fid, resources=None):
    return {
        "id": fid, "name": fid.title(), "kind": "test", "goal": "test",
        "resources": resources or {"coin": "medium", "muscle": "low",
                                   "legitimacy": "low"},
        "disposition": {},
    }


def state(week=8, factions=None, projects_list=None):
    s = {
        "meta": {"clock": {"week": week}},
        "city": {"name": "Testburg"},
        "factions": factions or [faction("wardens"),
                                 faction("mint")],
        "events": [], "rumors": [],
    }
    if projects_list is not None:
        s["projects"] = projects_list
    return s


def ferry(target=4, visibility="public", requirements=None, effects_list=None,
          faction_id="wardens", title="Buy the ferry concession"):
    """The canonical authoring record: the Wardens buy the ferry."""
    return {
        "faction_id": faction_id,
        "title": title,
        "objective": "Secure the concession before the spring auctions.",
        "target": target,
        "visibility": visibility,
        "requirements": requirements or {},
        "completion": {
            "text": "The Wardens acquired the ferry concession.",
            "effects": effects_list if effects_list is not None else [
                {"type": "set_resource", "faction_id": "wardens",
                 "resource": "legitimacy", "expected_before": "low",
                 "after": "medium", "reason": "Recognized standing."}],
        },
    }


def mk(state_, raw=None, id_factory=lambda: "7b03"):
    proj, errs = normalize_project(raw or ferry(), week=8, state=state_,
                                   id_factory=id_factory)
    assert not errs, errs
    state_.setdefault("projects", []).append(proj)
    return proj


def work(actor, pid, success=True, margin=4, action_id=None, action="works"):
    return {"actor": actor, "action": action, "target": "self",
            "intent": "work_project", "project_id": pid,
            "resources_committed": {}, "action_id": action_id}


def outcome(actor, margin, success, contested=False, vs=None, defensive=False):
    o = {"actor": actor, "action": f"{actor} acts", "roll": 10, "bonus": 1,
         "total": 11, "dc": 12, "contested": contested, "success": success,
         "margin": margin}
    if vs:
        o["vs"] = vs
    if defensive:
        o["defensive"] = True
    return o


# ------------------------------------------------------------- normalization


def test_normalize_fills_defaults_and_mints_ids_once():
    s = state()
    proj, errs = normalize_project({"faction_id": "wardens",
                                    "title": "Buy the ferry concession",
                                    "objective": "Own the crossing."},
                                   week=8, state=s,
                                   id_factory=lambda: "abcd")
    assert not errs, errs
    assert proj["id"] == "project-buy-the-ferry-co-abcd"
    assert proj["target"] == 4 and proj["progress"] == 0
    assert proj["status"] == "active"
    assert proj["visibility"] == "restricted", "the safe default is hidden"
    assert proj["completed_week"] is None
    # a renamed title never re-derives the id: normalization keeps an
    # existing id verbatim
    proj2, errs = normalize_project(dict(proj, title="Something else"),
                                    week=9, state=s)
    assert not errs and proj2["id"] == proj["id"]
    print("  normalize: defaults fill, ids mint once and never re-derive  OK")


def test_normalize_rejects_unknown_owner_and_bad_target():
    _, errs = normalize_project({"faction_id": "ghosts", "title": "T",
                                 "objective": "O", "target": 99},
                                week=8, state=state())
    assert any("faction_id" in e for e in errs) and any("target" in e for e in errs)
    _, errs = normalize_project({"faction_id": "wardens", "title": "T",
                                 "objective": "O",
                                 "completion": {"text": "x", "effects": [
                                     {"type": "set_resource",
                                      "faction_id": "mint",
                                      "resource": "coin", "after": "high",
                                      "reason": "transfer"}]}},
                                week=8, state=state())
    assert any("owner" in e for e in errs), \
        "completion effects touch the owner only — no transfers"
    print("  normalize: unknown owner, bad target, foreign transfer refused  OK")


def test_normalize_assigns_stable_effect_slots():
    s = state()
    proj, _errs = normalize_project(ferry(), week=8, state=s,
                                    id_factory=lambda: "7b03")
    slots = [e["slot"] for e in proj["completion"]["effects"]]
    assert len(slots) == 1 and slots[0], \
        "the persisted record carries a stable slot id even when authoring omitted one"
    print("  normalize: completion effects get stable slot ids  OK")


def test_find_state_issues_catches_hand_edits():
    s = state()
    proj = mk(s)
    dup = copy.deepcopy(proj)
    dup["id"] = proj["id"]
    s["projects"].append(dup)
    issues = projects.find_state_issues(s)
    assert any("duplicate id" in i for i in issues)
    s["projects"] = [dict(proj, progress=9)]
    assert any("progress" in i for i in projects.find_state_issues(s))
    s["projects"] = [dict(proj, target=1)]
    assert any("target" in i for i in projects.find_state_issues(s))
    mint_raw = dict(ferry())
    mint_raw["faction_id"] = "mint"
    mint_raw["title"] = "Mint works"
    mint_raw["completion"] = {"text": "Mint done.", "effects": [
        {"type": "set_resource", "faction_id": "mint", "resource": "coin",
         "after": "high", "reason": "r"}]}
    mint2 = mk(state(), mint_raw)
    other = mk(state(), dict(mint_raw, title="Mint again"))
    two = state(projects_list=[dict(mint2, status="active"),
                               dict(other, status="active")])
    assert any("one active project per faction" in i
               for i in projects.find_state_issues(two))
    print("  state issues: dup ids, bad counters, two actives refused  OK")


# ------------------------------------------------------------------ visibility


def test_visibility_owner_public_known_to():
    s = state()
    proj = mk(s, dict(ferry(), visibility="restricted"))
    assert projects.visible_to(proj, "wardens")
    assert not projects.visible_to(proj, "mint")
    proj2 = mk(state(), dict(ferry(), visibility="restricted",
                             known_to=["mint"]))
    assert projects.visible_to(proj2, "mint"), "known_to addresses the rival"
    pub = mk(state(), dict(ferry(), visibility="public"))
    assert projects.visible_to(pub, "mint")
    print("  visibility: owner always, public always, known_to addressed  OK")


def test_outsider_view_hides_counters_requirements_and_effects():
    s = state()
    proj = mk(s, dict(ferry(visibility="public", requirements={"coin": "low"})))
    view = projects.outsider_view(proj, {"ferry-crossing": "the crossing"})
    assert view["id"] == proj["id"] and view["faction_id"] == "wardens"
    assert view["progress"] in ("starting", "underway", "near completion")
    for secret in (str(proj["requirements"]), str(proj["completion"])):
        assert secret not in str(view)
    assert "target" not in view and "requirements" not in view, \
        "exact counters are DM/owner information"
    own = projects.owner_view(proj, s["factions"][0])
    assert own["progress"] == 0 and own["target"] == 4
    assert own["requirements_met"] is True
    assert "effects" not in own, "completion EFFECTS stay DM-side"
    print("  outsider view: coarse label only; owner sees full detail  OK")


# ------------------------------------------------------------------- actions


def test_validate_action_ownership_visibility_and_requirements():
    s = state()
    proj = mk(s, dict(ferry(requirements={"coin": "high"})))
    # work: own project, requirements unmet
    upd, find = validate_action({"intent": "work_project",
                                 "project_id": proj["id"]}, s, "wardens")
    assert find and "requirements unmet" in find[0]
    proj["requirements"] = {}
    upd, find = validate_action({"intent": "work_project",
                                 "project_id": proj["id"]}, s, "wardens")
    assert not find and upd == {"target": "self"}
    # a model cannot work a rival's project
    upd, find = validate_action({"intent": "work_project",
                                 "project_id": proj["id"]}, s, "mint")
    assert find and "your own" in find[0]
    # sabotage requires visibility
    proj["visibility"] = "restricted"
    upd, find = validate_action({"intent": "sabotage_project",
                                 "project_id": proj["id"]}, s, "mint")
    assert find and "not visible" in find[0]
    proj["visibility"] = "public"
    upd, find = validate_action({"intent": "sabotage_project",
                                 "project_id": proj["id"]}, s, "mint")
    assert not find and upd == {"target": "wardens"}, \
        "sabotage resolves against the OWNER — never an uncontested roll"
    print("  actions: ownership, visibility, requirements, owner-target  OK")


# ---------------------------------------------------------------- weekly math


def test_successful_work_advances_exactly_once():
    s = state()
    proj = mk(s)
    props = [work("wardens", proj["id"], action_id="act-0009-wardens")]
    outs = [outcome("wardens", 4, True)]
    tick.apply_outcomes(s, outs, props)
    report = apply_week(s, props, outs)
    assert proj["progress"] == 1, "one success, one step"
    assert report["deltas"][proj["id"]]["before"] == 0
    ev = [e for e in s["events"] if e.get("project_id")]
    assert len(ev) == 1 and ev[0]["progress_before"] == 0 \
        and ev[0]["progress_after"] == 1
    assert ev[0]["action_id"] == "act-0009-wardens", \
        "the event carries the source action id"
    assert ev[0]["event_id"], "events carry stable ids"
    print("  week: one successful work action advances exactly once  OK")


def test_multi_contest_actor_advances_once():
    s = state()
    proj = mk(s)
    props = [work("wardens", proj["id"], action_id="act-x")]
    # in two contests: a win and a bigger loss — the worst margin judges
    outs = [outcome("wardens", 3, True, contested=True, vs="mint"),
            outcome("wardens", -6, False, contested=True, vs="longshoremen")]
    tick.apply_outcomes(s, outs, props)
    apply_week(s, props, outs)
    assert proj["progress"] == 0, "a loss in any contest defeats the work"
    # and a clean sweep still advances exactly one step
    s2 = state()
    proj2 = mk(s2)
    outs2 = [outcome("wardens", 1, True, contested=True, vs="mint"),
             outcome("wardens", 5, True, contested=True, vs="longshoremen")]
    tick.apply_outcomes(s2, outs2, props)
    apply_week(s2, props, outs2)
    assert proj2["progress"] == 1, "two wins, still one step"
    print("  week: multi-contest actors move a project at most one step  OK")


def test_failed_work_leaves_progress_and_still_bites_resources():
    s = state()
    proj = mk(s)
    props = [dict(work("wardens", proj["id"]),
                  resources_committed={"coin": "low"})]
    outs = [outcome("wardens", -7, False)]
    tick.apply_outcomes(s, outs, props)
    apply_week(s, props, outs)
    assert proj["progress"] == 0
    assert s["factions"][0]["resources"]["coin"] == "low", \
        "the ordinary consequence still applies — projects add, not replace"
    print("  week: failed work moves nothing; resource loss still lands  OK")


def test_reordering_work_and_sabotage_is_identical():
    pid_raw = dict(ferry(visibility="public"))
    results = []
    for order in ("work_first", "sabotage_first"):
        s = state()
        proj = mk(s, dict(pid_raw))
        wp = [dict(work("wardens", proj["id"]), action_id="act-w")]
        sp = [{"actor": "mint", "action": "sabotage", "target": "wardens",
               "intent": "sabotage_project", "project_id": proj["id"],
               "resources_committed": {}, "action_id": "act-s"}]
        # both succeed; sabotage cancels work at every counter INCLUDING
        # the threshold: 3 -> work+1 -> 4 -> sabotage-1 -> 3: no completion
        s["projects"][0]["progress"] = 3
        first, second = (wp, sp) if order == "work_first" else (sp, wp)
        outs = [outcome("wardens", 2, True), outcome("mint", 1, True)]
        tick.apply_outcomes(s, outs, first + second)
        r = apply_week(s, first + second, outs)
        results.append((proj["progress"], proj["status"],
                        r["deltas"][proj["id"]]))
    assert results[0] == results[1], \
        f"proposal ordering changed the result: {results}"
    assert results[0][0] == 3 and results[0][1] == "active", \
        "work and sabotage cancelling at the threshold do not complete"
    print("  week: reordering simultaneous work+sabotage changes nothing  OK")


def test_inactive_defender_winning_does_not_work():
    s = state()
    proj = mk(s, dict(ferry()))
    # the wardens are attacked and win defensively without proposing —
    # a defense is not an action, so their project does not move
    props = [{"actor": "mint", "action": "raid", "target": "wardens",
              "resources_committed": {}}]
    outs = [outcome("mint", -2, False, contested=True, vs="wardens"),
            outcome("wardens", 2, True, contested=True, vs="mint",
                    defensive=True)]
    tick.apply_outcomes(s, outs, props)
    report = apply_week(s, props, outs)
    assert proj["progress"] == 0
    assert not report["deltas"]
    print("  week: an inactive defensive win performs no project work  OK")


def test_completion_applies_preapproved_effects_exactly_once():
    s = state()
    proj = mk(s)
    proj["progress"] = 3
    props = [work("wardens", proj["id"], action_id="act-w")]
    outs = [outcome("wardens", 3, True)]
    tick.apply_outcomes(s, outs, props)
    report = apply_week(s, props, outs)
    assert report["completions"] == [proj["id"]]
    assert proj["status"] == "completed" and proj["completed_week"] == 8
    assert s["factions"][0]["resources"]["legitimacy"] == "medium"
    comp_events = [e for e in s["events"] if e.get("project_id") == proj["id"]
                   and e.get("outcome") == "succeeded"
                   and "completed" in e.get("text", "")]
    assert len(comp_events) == 1, "exactly one completion event"
    # re-running the same completion (retry, replay, restart) changes nothing
    completions, _blocked, _findings = completion_pass(s, 8)
    assert completions == [] and s["factions"][0]["resources"]["legitimacy"] \
        == "medium", "the dedup guard holds on recheck"
    # and the completion carries no second ordinary reward
    print("  completion: preapproved effects apply exactly once  OK")


def test_completion_with_no_effects_still_emits_one_event():
    s = state()
    proj = mk(s, dict(ferry(effects_list=[])))
    proj["progress"] = proj["target"]
    completions, blocked, _findings = completion_pass(s, 8)
    assert completions == [proj["id"]] and not blocked
    comp = [e for e in s["events"] if e.get("project_id") == proj["id"]]
    assert len(comp) == 1, "a mechanical-free completion still records once"
    print("  completion: no-effect projects still emit exactly one event  OK")


def test_stale_precondition_blocks_whole_completion_no_partial_apply():
    s = state()
    proj = mk(s, dict(ferry(effects_list=[
        {"type": "set_resource", "faction_id": "wardens",
         "resource": "legitimacy", "expected_before": "high",
         "after": "medium", "reason": "Standing."},
        {"type": "set_field", "faction_id": "wardens", "field": "kind",
         "after": "ferry lords", "reason": "New standing."},
    ])))
    s["factions"][0]["resources"]["legitimacy"] = "low"  # stale
    proj["progress"] = proj["target"]
    completions, blocked, _findings = completion_pass(s, 8)
    assert completions == [] and len(blocked) == 1
    assert proj["status"] == "active" and proj["progress"] == proj["target"]
    assert proj.get("completion_blocked"), "the finding rides on the record"
    assert s["factions"][0]["resources"]["legitimacy"] == "low"
    assert s["factions"][0]["kind"] == "test", \
        "the effects fail TOGETHER — nothing lands half-applied"
    # a later tick rechecks deterministically: fix the precondition, no
    # LLM, no action, and the completion lands
    s["factions"][0]["resources"]["legitimacy"] = "high"
    completions, _, _ = completion_pass(s, 9)
    assert completions == [proj["id"]] and s["factions"][0]["kind"] == \
        "ferry lords"
    print("  completion: stale preconditions block whole; recheck lands  OK")


def test_overlapping_completions_block_together_not_by_order():
    """Unit-level: two at-target projects whose completions write the SAME
    faction field. (Version one cannot reach this through valid states —
    effects touch the owner only and an owner holds one active project —
    but the rule is the guard for exactly this collision, so it is pinned
    here order-free.)"""
    raw = [
        dict(ferry(faction_id="wardens", title="Wardens ferry",
                   effects_list=[
                       {"type": "set_field", "faction_id": "wardens",
                        "field": "kind", "after": "ferry lords",
                        "reason": "r1"}])),
        dict(ferry(faction_id="wardens", title="Wardens levy",
                   effects_list=[
                       {"type": "set_field", "faction_id": "wardens",
                        "field": "kind", "after": "levy lords",
                        "reason": "r2"}])),
    ]
    results = []
    for swap in (False, True):
        s = state(projects_list=[])
        for r in raw:
            p, errs = normalize_project(r, week=8, state=s,
                                        id_factory=lambda: "aa11")
            assert not errs, errs
            p["progress"] = p["target"]
            s.setdefault("projects", []).append(p)
        if swap:
            s["projects"].reverse()
        completions, blocked, _ = completion_pass(s, 8)
        results.append((sorted(completions), len(blocked),
                        s["factions"][0].get("kind"),
                        s["factions"][1].get("kind")))
    assert results[0] == results[1], \
        f"iteration order decided the overlap: {results}"
    assert results[0][0] == [] and results[0][1] == 2, \
        "overlapping writes to the same kind of spot block BOTH completions"
    print("  completion: overlapping writes block together, order-free  OK")


def test_sabotage_pulls_blocked_counter_below_target():
    s = state()
    proj = mk(s, dict(ferry(effects_list=[
        {"type": "set_resource", "faction_id": "wardens",
         "resource": "legitimacy", "expected_before": "high",
         "after": "medium", "reason": "Standing."}])))
    proj["progress"] = 4
    completions, _, _ = completion_pass(s, 8)
    assert completions == [] and proj["completion_blocked"]
    # a successful sabotage drops the counter; the blocked flag clears
    props = [{"actor": "mint", "action": "spike the ferry", "target": "wardens",
              "intent": "sabotage_project", "project_id": proj["id"],
              "resources_committed": {}, "action_id": "act-s"}]
    outs = [outcome("mint", 2, True)]
    tick.apply_outcomes(s, outs, props)
    apply_week(s, props, outs)
    assert proj["progress"] == 3, "sabotage removes one step"
    assert not proj.get("completion_blocked")
    print("  completion: sabotage reduces a blocked counter, flag clears  OK")


# ------------------------------------------------------------- consolidation


def test_tied_loss_beats_tied_success():
    """The spec's tie rule: at equal worst margin, a loss defeats a success
    — a tied defensive win cannot hide a tied attacking loss."""
    props = [{"actor": "wardens", "action": "act", "target": "mint",
              "resources_committed": {}}]
    # attacker loses a tie (margin 0), defender wins a tie (margin 0)
    outs = [outcome("wardens", 0, False, contested=True, vs="mint"),
            outcome("mint", 0, True, contested=True, vs="wardens")]
    consolidated = resolve.consolidate_outcomes(outs, props)
    assert consolidated["wardens"]["success"] is False
    deltas = week_deltas(props, outs)
    assert deltas == {}, "the tied loss cancels the work"
    print("  consolidation: equal-margin loss defeats the success  OK")


def test_no_projects_preserves_everything():
    s = state()
    props = [{"actor": "wardens", "action": "trade", "target": "self",
              "resources_committed": {}}]
    outs = [outcome("wardens", 2, True)]
    tick.apply_outcomes(s, outs, props)
    report = apply_week(s, props, outs)
    assert report["deltas"] == {} and report["completions"] == []
    assert "projects" not in s, "a project-free week writes no projects key"
    print("  week: no projects — identical behavior, no key written  OK")


# ---------------------------------------------------------------- scheduler


def test_active_project_overrides_the_quiet_gate():
    s = state(week=5)  # no history: everyone would hold
    held = scheduler.holds(s, scheduler.activation(s))
    assert set(held) == {"wardens", "mint"}, "the quiet week holds all"
    proj = mk(s, dict(ferry()))
    act = scheduler.activation(s)
    assert act["wardens"]["act"] is True
    assert "active project" in act["wardens"]["reasons"]
    assert "mint" in scheduler.holds(s, act)
    # blocked requirements create NO override
    proj["requirements"] = {"coin": "high"}
    s["factions"][0]["resources"]["coin"] = "low"
    act2 = scheduler.activation(s)
    assert act2["wardens"]["act"] is False
    # and neither does a project sitting at target awaiting completion
    proj["requirements"] = {}
    proj["progress"] = proj["target"]
    act3 = scheduler.activation(s)
    assert act3["wardens"]["act"] is False
    print("  scheduler: eligible project activates; blocked/at-target not  OK")


# ------------------------------------------------------------------- views


def test_views_carry_project_tables_and_hide_hidden_projects():
    import views
    s = state()
    own = mk(s, dict(ferry(requirements={"coin": "high"},
                           visibility="restricted")))
    secret = mk(s, dict(ferry(title="Mint's silent buy",
                              visibility="restricted")))
    secret["faction_id"] = "mint"
    s["factions"][1]["resources"]["coin"] = "low"
    wv = views.faction_view(s, "wardens")
    assert [p["id"] for p in wv["your_projects"]] == [own["id"]]
    assert wv["your_projects"][0]["progress"] == 0
    assert wv["your_projects"][0]["requirements_met"] is False
    assert "visible_projects" not in wv, "mint's restricted project is hidden"
    mv = views.faction_view(s, "mint")
    assert [p["id"] for p in mv["your_projects"]] == [secret["id"]]
    assert "visible_projects" not in mv
    own["visibility"] = "public"
    mv2 = views.faction_view(s, "mint")
    assert mv2["visible_projects"][0]["id"] == own["id"]
    assert mv2["visible_projects"][0]["progress"] in \
        ("starting", "underway", "near completion"), "label, not counter"
    print("  views: your_projects full, visible coarse, hidden invisible  OK")


# ------------------------------------------------------------------- rumors


def test_restricted_work_rumor_quotes_public_action_only():
    import rumors
    s = state()
    proj = mk(s, dict(ferry(visibility="restricted")))
    props = [dict(work("wardens", proj["id"]),
                  action="Negotiate the remaining ferry shares.")]
    outs = [outcome("wardens", 3, True)]
    tick.apply_outcomes(s, outs, props)
    # tick's run_live sets the fallback public_action; simulate it here
    s["events"][-1]["public_action"] = \
        "wardens attended to private business"
    spawned = rumors.spawn_from_events(s)
    assert spawned, "the week still makes street talk"
    assert all("ferry" not in r["claim"].lower() for r in spawned), \
        f"the restricted project leaked: {[r['claim'] for r in spawned]}"
    assert spawned[0]["claim"] == "wardens attended to private business"
    print("  rumors: restricted work quotes the public-safe line only  OK")


# ------------------------------------------------------- session effects


def test_session_progress_and_status_effects_route_through_completion():
    s = state()
    proj = mk(s)
    rec = {"source": "session:session-09", "week": 9, "events": [],
           "effects": [
               {"id": "eff-1", "type": "set_project_progress",
                "project_id": proj["id"], "after": 2,
                "expected_before": 0, "reason": "The party brokered the deal.",
                "source": "session:session-09"}]}
    _events_n, applied, errs = effects.apply_session_record(rec, s)
    assert not errs, errs
    assert applied == 1 and proj["progress"] == 2
    # reaching target completes through the shared pass
    rec2 = {"source": "session:session-10", "week": 10, "events": [],
            "effects": [
                {"id": "eff-2", "type": "set_project_progress",
                 "project_id": proj["id"], "after": 4,
                 "reason": "The final share changes hands.",
                 "source": "session:session-10"}]}
    _, _applied2, errs2 = effects.apply_session_record(rec2, s)
    assert not errs2 and proj["status"] == "completed"
    assert s["factions"][0]["resources"]["legitimacy"] == "medium"
    # re-approval changes nothing twice
    _, applied3, _ = effects.apply_session_record(rec2, s)
    assert applied3 == 0
    # completed projects take no session effects, and `completed` is
    # unreachable directly
    rec3 = {"source": "session:session-11", "week": 11, "events": [],
            "effects": [
                {"id": "eff-3", "type": "set_project_status",
                 "project_id": proj["id"], "after": "active",
                 "reason": "reopen", "source": "session:session-11"}]}
    _, _, errs3 = effects.apply_session_record(rec3, s)
    assert errs3 and "terminal" in errs3[0]
    rec4 = {"source": "session:session-11", "week": 11, "events": [],
            "effects": [
                {"id": "eff-4", "type": "set_project_status",
                 "project_id": proj["id"], "after": "completed",
                 "reason": "shortcut", "source": "session:session-11"}]}
    # a completed project refuses everything as terminal; a LIVE project
    # refuses `completed` as unreachable-by-text
    _, _, errs4a = effects.apply_session_record(rec4, s)
    assert errs4a and "terminal" in errs4a[0]
    proj["status"] = "active"  # the direct-completion refusal needs a live one
    _, _, errs4 = effects.apply_session_record(rec4, s)
    assert errs4 and "never set directly" in errs4[0]
    print("  session: project effects route through the shared completion  OK")


def test_resume_at_target_completes():
    s = state()
    proj = mk(s)
    proj["progress"] = 4
    proj["status"] = "paused"
    rec = {"source": "session:session-12", "week": 12, "events": [],
           "effects": [{"id": "eff-9", "type": "set_project_status",
                        "project_id": proj["id"], "after": "active",
                        "reason": "The DM resumes the undertaking.",
                        "source": "session:session-12"}]}
    _, _, errs = effects.apply_session_record(rec, s)
    assert not errs, errs
    assert proj["status"] == "completed", \
        "resuming a at-target project invokes the same completion"
    print("  session: resume at target completes via the shared pass  OK")


# --------------------------------------------------------------- operations


def test_operations_create_pause_resume_abandon_edit():
    s = state()
    ops = [{"id": "op-1", "op": "create", "reason": "setup",
            "project": ferry()},
           {"id": "op-2", "op": "create", "reason": "second active",
            "project": dict(ferry(title="Another"))}]
    norm, findings = apply_operations(s, ops, id_factory=lambda: "0001")
    assert findings and "one active project" in findings[0], \
        "a second active project for one owner is refused"
    assert len(norm) == 1 and projects_of(s)[0]["id"] == \
        "project-buy-the-ferry-co-0001"
    proj = projects_of(s)[0]
    _norm2, findings2 = apply_operations(s, [
        {"id": "op-3", "op": "pause", "project_id": proj["id"],
         "reason": "winter"},
        {"id": "op-4", "op": "resume", "project_id": proj["id"],
         "reason": "spring"},
        {"id": "op-5", "op": "edit", "project_id": proj["id"],
         "reason": "the auctions moved", "set": {"target": 5}},
    ])
    assert not findings2, findings2
    assert proj["status"] == "active" and proj["target"] == 5
    _, findings6 = apply_operations(s, [
        {"id": "op-6", "op": "edit", "project_id": proj["id"],
         "reason": "nope", "set": {"brainwash": True}}])
    assert any("unknown field" in f for f in findings6), \
        "an unknown field is refused with the editable list named"
    # pause preserves progress; abandon is terminal and refuses edits
    proj["progress"] = 2
    apply_operations(s, [{"id": "op-7", "op": "pause",
                          "project_id": proj["id"], "reason": "hold"}])
    assert proj["progress"] == 2 and proj["status"] == "paused"
    apply_operations(s, [{"id": "op-8", "op": "abandon",
                          "project_id": proj["id"], "reason": "done"}])
    _, findings3 = apply_operations(s, [{"id": "op-9", "op": "edit",
                                         "project_id": proj["id"],
                                         "reason": "late",
                                         "set": {"target": 3}}])
    assert findings3 and "terminal" in findings3[0]
    print("  operations: create/pause/resume/edit/abandon with all guards  OK")


def test_edit_target_below_progress_refuses():
    s = state()
    proj = mk(s)
    proj["progress"] = 3
    _, findings = apply_operations(s, [{"id": "op-1", "op": "edit",
                                        "project_id": proj["id"],
                                        "reason": "shrink",
                                        "set": {"target": 2}}])
    assert findings and "below the project's progress" in findings[0]
    print("  operations: shrinking target below progress refused  OK")


# ---------------------------------------------------------------------- CLI


def test_cli_check_preview_apply_round_trip():
    import subprocess
    root = pathlib.Path(tempfile.mkdtemp(prefix="proj-cli-"))
    try:
        s = state(week=8, projects_list=[])
        mk(s, dict(ferry(title="Existing undertaking")))
        state_path = root / "state.yaml"
        state_path.write_text(yaml.safe_dump(s), encoding="utf-8")
        pid = projects_of(s)[0]["id"]
        changes = root / "changes.yaml"
        changes.write_text(yaml.safe_dump({"operations": [
            {"id": "op-1", "op": "pause",
             "project_id": pid,
             "reason": "winter holds"}]}), encoding="utf-8")
        src = pathlib.Path(projects.__file__).parent

        def run(*extra):
            return subprocess.run(
                [sys.executable, "-X", "utf8", str(src / "projects.py"),
                 *extra, "--state", str(state_path)],
                capture_output=True, text=True, encoding="utf-8",
                check=False)  # the exit code IS the result under test

        r = run("--check", str(changes))
        assert r.returncode == 0 and "valid" in r.stdout, r.stdout + r.stderr
        assert projects_of(yaml.safe_load(
            state_path.read_text(encoding="utf-8")))[0]["status"] == "active", \
            "--check writes nothing"
        preview = root / "preview.yaml"
        r = run("--preview", str(changes), "--out", str(preview))
        assert r.returncode == 0, r.stdout + r.stderr
        doc = yaml.safe_load(preview.read_text(encoding="utf-8"))
        assert doc["operations"][0]["op"] == "pause"
        assert doc["state"]["projects"][0]["status"] == "paused"
        assert projects_of(yaml.safe_load(
            state_path.read_text(encoding="utf-8")))[0]["status"] == \
            "active", "--preview writes only the out file"
        r = run("--apply", str(changes))
        assert r.returncode == 0 and "applied" in r.stdout
        after = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        assert projects_of(after)[0]["status"] == "paused", \
            "the canonical state is the paused one"
        # a refusing batch changes nothing
        bad = root / "bad.yaml"
        bad.write_text(yaml.safe_dump({"operations": [
            {"id": "op-bad", "op": "resume",
             "project_id": "project-nope", "reason": "ghost"}]}),
            encoding="utf-8")
        r = run("--apply", str(bad))
        assert r.returncode == 1 and "unknown project_id" in r.stderr
        assert projects_of(yaml.safe_load(
            state_path.read_text(encoding="utf-8")))[0]["status"] == "paused"
        # --report lists with computed findings
        r = run("--report")
        assert r.returncode == 0
        rep = yaml.safe_load(r.stdout)
        assert rep["projects"][0]["status"] == "paused"
        assert rep["projects"][0]["progress_label"] in \
            ("starting", "underway", "near completion")
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    print("  cli: check/preview/apply/report round-trip; refusals clean  OK")


# ------------------------------------------------------------ replay + LLm


def test_replay_with_projects_verifies_dice_and_mechanics():
    root = pathlib.Path(tempfile.mkdtemp(prefix="proj-replay-"))
    saved = (tick.ROOT, tick.STATE_FILE)
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    try:
        s = state(week=8, projects_list=[])
        proj = mk(s, dict(ferry()))
        tick.STATE_FILE.write_text(yaml.safe_dump(s), encoding="utf-8")
        props = [work("wardens", proj["id"], action_id="act-0009-wardens")]
        base = copy.deepcopy(s)  # the resolver's PRE-week input, like run_live
        resolve.set_rng(4242)
        outs = resolve.resolve_tick(props, s)
        assert outs[0]["success"], "seed the roll so the work succeeds"
        tick.apply_outcomes(s, outs, props)
        report = apply_week(s, props, outs)
        import hashlib
        sha = hashlib.sha256(tick.STATE_FILE.read_bytes()).hexdigest()
        path = tick.save_replay(9, 4242, props, outs, sha,
                                base, projects_report=report)
        assert path.name == "week-9.yaml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc["project_rule_version"] == projects.RULE_VERSION
        assert doc["schema_version"] == 3
        ok, msg = tick.replay_verify(path)
        assert ok and "dice and projects" in msg, msg
        # tamper with the recorded progress: mechanics verification refuses
        doc["projects"]["deltas"][proj["id"]]["after"] = 99
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")
        ok, msg = tick.replay_verify(path)
        assert not ok and "project mechanics" in msg, msg
    finally:
        tick.ROOT, tick.STATE_FILE = saved
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    print("  replay: schema 3 verifies dice AND project mechanics  OK")


def test_replay_without_projects_section_is_honest():
    root = pathlib.Path(tempfile.mkdtemp(prefix="proj-replay-old-"))
    saved = (tick.ROOT, tick.STATE_FILE)
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    try:
        s = state(week=8, projects_list=[])
        tick.STATE_FILE.write_text(yaml.safe_dump(s), encoding="utf-8")
        props = [{"actor": "wardens", "action": "trade", "target": "self",
                  "resources_committed": {}}]
        resolve.set_rng(7)
        outs = resolve.resolve_tick(props, s)
        import hashlib
        sha = hashlib.sha256(tick.STATE_FILE.read_bytes()).hexdigest()
        path = tick.save_replay(8, 7, props, outs, sha, copy.deepcopy(s))
        ok, msg = tick.replay_verify(path)
        assert ok and "before project mechanics" in msg, \
            f"old records must not claim mechanics they never stored: {msg}"
    finally:
        tick.ROOT, tick.STATE_FILE = saved
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    print("  replay: old records verify dice only, and say so  OK")


def test_llm_validation_normalizes_project_intents():
    s = state()
    proj = mk(s, dict(ferry(visibility="public")))
    import llm
    ctx = {"own": {proj["id"]: dict(projects.owner_view(proj,
                                                     s["factions"][0]))},
           "visible": {}}
    # work forces target self
    p = llm._validate({"action": "Push the purchase.", "target": "mint",
                       "intent": "work_project", "project_id": proj["id"],
                       "resources_committed": {}}, "wardens", known_ids=("wardens", "mint", "world"),
                      project_ctx=copy.deepcopy(ctx))
    assert p["target"] == "self" and p["intent"] == "work_project"
    # a named project with no intent infers it
    p = llm._validate({"action": "Push.", "target": "self",
                       "project_id": proj["id"],
                       "resources_committed": {}}, "wardens", known_ids=("wardens", "mint", "world"),
                      project_ctx=copy.deepcopy(ctx))
    assert p["intent"] == "work_project"
    # sabotage resolves to the owner
    rival_view = projects.outsider_view(proj)
    ctx2 = {"own": {}, "visible": {proj["id"]: rival_view}}
    p = llm._validate({"action": "Spike the ferry.", "target": "self",
                       "intent": "sabotage_project",
                       "project_id": proj["id"],
                       "resources_committed": {}}, "mint", known_ids=("wardens", "mint", "world"),
                      project_ctx=copy.deepcopy(ctx2))
    assert p["target"] == "wardens", \
        "a model cannot mark a rival project as self for an easy roll"
    # unknown project id raises (correction attempt territory)
    try:
        llm._validate({"action": "X", "target": "self",
                       "intent": "work_project",
                       "project_id": "project-ghost",
                       "resources_committed": {}}, "wardens", known_ids=("wardens", "mint", "world"),
                      project_ctx=copy.deepcopy(ctx))
        raise AssertionError("unknown project_id must raise")
    except ValueError as e:
        assert "your projects" in str(e)
    # unmet requirements raise with the resource named
    own_view = projects.owner_view(proj, {"resources": {"coin": "none"}})
    own_view["requirements"] = {"coin": "low"}
    own_view["requirements_met"] = False
    own_view["requirements_unmet"] = ["coin none below the required low"]
    ctx3 = {"own": {proj["id"]: own_view}, "visible": {}}
    try:
        llm._validate({"action": "X", "target": "self",
                       "intent": "work_project", "project_id": proj["id"],
                       "resources_committed": {}}, "wardens", known_ids=("wardens", "mint", "world"),
                      project_ctx=copy.deepcopy(ctx3))
        raise AssertionError("unmet requirements must raise")
    except ValueError as e:
        assert "requirements are unmet" in str(e)
    # public_action is kept one-line
    p = llm._validate({"action": "Push.", "target": "self",
                       "intent": "work_project", "project_id": proj["id"],
                       "public_action": "wardens\n  were busy",
                       "resources_committed": {}}, "wardens", known_ids=("wardens", "mint", "world"),
                      project_ctx=copy.deepcopy(ctx))
    assert p["public_action"] == "wardens were busy"
    print("  llm: intents normalize, targets resolve, unknowns raise  OK")


def test_ferry_scenario_four_weeks_through_run_live():
    """The plan's definition of done, engine side: the ferry example runs
    through real run_live weeks with scripted (mocked) proposals — two work
    weeks, a sabotage week, the completing work weeks — then one session
    adjustment attempt through effects.py. Exactly one completion, and
    every week's replay verifies dice AND project mechanics. Seeds are
    chosen by simulating each week's dice first: no RNG luck in this test."""
    import contextlib
    import io
    import shutil

    root = pathlib.Path(tempfile.mkdtemp(prefix="proj-ferry-"))
    saved = (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR,
             tick.llm.propose, tick.llm.narrate_week, tick.save_state,
             resolve.set_rng, tick.random.SystemRandom)
    tick.ROOT = root
    tick.STATE_FILE = root / "state.yaml"
    tick.CHRONICLE_DIR = root / "chronicle"
    real_save_state = tick.save_state
    tick.save_state = lambda doc, path=None: real_save_state(
        doc, path or tick.STATE_FILE)

    class _FixedSeed:
        """tick.run_live draws its week seed from SystemRandom; hand it a
        deterministic one per week (set before each run_live)."""
        current = 1

        def __init__(self, *a):
            pass

        def getrandbits(self, n):
            return _FixedSeed.current

    def proposals_for(week, pid):
        wardens_work = {"actor": "wardens",
                        "action": "Negotiate the shares.",
                        "target": "self", "intent": "work_project",
                        "project_id": pid, "resources_committed": {},
                        "risk_acceptance": "medium"}
        if week == 11:
            return [wardens_work,
                    {"actor": "mint", "action": "Spike the ferry.",
                     "target": "wardens", "intent": "sabotage_project",
                     "project_id": pid, "resources_committed": {},
                     "risk_acceptance": "medium"}]
        if week in (9, 10, 12, 13, 14):
            return [wardens_work,
                    {"actor": "mint", "action": "mint watches the ferry.",
                     "target": "self", "resources_committed": {},
                     "risk_acceptance": "medium"}]
        return []

    def seed_where(state_snap, props, want):
        """The lowest seed whose dice give every wanted actor their
        consolidated outcome — found by simulating, not hoping."""
        for candidate in range(1, 100_000):
            resolve.set_rng(candidate)
            outs = resolve.resolve_tick([dict(x) for x in props],
                                        state_snap)
            cons = resolve.consolidate_outcomes(outs, props)
            if all(cons[a]["success"] is v for a, v in want.items()):
                return candidate
        raise AssertionError("no seed satisfies the dice contract")

    tick.llm.narrate_week = lambda *a, **k: "TITLE: The Ferry\nprose."
    try:
        s = state(week=8)
        proj = mk(s, dict(ferry(visibility="public")))  # mint may sabotage
        # week 8 history touches both factions so neither holds
        s["events"] = [{"week": 8, "actor": "wardens",
                        "text": "wardens moved on mint", "contested": True,
                        "vs": "mint", "outcome": "succeeded", "margin": 1}]
        tick.STATE_FILE.write_text(yaml.safe_dump(s), encoding="utf-8")
        tick.llm.propose = lambda view, template, actor: next(
            p for p in proposals_for(view["week"], proj["id"])
            if p["actor"] == actor)
        tick.random.SystemRandom = _FixedSeed
        want_by_week = {9: {"wardens": True}, 10: {"wardens": True},
                        # the saboteur wins the contest; the owner's work
                        # rides the SAME roll and fails (consolidation rule)
                        11: {"mint": True},
                        12: {"wardens": True}, 13: {"wardens": True},
                        14: {"wardens": True}}
        for week in (9, 10, 11, 12, 13, 14):
            fresh = yaml.safe_load(
                tick.STATE_FILE.read_text(encoding="utf-8"))
            _FixedSeed.current = seed_where(fresh,
                                            proposals_for(week, proj["id"]),
                                            want_by_week[week])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tick.run_live(fresh)

        after = yaml.safe_load(tick.STATE_FILE.read_text(encoding="utf-8"))
        assert after["meta"]["clock"]["week"] == 14
        final = projects.find_project(after, proj["id"])
        assert final["status"] == "completed" \
            and final["completed_week"] == 14
        assert final["progress"] == final["target"] == 4
        # project-level transitions, one per week (a week with both work
        # and sabotage annotates BOTH events with the same aggregate
        # before/after — that is the contract, not a per-action delta)
        moves = {}
        for e in after["events"]:
            if e.get("project_id") and e.get("progress_before") is not None:
                moves[e["week"]] = (e["progress_before"],
                                    e["progress_after"])
        transitions = [moves[w] for w in sorted(moves)]
        assert transitions == [(0, 1), (1, 2), (2, 1), (1, 2), (2, 3),
                               (3, 4)], transitions
        # the preapproved completion applied EXACTLY once
        wardens = next(f for f in after["factions"]
                       if f["id"] == "wardens")
        assert wardens["resources"]["legitimacy"] == "medium"
        comp = [e for e in after["events"]
                if e.get("event_id", "").endswith(proj["id"])]
        assert len(comp) == 1, "exactly one completion event"
        # every week's replay verifies dice AND project mechanics
        for week in (9, 10, 11, 12, 13, 14):
            path = root / "world" / "replay" / f"week-{week}.yaml"
            ok, msg = tick.replay_verify(path)
            assert ok, f"week {week}: {msg}"
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert doc["schema_version"] == 3
        # the chronicle carries the DM's project movement section
        chron_text = "\n".join(
            p.read_text(encoding="utf-8")
            for p in tick.CHRONICLE_DIR.glob("0014-*.md"))
        assert "## Projects" in chron_text and "COMPLETED" in chron_text

        # one session adjustment attempt through effects.py on the canon
        # state: a completed project is terminal — reopen is refused
        rec = {"source": "session:session-14", "week": 14, "events": [
            {"actor": "wardens",
             "text": "The party rode the wardens' ferry for free."}],
            "effects": [{"id": "eff-final", "type": "set_project_status",
                         "project_id": proj["id"], "after": "paused",
                         "reason": "reopen"}]}
        _events, _applied, errs = effects.apply_session_record(rec, after)
        assert errs and "terminal" in errs[0], \
            "a completed project takes no session effects"
    finally:
        (tick.ROOT, tick.STATE_FILE, tick.CHRONICLE_DIR,
         tick.llm.propose, tick.llm.narrate_week, tick.save_state,
         resolve.set_rng, tick.random.SystemRandom) = saved
        shutil.rmtree(root, ignore_errors=True)
    print("  ferry scenario: 5 live weeks, sabotage, one completion, "
          "replays verify  OK")


def test_llm_prompt_carries_project_guidance():
    template = (pathlib.Path(tick.__file__).parent.parent / "agents"
                / "faction.md").read_text(encoding="utf-8")
    assert "work_project" in template and "sabotage_project" in template
    assert "public_action" in template
    print("  prompt: the faction template teaches the project actions  OK")


if __name__ == "__main__":
    for fn in [
        test_normalize_fills_defaults_and_mints_ids_once,
        test_normalize_rejects_unknown_owner_and_bad_target,
        test_normalize_assigns_stable_effect_slots,
        test_find_state_issues_catches_hand_edits,
        test_visibility_owner_public_known_to,
        test_outsider_view_hides_counters_requirements_and_effects,
        test_validate_action_ownership_visibility_and_requirements,
        test_successful_work_advances_exactly_once,
        test_multi_contest_actor_advances_once,
        test_failed_work_leaves_progress_and_still_bites_resources,
        test_reordering_work_and_sabotage_is_identical,
        test_inactive_defender_winning_does_not_work,
        test_completion_applies_preapproved_effects_exactly_once,
        test_completion_with_no_effects_still_emits_one_event,
        test_stale_precondition_blocks_whole_completion_no_partial_apply,
        test_overlapping_completions_block_together_not_by_order,
        test_sabotage_pulls_blocked_counter_below_target,
        test_tied_loss_beats_tied_success,
        test_no_projects_preserves_everything,
        test_active_project_overrides_the_quiet_gate,
        test_views_carry_project_tables_and_hide_hidden_projects,
        test_restricted_work_rumor_quotes_public_action_only,
        test_session_progress_and_status_effects_route_through_completion,
        test_resume_at_target_completes,
        test_operations_create_pause_resume_abandon_edit,
        test_edit_target_below_progress_refuses,
        test_cli_check_preview_apply_round_trip,
        test_replay_with_projects_verifies_dice_and_mechanics,
        test_replay_without_projects_section_is_honest,
        test_llm_validation_normalizes_project_intents,
        test_llm_prompt_carries_project_guidance,
        test_ferry_scenario_four_weeks_through_run_live,
    ]:
        fn()
    print("all projects tests passed")
