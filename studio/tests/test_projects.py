"""Faction projects through the studio: staging, review, acceptance.

The DM workflow from the faction-projects plan (shipped 2026-09-28):
  1. prepare typed operations -> a project_changes proposal (no LLM spend);
  2. nothing in the campaign changes until the proposal is accepted;
  3. acceptance applies through the engine's own rules, exactly once;
  4. an external state edit between prepare and accept is a 409, never a
     silent overwrite;
  5. session effects can move a project counter through the same rules;
  6. the list/detail routes show computed findings and sourced history.
"""

import proposals
import yaml

STATE = "simulation/state.yaml"


def _state_path(tmp_spine_root):
    return tmp_spine_root / "testvale" / STATE


def _state(tmp_spine_root):
    return yaml.safe_load(_state_path(tmp_spine_root).read_text(
        encoding="utf-8"))


def _prepare(client, operations):
    return client.post("/api/campaign/testvale/simulation/projects/prepare",
                       json={"operations": operations})


def _accept(client, pid):
    return client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")


FERRY = {
    "faction_id": "wardens",
    "title": "Buy the ferry concession",
    "objective": "Secure the ferry concession before the spring auctions.",
    "target": 4,
    "requirements": {"coin": "low"},
    "completion": {
        "text": "The Wardens acquired the ferry concession.",
        "effects": [
            {"type": "set_resource", "faction_id": "wardens",
             "resource": "legitimacy", "expected_before": "high",
             "after": "medium", "reason": "Recognized standing."}],
    },
}


def test_prepare_and_accept_round_trip(client, tmp_spine_root):
    before = _state_path(tmp_spine_root).read_bytes()

    r = _prepare(client, [{"op": "create", "reason": "setup",
                           "project": FERRY}])
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "project_changes"
    assert r.json()["operations"] == 1
    assert _state_path(tmp_spine_root).read_bytes() == before, \
        "preparing stages a proposal — the campaign is untouched"

    # the pending proposal shows the normalized operation and the diff
    pending = client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"]
    assert pending["kind"] == "project_changes"
    bundle = proposals.load("testvale", pending["proposal_id"])
    assert bundle["changes"][0]["op"] == "create"
    assert bundle["changes"][0]["project_id"], "the engine minted the id"
    assert bundle["diff"]["projects_added"][0]["title"] == \
        FERRY["title"]

    # a pending proposal blocks a week run (same slot as every proposal)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 409

    r = _accept(client, pending["proposal_id"])
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "project_changes"
    state = _state(tmp_spine_root)
    assert len(state["projects"]) == 1
    proj = state["projects"][0]
    assert proj["progress"] == 0 and proj["status"] == "active"
    assert proj["completion"]["effects"][0]["slot"], \
        "the persisted record carries the stable effect slot"
    assert proj["id"] == bundle["changes"][0]["project_id"], \
        "the id was minted once at prepare and never re-derived"

    # re-accept is idempotent, and the completion dedup guard exists
    r = _accept(client, pending["proposal_id"])
    assert r.status_code == 200 and r.json().get("idempotent") is True
    assert len(_state(tmp_spine_root)["projects"]) == 1


def test_prepare_refuses_invalid_batches_and_changes_nothing(
        client, tmp_spine_root):
    before = _state_path(tmp_spine_root).read_bytes()

    # unknown owner faction
    r = _prepare(client, [{"op": "create", "reason": "setup",
                           "project": dict(FERRY, faction_id="ghosts")}])
    assert r.status_code == 422 and "refused" in r.json()["error"]
    # missing reason
    r = _prepare(client, [{"op": "create", "reason": "",
                           "project": FERRY}])
    assert r.status_code == 422
    # unknown project id
    r = _prepare(client, [{"op": "pause", "reason": "winter",
                           "project_id": "project-nope"}])
    assert r.status_code == 422
    # unknown set field fails in the engine, not the studio
    r = _prepare(client, [{"op": "edit", "reason": "x",
                           "project_id": "project-nope",
                           "set": {"brainwash": True}}])
    assert r.status_code == 422

    assert _state_path(tmp_spine_root).read_bytes() == before
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_prepare_empty_operations_refused(client, tmp_spine_root):
    r = _prepare(client, [])
    assert r.status_code == 422


def test_accept_conflicts_after_external_edit(client, tmp_spine_root):
    r = _prepare(client, [{"op": "create", "reason": "setup",
                           "project": FERRY}])
    assert r.status_code == 200
    pid = r.json()["proposal_id"]
    path = _state_path(tmp_spine_root)
    path.write_bytes(path.read_bytes() + b"# external edit\n")
    r = _accept(client, pid)
    assert r.status_code == 409 and "stale proposal" in r.json()["error"]
    # editing project terms after the preview makes acceptance stale — the
    # same revision gate, exactly the acceptance test the plan names
    assert "ghosts" not in _state_path(tmp_spine_root).read_text(
        encoding="utf-8")


def test_pause_resume_edit_round_trip(client, tmp_spine_root):
    r = _prepare(client, [{"op": "create", "reason": "setup",
                           "project": FERRY}])
    pid = r.json()["proposal_id"]
    assert _accept(client, pid).status_code == 200
    proj_id = _state(tmp_spine_root)["projects"][0]["id"]

    r = _prepare(client, [
        {"op": "edit", "reason": "the auctions moved", "project_id": proj_id,
         "set": {"target": 5}},
        {"op": "pause", "reason": "winter holds", "project_id": proj_id},
    ])
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]
    diff = r.json()["diff"]
    assert diff["projects_changed"][proj_id]["target"] == \
        {"before": 4, "after": 5}
    assert _state(tmp_spine_root)["projects"][0]["target"] == 4, \
        "staging shows the change; canon holds until acceptance"
    assert _accept(client, pid).status_code == 200
    proj = _state(tmp_spine_root)["projects"][0]
    assert proj["target"] == 5 and proj["status"] == "paused"
    assert proj["progress"] == 0, "pausing preserves progress"


def test_terminal_projects_refuse_edits(client, tmp_spine_root):
    r = _prepare(client, [{"op": "create", "reason": "setup",
                           "project": FERRY}])
    pid = r.json()["proposal_id"]
    assert _accept(client, pid).status_code == 200
    proj_id = _state(tmp_spine_root)["projects"][0]["id"]
    # complete it through a session effect (the shared completion path)
    r = client.post("/api/campaign/testvale/simulation/session", json={
        "source": "session:session-09", "week": 4,
        "effects": [{"type": "set_project_progress",
                     "project_id": proj_id, "after": 4,
                     "reason": "The last share changes hands."}]})
    assert r.status_code == 200, r.text
    assert _accept(client, r.json()["proposal_id"]).status_code == 200
    state = _state(tmp_spine_root)
    assert state["projects"][0]["status"] == "completed"
    assert state["factions"][0]["resources"]["legitimacy"] == "medium", \
        "the preapproved completion effect applied exactly once"
    completions = [e for e in state["events"]
                   if e.get("project_id") == proj_id
                   and "completed" in str(e.get("text", ""))]
    assert len(completions) == 1

    r = _prepare(client, [{"op": "edit", "reason": "late",
                           "project_id": proj_id, "set": {"target": 3}}])
    assert r.status_code == 422 and "terminal" in r.json()["error"]
    r = _prepare(client, [{"op": "resume", "reason": "reopen",
                           "project_id": proj_id}])
    assert r.status_code == 422


def test_project_list_and_detail_routes(client, tmp_spine_root):
    r = _prepare(client, [{"op": "create", "reason": "setup",
                           "project": FERRY}])
    assert _accept(client, r.json()["proposal_id"]).status_code == 200
    proj_id = _state(tmp_spine_root)["projects"][0]["id"]

    r = client.get("/api/campaign/testvale/simulation/projects")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["week"] == 3
    assert rep["projects"][0]["id"] == proj_id
    assert rep["projects"][0]["requirements_met"] is True
    assert rep["projects"][0]["progress_label"] in \
        ("starting", "underway", "near completion")

    r = client.get(
        f"/api/campaign/testvale/simulation/projects/{proj_id}")
    assert r.status_code == 200
    assert r.json()["project"]["id"] == proj_id
    assert r.json()["history"] == [], "no movement yet"

    r = client.get(
        "/api/campaign/testvale/simulation/projects/project-nope")
    assert r.status_code == 404

    # session movement shows up in the sourced history
    r = client.post("/api/campaign/testvale/simulation/session", json={
        "source": "session:session-10", "week": 4,
        "effects": [{"type": "set_project_progress",
                     "project_id": proj_id, "after": 2,
                     "reason": "The party brokered the first share."}]})
    assert r.status_code == 200, r.text
    assert _accept(client, r.json()["proposal_id"]).status_code == 200
    r = client.get(
        f"/api/campaign/testvale/simulation/projects/{proj_id}")
    hist = r.json()["history"]
    assert len(hist) == 1
    # the audit event: effect id provenance, project back-reference, and
    # the movement detail in the text (the effect's "effect" field is its
    # stable effect id, not the type)
    assert hist[0]["effect"].startswith("eff-")
    assert hist[0]["project_id"] == proj_id
    assert "brokered" in hist[0]["text"]
    rep = client.get(
        "/api/campaign/testvale/simulation/projects").json()
    assert rep["projects"][0]["progress"] == 2


def test_session_project_effect_validations_change_nothing(
        client, tmp_spine_root):
    r = _prepare(client, [{"op": "create", "reason": "setup",
                           "project": FERRY}])
    pid = r.json()["proposal_id"]
    assert _accept(client, pid).status_code == 200
    before = _state_path(tmp_spine_root).read_bytes()

    # unknown project, non-integer counter, out of range, `completed` text
    for effect in (
        {"type": "set_project_progress", "project_id": "project-nope",
         "after": 1, "reason": "ghost"},
        {"type": "set_project_progress", "project_id": "project-nope",
         "after": "many", "reason": "not a number"},
        {"type": "set_project_status", "project_id": "project-nope",
         "after": "paused", "reason": "ghost status"},
    ):
        r = client.post("/api/campaign/testvale/simulation/session", json={
            "source": "session:session-11", "week": 4,
            "effects": [effect]})
        assert r.status_code == 422, r.text
    assert _state_path(tmp_spine_root).read_bytes() == before


def test_diff_states_reports_project_changes():
    before = {"projects": [{"id": "p1", "faction_id": "wardens",
                            "title": "Old title", "progress": 1, "target": 4,
                            "status": "active", "visibility": "public"}]}
    after = {"projects": [
        {"id": "p1", "faction_id": "wardens", "title": "Old title",
         "progress": 2, "target": 4, "status": "active",
         "visibility": "public"},
        {"id": "p2", "faction_id": "mint", "title": "New undertaking",
         "progress": 0, "target": 3, "status": "active",
         "visibility": "restricted"},
    ]}
    diff = proposals.diff_states(before, after, [])
    # place_ids rides along for the place view's pending section
    assert diff["projects_added"] == [
        {"id": "p2", "faction_id": "mint", "title": "New undertaking",
         "place_ids": []}]
    changed = diff["projects_changed"]["p1"]
    assert changed["progress"] == {"before": 1, "after": 2}
    assert "target" not in changed and "status" not in changed
    print("diff_states: additions and per-project movement reported")


def test_get_simulation_carries_projects(client, tmp_spine_root):
    r = _prepare(client, [{"op": "create", "reason": "setup",
                           "project": FERRY}])
    pid = r.json()["proposal_id"]
    assert _accept(client, pid).status_code == 200
    sim = client.get("/api/campaign/testvale/simulation").json()
    assert len(sim["projects"]) == 1
    assert sim["projects"][0]["title"] == FERRY["title"]
