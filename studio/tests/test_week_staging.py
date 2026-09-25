"""Phase 2: a successful week run stages a PROPOSAL bundle — the live
campaign is byte-identical until the DM accepts the whole week.

Unit tests run build_week_entries directly; the flow tests run the week job
with a fake tick (fixture events only — a real week costs API credit and
belongs to the human user), then exercise GET proposal / accept / discard.

The old expectation (the week writes state + chronicle + a pending-week
file immediately) is intentionally obsolete: pending-week files are now
legacy timeline-only reviews, and week effects land only on accept."""

import hashlib
import json

import yaml

import engines
import proposals
import publication
import spine
from conftest import wait_for_job
from routers.simulation import build_week_entries, propose_title

WEEK4_EVENTS = [
    {"week": 4, "actor": "bastard",
     "text": "Petition the Reliquary to convene a formal examination — "
             "succeeded (contested vs mint), margin +12"},
    {"week": 4, "actor": "world",
     "text": "The season turned; the harbor ices closed two days"},
    {"week": 4, "actor": "mint",
     "text": "Open the Mint's coffers to the Longshoremen — failed "
             "(contested vs longshoremen), margin +0"},
    {"week": 3, "actor": "longshoremen",
     "text": "A week-3 event must not leak into week 4's proposal"},
]


# ------------------------------------------------------------- title rule

def test_propose_title_cuts_at_punctuation_after_four_words():
    # the parenthetical starts word 5: cut before it
    assert propose_title(
        "burned the toll bridge (contested vs wardens) — succeeded, margin +6"
    ) == "burned the toll bridge"


def test_propose_title_keeps_seven_words_without_clause_punctuation():
    # the em-dash is word 8, beyond the 7-word window: no cut
    assert propose_title(
        "raised the toll on the Vane crossing — succeeded, margin +3"
    ) == "raised the toll on the Vane crossing"


def test_propose_title_floor_and_possessives():
    # punctuation before the 4-word floor does not cut; the first clause
    # mark at word 4 (0-based) or later does
    assert propose_title("A, b, c, d — something more") == "A, b, c, d"
    assert propose_title("Run, hide, survive the winter and beyond") == \
        "Run, hide, survive the winter and beyond"
    # possessive apostrophes are not clause punctuation
    assert propose_title("The Mint's coffers stayed shut") == \
        "The Mint's coffers stayed shut"


# ------------------------------------------------------- entry building

def test_build_week_entries_one_entry_per_non_world_event():
    entries = build_week_entries(4, WEEK4_EVENTS)
    assert len(entries) == 2  # the world event and the week-3 one are out
    first, second = entries
    assert first["id"] == "pw4-1" and second["id"] == "pw4-2"
    for e in (first, second):
        assert e["when"] == "week 4"
        assert e["source"] == "sim:week-4"
        assert e["text"].strip() == e["text"] and e["text"]
    assert first["title"] == "Petition the Reliquary to convene a formal"
    assert second["title"] == "Open the Mint's coffers to the Longshoremen"


def test_build_week_entries_empty_week():
    assert build_week_entries(9, []) == []


# ------------------------------------------------------------ fake engine

def _fake_tick(engine_root, events=WEEK4_EVENTS, week=4, rc=0):
    def tick(engine_root_, script, *args, timeout=600, on_line=None):
        assert (script,) + args == ("src/tick.py", "--live")
        world = engine_root_ / "world"
        world.mkdir(parents=True, exist_ok=True)
        world.joinpath("state.yaml").write_text(yaml.safe_dump({
            "meta": {"clock": {"week": week, "season": "late autumn"},
                     "rng_seeds": {str(week): 123456789}},
            "events": events,
        }), encoding="utf-8")
        chron = engine_root_ / "chronicle"
        chron.mkdir(exist_ok=True)
        # like the real tick, each run writes the next FREE chronicle path
        taken = {p.name for p in chron.glob("*.md")}
        n = 0
        while True:
            name = f"{week:04d}-the-gate.md" if n == 0 \
                else f"{week:04d}-the-gate-{n + 1}.md"
            if name not in taken:
                break
            n += 1
        (chron / name).write_text(
            f"# Week {week} — The Gate\n\nProse.\n\nHeld (scheduler): reliquary\n",
            encoding="utf-8")
        return rc, ["week %d written" % week]
    return tick


def _run_week(client, monkeypatch, tmp_spine_root, **kwargs):
    monkeypatch.setattr(engines, "run_py", _fake_tick(None, **kwargs))
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    return wait_for_job(client, r.json()["job_id"])


def _live_state_bytes(tmp_spine_root):
    return (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
            ).read_bytes()


def _live_facts(tmp_spine_root):
    d = tmp_spine_root / "testvale"
    return {
        "state": _live_state_bytes(tmp_spine_root),
        "timeline": (d / "lore" / "timeline.yaml").read_bytes(),
        "chronicles": sorted(p.name for p in
                             (d / "simulation" / "chronicle").glob("*.md")),
    }


# ------------------------------------------------------------------ flow

def test_week_generates_proposal_and_touches_nothing(
        client, tmp_spine_root, monkeypatch):
    before = _live_facts(tmp_spine_root)
    job = _run_week(client, monkeypatch, tmp_spine_root)
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["week"] == 4 and result["base_week"] == 3
    assert result["status"] == "pending"
    assert result["held"] == ["reliquary"]
    assert result["timeline_entries"] == 2

    # every live canonical file is byte-identical to its pre-run version
    assert _live_facts(tmp_spine_root) == before

    # the bundle is on disk and complete
    pid = result["proposal_id"]
    bundle = proposals.load("testvale", pid)
    assert bundle["manifest"]["status"] == "pending"
    assert bundle["manifest"]["run_record"]["seed"] == 123456789
    assert "0004-the-gate.md" in bundle["chronicles"]
    assert len(bundle["entries"]) == 2
    assert bundle["diff"]["clock"] == {"before": 3, "after": 4}


def test_get_proposal_reads_bundle_and_accept_publishes_once(
        client, tmp_spine_root, monkeypatch):
    job = _run_week(client, monkeypatch, tmp_spine_root)
    pid = job["result"]["proposal_id"]
    before = _live_facts(tmp_spine_root)

    r = client.get("/api/campaign/testvale/simulation/proposal")
    p = r.json()["proposal"]
    assert p["proposal_id"] == pid and p["status"] == "pending"
    assert r.json()["diff"]["clock"]["after"] == 4
    assert len(r.json()["entries"]) == 2
    assert "The Gate" in r.json()["chronicles"][0]["markdown"]

    # generate another week while unresolved -> refused
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 409 and "awaiting review" in r.json()["error"]

    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["week"] == 4 and body["timeline_entries"] == 2
    assert body["snapshot"]["state"] in ("committed", "no_change")

    # exactly one week advanced: state replaced, chronicle created once
    state = yaml.safe_load(_live_state_bytes(tmp_spine_root))
    assert state["meta"]["clock"]["week"] == 4
    chron_dir = tmp_spine_root / "testvale" / "simulation" / "chronicle"
    assert sorted(p.name for p in chron_dir.glob("*.md")) == \
        before["chronicles"] + ["0004-the-gate.md"]
    timeline = yaml.safe_load(
        (tmp_spine_root / "testvale" / "lore" / "timeline.yaml").read_text(
            encoding="utf-8"))
    entries = timeline if isinstance(timeline, list) else timeline["entries"]
    # provisional pw ids get the next free t-numbers on approval (designed);
    # source and text prove these are the week-4 proposal entries
    assert [e["source"] for e in entries][-2:] == ["sim:week-4", "sim:week-4"]
    assert entries[-2]["title"] == "Petition the Reliquary to convene a formal"

    # approving again is idempotent (no duplicate events, clock stays put)
    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r.status_code == 200 and r.json().get("idempotent") is True
    state = yaml.safe_load(_live_state_bytes(tmp_spine_root))
    assert state["meta"]["clock"]["week"] == 4
    entries_now = yaml.safe_load(
        (tmp_spine_root / "testvale" / "lore" / "timeline.yaml").read_text(
            encoding="utf-8"))
    entries_now = entries_now if isinstance(entries_now, list) \
        else entries_now["entries"]
    assert len(entries_now) == len(entries)
    # the proposal is closed, so a NEW week can be generated
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_accept_after_restart_is_still_idempotent(
        client, tmp_spine_root, monkeypatch):
    job = _run_week(client, monkeypatch, tmp_spine_root)
    pid = job["result"]["proposal_id"]
    r1 = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r1.status_code == 200
    # receipts are files: a restarted server answers from the same receipt
    r2 = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r2.status_code == 200 and r2.json().get("idempotent") is True
    state = yaml.safe_load(_live_state_bytes(tmp_spine_root))
    assert state["meta"]["clock"]["week"] == 4


def test_discard_preserves_all_bytes(client, tmp_spine_root, monkeypatch):
    before = _live_facts(tmp_spine_root)
    job = _run_week(client, monkeypatch, tmp_spine_root)
    assert _live_facts(tmp_spine_root) == before
    pid = job["result"]["proposal_id"]

    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/discard")
    assert r.status_code == 200 and r.json()["discarded"] == pid
    assert _live_facts(tmp_spine_root) == before  # discard changed nothing

    bundle = proposals.load("testvale", pid)  # audit receipt kept
    assert bundle["manifest"]["status"] == "discarded"
    assert bundle["manifest"]["decided"]
    # a discarded proposal cannot be accepted
    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r.status_code == 409
    # and a NEW week can be generated from the same base
    job2 = _run_week(client, monkeypatch, tmp_spine_root)
    assert job2["status"] == "done"
    assert job2["result"]["proposal_id"] != pid


def test_external_edit_between_generation_and_accept_conflicts(
        client, tmp_spine_root, monkeypatch):
    job = _run_week(client, monkeypatch, tmp_spine_root)
    pid = job["result"]["proposal_id"]
    # Obsidian (or anything) edits the timeline after the proposal was made
    timeline = tmp_spine_root / "testvale" / "lore" / "timeline.yaml"
    timeline.write_bytes(timeline.read_bytes() + b"# external edit\n")
    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r.status_code == 409 and "stale proposal" in r.json()["error"]
    # nothing was written; discard and regenerate from the new base
    state = yaml.safe_load(_live_state_bytes(tmp_spine_root))
    assert state["meta"]["clock"]["week"] == 3
    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/discard")
    assert r.status_code == 200
    job2 = _run_week(client, monkeypatch, tmp_spine_root)
    assert job2["status"] == "done"  # fresh proposal from the new base


def test_failed_engine_leaves_no_approvable_bundle(
        client, tmp_spine_root, monkeypatch):
    before = _live_facts(tmp_spine_root)
    job = _run_week(client, monkeypatch, tmp_spine_root, rc=1)
    assert job["status"] == "error" and "tick --live failed" in job["error"]
    assert _live_facts(tmp_spine_root) == before
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_clock_not_advancing_is_generation_failure(
        client, tmp_spine_root, monkeypatch):
    def stuck_tick(engine_root_, script, *args, timeout=600, on_line=None):
        world = engine_root_ / "world"
        world.mkdir(parents=True, exist_ok=True)
        world.joinpath("state.yaml").write_text(
            "meta:\n  clock:\n    week: 3\n", encoding="utf-8")
        return 0, ["nothing happened"]

    monkeypatch.setattr(engines, "run_py", stuck_tick)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "exactly one week" in job["error"]
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_all_faction_calls_failed_is_generation_failure(
        client, tmp_spine_root, monkeypatch):
    def failed_tick(engine_root, script, *args, timeout=600, on_line=None):
        world = engine_root / "world"
        world.mkdir(parents=True, exist_ok=True)
        world.joinpath("state.yaml").write_text(yaml.safe_dump({
            "meta": {"clock": {"week": 4}}, "events": [],
        }), encoding="utf-8")
        chron = engine_root / "chronicle"
        chron.mkdir(exist_ok=True)
        (chron / "0004-dud.md").write_text(
            "# Week 4 — Dud\n\nNothing.\n", encoding="utf-8")
        return 0, ["wardens       HOLDS (proposal failed: boom)",
                    "mint          HOLDS (proposal failed: boom)"]

    monkeypatch.setattr(engines, "run_py", failed_tick)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "generation failure" in job["error"]
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None


def test_quiet_scheduler_held_week_is_a_valid_proposal(
        client, tmp_spine_root, monkeypatch):
    def quiet_tick(engine_root, script, *args, timeout=600, on_line=None):
        world = engine_root / "world"
        world.mkdir(parents=True, exist_ok=True)
        world.joinpath("state.yaml").write_text(yaml.safe_dump({
            "meta": {"clock": {"week": 4}}, "events": [],
        }), encoding="utf-8")
        chron = engine_root / "chronicle"
        chron.mkdir(exist_ok=True)
        (chron / "0004-quiet.md").write_text(
            "# Week 4 — Quiet\n\nHeld (scheduler): wardens, mint\n",
            encoding="utf-8")
        return 0, ["wardens       HOLDS (quiet week)",
                    "mint          HOLDS (resting)"]

    monkeypatch.setattr(engines, "run_py", quiet_tick)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["result"]["held"] == ["mint", "wardens"]
    assert job["result"]["timeline_entries"] == 0  # quiet, but approvable


def test_week_run_refuses_when_publication_conflict_parked(
        client, tmp_spine_root, monkeypatch):
    # a conflict-parked campaign refuses new publications; the week route
    # must surface that instead of burning API credits on an unstashable run
    with mock_publication_crash():
        try:
            publication.publish(
                "testvale", [{"path": "lore/x.yaml", "text": "x"}],
                operation="park test")
        except RuntimeError:
            pass
    (tmp_spine_root / "testvale" / "lore" / "x.yaml").write_text(
        "external\n", encoding="utf-8")
    publication.recover_campaign("testvale")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 409 and "recovery conflict" in r.json()["error"]


def mock_publication_crash():
    import unittest.mock as mock
    return mock.patch.object(
        publication, "_apply_text",
        mock.Mock(side_effect=RuntimeError("injected crash")))


# --------------------------------------------------- review-pass additions

def test_replay_trail_rides_with_the_proposal(
        client, tmp_spine_root, monkeypatch):
    """The resolver's replay trail (seed + rule_version + normalized
    proposals) is part of the proposal's identity — the previously untested
    path where the engine wrote a replay file and staging attaches it."""
    inner = _fake_tick(None)

    def fake(engine_root_, script, *args, timeout=600, on_line=None):
        rc, lines = inner(engine_root_, script, *args, timeout=timeout,
                          on_line=on_line)
        replay = engine_root_ / "world" / "replay" / "week-4.yaml"
        replay.parent.mkdir(parents=True, exist_ok=True)
        replay.write_text(yaml.safe_dump({
            "schema_version": 1, "rule_version": 2, "week": 4,
            "seed": 123456789, "proposals": [], "outcomes": [],
        }, sort_keys=False), encoding="utf-8")
        return rc, lines

    monkeypatch.setattr(engines, "run_py", fake)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    pid = job["result"]["proposal_id"]
    bundle = proposals.load("testvale", pid)
    assert bundle["manifest"]["run_record"]["rule_version"] == 2, \
        "the replay's rule version lands in the run record"
    assert bundle["manifest"]["artifacts"].get("replay") == \
        "artifacts/replay.yaml"
    assert (tmp_spine_root / "testvale" / "simulation" / "proposals" / pid
            / "artifacts" / "replay.yaml").is_file()


def test_accept_and_discard_refuse_unknown_proposal_ids(
        client, tmp_spine_root, monkeypatch):
    r = client.post(
        "/api/campaign/testvale/simulation/proposal/no-such-pid/accept")
    assert r.status_code == 404 and "no such proposal" in r.json()["error"]
    r = client.post(
        "/api/campaign/testvale/simulation/proposal/no-such-pid/discard")
    assert r.status_code == 409
    # a discarded proposal cannot be discarded twice
    job = _run_week(client, monkeypatch, tmp_spine_root)
    pid = job["result"]["proposal_id"]
    assert client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/discard"
    ).status_code == 200
    r = client.post(
        f"/api/campaign/testvale/simulation/proposal/{pid}/discard")
    assert r.status_code == 409 and "not pending" in r.json()["error"]


def test_week_run_fast_fails_while_the_engine_is_busy(
        client, tmp_spine_root):
    """The courtesy 409: a week would queue behind another campaign's
    half-hour run with no signal — the route refuses quickly instead."""
    with engines.ENGINE_LOCK:
        r = client.post("/api/campaign/testvale/simulation/week",
                        json={"confirm": True})
    assert r.status_code == 409 and "busy" in r.json()["error"]
    assert client.get(
        "/api/campaign/testvale/simulation/proposal"
    ).json()["proposal"] is None


def test_week_job_rechecks_the_pending_invariant_under_the_lock(
        client, tmp_spine_root, monkeypatch):
    """The pre-route pending check runs on the request thread — a second
    request could slip past it. The job re-checks under the campaign lock
    and refuses before writing the flag or spending anything."""
    import unittest.mock as mock
    fake_pending = {"proposal_id": "fake-1", "proposed_week": 4}
    responses = iter([None, fake_pending])
    monkeypatch.setattr(engines, "run_py", _fake_tick(None))
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    # call 1: the route's pre-check (None); call 2: the job's re-check
    # (the "raced" proposal); any later reader: back to the real answer
    monkeypatch.setattr(proposals, "find_pending",
                        lambda name: next(responses, None))
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error" and "proposal appeared" in job["error"]
    assert client.get(
        "/api/campaign/testvale/simulation/proposal"
    ).json()["proposal"] is None
    assert not engines.week_flag_path().exists(), "no flag left behind"


def test_accept_repairs_a_wedged_pending_status_from_the_receipt(
        client, tmp_spine_root, monkeypatch):
    """The crash window AFTER the receipt: publish landed, the status flip
    did not. The receipt short-circuit must repair the proposal's status,
    or a published week blocks every new week until a manual discard."""
    job = _run_week(client, monkeypatch, tmp_spine_root)
    pid = job["result"]["proposal_id"]
    key = hashlib.sha256(json.dumps(
        ["accept-proposal", "testvale", pid]).encode("utf-8")).hexdigest()
    publication.record_receipt("testvale", key, {"accepted": pid})
    # the proposal is still pending, and canon never received the week —
    # this retry walks the receipt short-circuit with nothing applied
    r = client.post(f"/api/campaign/testvale/simulation/proposal/{pid}/accept")
    assert r.status_code == 200 and r.json().get("idempotent") is True
    manifest = proposals.load("testvale", pid)["manifest"]
    assert manifest["status"] == "accepted", \
        "the short-circuit repairs the wedged status"
    # and a NEW week can be generated again (no phantom pending blocking)
    job2 = _run_week(client, monkeypatch, tmp_spine_root)
    assert job2["status"] == "done", job2["error"]


def test_session_route_guards(client, tmp_spine_root):
    """The post_session 404/422 guards: no sandbox, no source, nothing to
    record — each refused before any engine call."""
    r = client.post("/api/campaigns", json={"name": "plain-camp",
                                            "title": "Plain"})
    assert r.status_code == 201, r.text
    body = {"source": "session:1",
            "events": [{"actor": "world", "text": "x"}]}
    r = client.post("/api/campaign/plain-camp/simulation/session", json=body)
    assert r.status_code == 404 and "sandbox" in r.json()["error"]
    r = client.post("/api/campaign/no-such-camp/simulation/session",
                    json=body)
    assert r.status_code == 404

    # testvale HAS a sandbox: the source / nothing-to-record guards fire
    # (a MISSING source is pydantic's 422; whitespace-only passes the model
    # and hits the route's own readable guard)
    r = client.post("/api/campaign/testvale/simulation/session",
                    json={"source": "   ",
                          "events": [{"actor": "world", "text": "x"}]})
    assert r.status_code == 422 and "source is required" in r.json()["error"]
    r = client.post("/api/campaign/testvale/simulation/session",
                    json={"source": "session:1"})
    assert r.status_code == 422 and "nothing to record" in r.json()["error"]
