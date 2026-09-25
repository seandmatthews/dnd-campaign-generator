"""Goal review: the sim proposes faction goal updates from the ledger,
staged as a `goal_updates` proposal the DM accepts or discards exactly
like a week. The flow tests run the goal-review job with a fake engine
run (a real pass costs API credit and belongs to the human user), faking
the engine's effects.py apply step the way the real one behaves: value
change + exactly one sourced audit event + the applied-effects receipt.
"""

import pathlib

import yaml

import engines
import proposals
from conftest import wait_for_job

UPDATES = {
    "updates": [
        {"faction_id": "wardens",
         "name": "The Bridge Wardens",
         "goal": "hold the bridge until the regency expires at week 8",
         "reason": "week 3: the council decreed the regency ends at week 8; "
                   "rebuilding the toll is moot until then",
         "current_goal": "rebuild the bridge and the toll with it"},
        {"faction_id": "ferrymen",
         "name": "The Ferrymen",
         "goal": "run grain convoys past the broken crossing",
         "reason": "week 2: the ferry monopoly outlasted the freeze",
         "current_goal": "keep the crossing broken, own the river traffic"},
    ],
    "findings": ["update 3: unknown faction_id 'ghost' — dropped"],
}


def _fake_engine(updates_doc, rc=0):
    """Dispatch on script: goals.py writes its updates file; effects.py
    --check passes, --apply mutates the copy like the real engine."""

    def run(engine_root_, script, *args, timeout=600, on_line=None):
        if script == "src/goals.py":
            out = pathlib.Path(args[args.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(yaml.safe_dump(updates_doc, allow_unicode=True),
                           encoding="utf-8")
            return rc, ["[goals] proposed %d goal update(s)"
                        % len(updates_doc.get("updates") or [])]
        if script == "src/effects.py":
            mode, record_path, state_path = \
                args[0], pathlib.Path(args[1]), pathlib.Path(args[3])
            if mode == "--check":
                return rc, ["effects valid"]
            rec = yaml.safe_load(record_path.read_text(encoding="utf-8"))
            st = yaml.safe_load(state_path.read_text(encoding="utf-8"))
            week = st["meta"]["clock"]["week"]
            for eff in rec["effects"]:
                faction = next(f for f in st["factions"]
                               if f["id"] == eff["faction_id"])
                faction[eff["field"]] = eff["after"]
                st.setdefault("events", []).append({
                    "week": week, "actor": eff["faction_id"],
                    "text": eff["reason"], "effect": eff["id"],
                    "source": eff["source"]})
                st.setdefault("meta", {}).setdefault(
                    "applied_effects", []).append(eff["id"])
            state_path.write_text(
                yaml.safe_dump(st, allow_unicode=True), encoding="utf-8")
            return rc, ["applied %d" % len(rec["effects"])]
        raise AssertionError(f"unexpected engine script: {script}")

    return run


def _run_review(client, monkeypatch, tmp_spine_root, updates_doc=UPDATES,
                rc=0):
    monkeypatch.setattr(engines, "run_py",
                        _fake_engine(updates_doc, rc=rc))
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/goal-review")
    assert r.status_code == 202, r.text
    return wait_for_job(client, r.json()["job_id"])


def _live_state(tmp_spine_root):
    return yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_bytes())


# ------------------------------------------------------------------ flow

def test_goal_review_stages_a_proposal_and_touches_nothing(
        client, tmp_spine_root, monkeypatch):
    before = (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
              ).read_bytes()
    job = _run_review(client, monkeypatch, tmp_spine_root)
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["staged"] is True and result["proposed"] == 2
    assert result["kind"] == "goal_updates"
    assert result["findings"] == UPDATES["findings"]

    # the campaign is byte-identical to its pre-run version
    assert (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
            ).read_bytes() == before

    bundle = proposals.load("testvale", result["proposal_id"])
    assert bundle["manifest"]["kind"] == "goal_updates"
    assert bundle["manifest"]["status"] == "pending"
    assert len(bundle["updates"]) == 2
    # the dropped-update findings ride INTO the bundle — they used to live
    # only in the ephemeral job log, lost after a restart
    assert bundle["findings"] == UPDATES["findings"]
    assert bundle["manifest"]["run_record"]["kind"] == "goal-review"
    # the diff carries the old->new goals the review UI renders
    fields = bundle["diff"]["fields"]["wardens"]["goal"]
    assert fields["before"] == "rebuild the bridge and the toll with it"
    assert fields["after"].startswith("hold the bridge")


def test_goal_review_surfaces_findings_and_goal_changes_in_get_proposal(
        client, tmp_spine_root, monkeypatch):
    job = _run_review(client, monkeypatch, tmp_spine_root)
    pid = job["result"]["proposal_id"]
    r = client.get("/api/campaign/testvale/simulation/proposal")
    p = r.json()["proposal"]
    assert p["proposal_id"] == pid and p["kind"] == "goal_updates"
    assert len(r.json()["updates"]) == 2
    assert r.json()["findings"] == UPDATES["findings"]
    wardens = r.json()["diff"]["fields"]["wardens"]["goal"]
    assert wardens["after"].startswith("hold the bridge")


def test_goal_accept_applies_goals_exactly_once(
        client, tmp_spine_root, monkeypatch):
    job = _run_review(client, monkeypatch, tmp_spine_root)
    pid = job["result"]["proposal_id"]
    base_events = len(_live_state(tmp_spine_root).get("events") or [])

    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "goal_updates" and body["goals_changed"] == 2
    assert body["snapshot"]["state"] in ("committed", "no_change")

    state = _live_state(tmp_spine_root)
    wardens = next(f for f in state["factions"] if f["id"] == "wardens")
    assert wardens["goal"].startswith("hold the bridge")
    # one audit event per applied effect, sourced like a session effect
    audit = [e for e in state["events"]
             if e.get("source") == "goal-review:week-3"]
    assert len(audit) == 2
    assert len(state["events"]) == base_events + 2

    # accepting again is idempotent: no duplicate events, no value change
    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r.status_code == 200 and r.json().get("idempotent") is True
    state2 = _live_state(tmp_spine_root)
    assert len(state2["events"]) == len(state["events"])

    # closed proposal unblocks the next one
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_goal_review_refused_while_a_proposal_is_pending(
        client, tmp_spine_root, monkeypatch):
    _run_review(client, monkeypatch, tmp_spine_root)
    r = client.post("/api/campaign/testvale/simulation/goal-review")
    assert r.status_code == 409 and "awaiting review" in r.json()["error"]


def test_goal_review_with_no_updates_stages_nothing(
        client, tmp_spine_root, monkeypatch):
    job = _run_review(client, monkeypatch, tmp_spine_root,
                      updates_doc={"updates": [], "findings": []})
    assert job["status"] == "done", job["error"]
    assert job["result"]["staged"] is False
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_failed_goal_review_is_a_job_error_not_a_proposal(
        client, tmp_spine_root, monkeypatch):
    job = _run_review(client, monkeypatch, tmp_spine_root,
                      updates_doc=UPDATES, rc=1)
    assert job["status"] == "error"
    assert "goal review failed" in job["error"]
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_goal_review_needs_a_simulation(client, tmp_spine_root, monkeypatch):
    r = client.post("/api/campaign/no-such-camp/simulation/goal-review")
    assert r.status_code == 404
