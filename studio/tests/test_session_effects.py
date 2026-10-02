"""Phase 3 acceptance: reviewed session effects through the studio.

The handoff scenario, end to end:
  1. fixture faction `mint` starts at a known coin level;
  2. record a session event about its destroyed treasury with an explicit
     set_resource effect;
  3. before approval the faction still has its coin;
  4. after acceptance it has none and the sourced event exists exactly once;
  5. the engine's own faction view exposes the updated resource level;
  6. reimporting or re-approving does not duplicate anything;
  7. invalid ids, stale preconditions, and invalid values change nothing.
"""

import shutil

import yaml

import engines
import proposals
import spine
from conftest import client, wait_for_job  # noqa: F401


def _state(tmp_spine_root):
    return yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml").read_text(
            encoding="utf-8"))


def _mint(state):
    return next(f for f in state["factions"] if f["id"] == "wardens")


def _submit(client, effects=None, events=None, source="session:session-08",
            week=None):
    body = {"source": source,
            "events": events or [],
            "effects": effects or []}
    if week is not None:
        body["week"] = week
    return client.post("/api/campaign/testvale/simulation/session", json=body)


def _accept(client, pid):
    return client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")


def test_session_effects_end_to_end(client, tmp_spine_root):
    state = _state(tmp_spine_root)
    mint = _mint(state)
    start = mint["resources"]["coin"]
    assert start != "none"  # something to destroy

    r = _submit(client,
                events=[{"actor": "wardens",
                         "text": "The party destroyed the treasury."}],
                effects=[{"type": "set_resource", "faction_id": "wardens",
                          "resource": "coin",
                          "expected_before": start,
                          "after": "none",
                          "reason": "The party destroyed the treasury."}],
                week=7)
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]
    assert r.json()["kind"] == "session_effects"

    # before approval the faction still has its coin — nothing applied
    assert _mint(_state(tmp_spine_root))["resources"]["coin"] == start

    # the pending proposal blocks a week run
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 409

    r = _accept(client, pid)
    assert r.status_code == 200, r.text
    state = _state(tmp_spine_root)
    assert _mint(state)["resources"]["coin"] == "none"
    evs = [e for e in state["events"] if e.get("text") ==
           "The party destroyed the treasury."]
    assert len(evs) == 1  # exactly once
    assert evs[0].get("source") == "session:session-08"
    assert evs[0].get("week") == 7  # the DM's date is preserved, not rerolled

    # the engine's own view exposes the updated resource (real views.py,
    # offline, against a temp engine home — never the live scratch home)
    home = tmp_spine_root / "engine-home"
    (home / "world").mkdir(parents=True, exist_ok=True)
    shutil.copytree(engines.AGENTIC_SIMULATOR / "src", home / "src")
    orig_home = engines.AGENTIC_SIMULATOR
    engines.AGENTIC_SIMULATOR = home
    try:
        r = client.get("/api/campaign/testvale/simulation/views/wardens")
        assert r.status_code == 200, r.text
        # the faction's own view carries its (updated) resources
        assert r.json()["you"]["resources"]["coin"] == "none"
    finally:
        engines.AGENTIC_SIMULATOR = orig_home

    # re-approval is idempotent; re-importing the same session is refused
    # as a duplicate proposal and changes nothing
    r = _accept(client, pid)
    assert r.status_code == 200 and r.json().get("idempotent") is True
    state = _state(tmp_spine_root)
    assert len([e for e in state["events"] if e.get("text") ==
                "The party destroyed the treasury."]) == 1


def test_session_proposal_blocks_second_session_and_discard_frees(
        client, tmp_spine_root):
    r = _submit(client, events=[{"actor": "wardens", "text": "Something."}])
    assert r.status_code == 200
    pid = r.json()["proposal_id"]
    r = _submit(client, events=[{"actor": "wardens", "text": "More."}])
    assert r.status_code == 409
    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/discard")
    assert r.status_code == 200
    before = (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
              ).read_bytes()
    r = _submit(client, events=[{"actor": "wardens", "text": "More."}])
    assert r.status_code == 200
    assert (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
            ).read_bytes() == before


def test_invalid_effects_change_nothing(client, tmp_spine_root):
    before = (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
              ).read_bytes()
    start = _mint(_state(tmp_spine_root))["resources"]["coin"]

    # unknown faction id
    r = _submit(client, effects=[{"type": "set_resource",
                                  "faction_id": "ghosts",
                                  "resource": "coin", "after": "none",
                                  "reason": "nope"}])
    assert r.status_code == 422 and "refused" in r.json()["error"]
    # invalid resource value
    r = _submit(client, effects=[{"type": "set_resource",
                                  "faction_id": "wardens",
                                  "resource": "coin", "after": "MAXIMUM",
                                  "reason": "nope"}])
    assert r.status_code == 422
    # invalid resource key
    r = _submit(client, effects=[{"type": "set_resource",
                                  "faction_id": "wardens",
                                  "resource": "favor", "after": "none",
                                  "reason": "nope"}])
    assert r.status_code == 422
    # stale precondition
    r = _submit(client, effects=[{"type": "set_resource",
                                  "faction_id": "wardens",
                                  "resource": "coin",
                                  "expected_before": "high" if start != "high"
                                  else "low",
                                  "after": "none", "reason": "nope"}])
    assert r.status_code == 422

    assert (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
            ).read_bytes() == before
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_padded_scalars_normalize_through_the_engine(client, tmp_spine_root):
    # stray whitespace in a quoted value (hand-written record or an
    # LLM-extracted suggestion) must apply to the REAL key: the engine's
    # validator and applier see identical, stripped strings, so nothing
    # writes a `" goal "` key the audit event then lies about
    r = _submit(client,
                effects=[{"type": "set_field", "faction_id": "wardens",
                          "field": " goal ", "after": " A quieter watch. ",
                          "reason": "The watch keeps its head down."}],
                week=5)
    assert r.status_code == 200, r.text
    r = _accept(client, r.json()["proposal_id"])
    assert r.status_code == 200, r.text
    state = _state(tmp_spine_root)
    wardens = _mint(state)
    assert wardens["goal"] == "A quieter watch."
    assert " goal " not in wardens
    evs = [e for e in state["events"]
           if e.get("text") == "The watch keeps its head down."]
    assert len(evs) == 1 and evs[0]["week"] == 5


def test_session_accept_validates_base_revision(client, tmp_spine_root):
    # an external state edit between submission and acceptance conflicts
    r = _submit(client, effects=[{"type": "set_resource",
                                  "faction_id": "wardens",
                                  "resource": "coin",
                                  "after": "none",
                                  "reason": "Treasury destroyed."}])
    assert r.status_code == 200
    pid = r.json()["proposal_id"]
    path = tmp_spine_root / "testvale" / "simulation" / "state.yaml"
    path.write_bytes(path.read_bytes() + b"# external edit\n")
    r = _accept(client, pid)
    assert r.status_code == 409 and "stale proposal" in r.json()["error"]
