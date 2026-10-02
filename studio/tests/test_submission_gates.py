"""Ticket 24: the submission gates must survive the queue.

R1 — the world-gen create-versus-replace gate runs at submit time, so a
second create-only POST can slip past it while the first world is still
generating (geography not yet on disk) and, once the FIFO hands it its
turn, replace the first world without the force approval. The job now
re-checks the gate under the campaign lock and refuses with the route's
own message — nothing run, nothing spent. Force semantics unchanged.

R2 — post_week/post_forecast used to 409 whenever ANY week/forecast held
the global engine home, so this campaign's own running week blocked
queueing the next (auto-accept) week or a forecast behind it. The lock now
records its holder: same-campaign requests JOIN the queue (GM ruling —
weeks chain back-to-back; the in-job re-checks guard correctness),
cross-campaign keeps the honest 409 + estimate.

All engine work here is faked (a real FMG run or a real week costs API
credit and belongs to the human user) — but the queue threads, the
campaign locks and the engine lock are the REAL ones the routes use.
"""

import json
import threading
from pathlib import Path

import yaml

import engines
import jobs
from conftest import tiny_fmg_dump, wait_for_job


# ------------------------------------------------------------- R1: the world


def _blocking_fmg(monkeypatch, tmp_spine_root):
    """Faked FMG + import pair (same shapes as test_world_gen's): the FIRST
    headless run blocks until the test releases it, so a second world POST
    can be made while the first job is mid-generation — geography not yet
    on disk, exactly the race the ticket describes."""
    entered = threading.Event()
    release = threading.Event()
    calls = {"node": 0}

    def fake_run_node(engine_root, *args, timeout=600, on_line=None, env=None):
        calls["node"] += 1
        if on_line:
            on_line(f"headless export seed {env['FMG_SEED']}")
        if calls["node"] == 1:
            entered.set()
            assert release.wait(timeout=15), "test never released the first FMG"
        with open(f"{env['FMG_OUT']}/headless-{env['FMG_SEED']}.json", "w",
                  encoding="utf-8") as f:
            json.dump(tiny_fmg_dump(str(env["FMG_SEED"])), f)
        return 0, [f"headless-{env['FMG_SEED']}.json"]

    def fake_run_py(engine_root, script, *args, timeout=600, on_line=None,
                    env=None):
        assert script == "src/import_fmg.py"
        dump = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        seed = dump["info"]["seed"]
        candidates = engine_root / "worlds" / "candidates"
        candidates.mkdir(parents=True, exist_ok=True)
        out = candidates / f"testland-{seed}.yaml"
        out.write_text(
            "meta:\n"
            f"  seed: '{seed}'\n"
            "summary:\n"
            "  nations: 1\n"
            "  settlements: 1\n"
            "  map_name: Testland\n", encoding="utf-8")
        return 0, [f"headless-{seed}.json -> testland-{seed}.yaml: "
                   f"'Testland' (seed {seed}): 1 nations, 1 settlements, "
                   "1 cultures"]

    monkeypatch.setattr(engines, "run_node", fake_run_node)
    monkeypatch.setattr(engines, "run_py", fake_run_py)
    monkeypatch.setattr(engines, "DND_MAPPING",
                        tmp_spine_root / "dnd-mapping")
    return entered, release, calls


def _create_mode(camp):
    """Drop the fixture's world: the create-versus-replace gate must see no
    geography.yaml at submit time (that is the whole race)."""
    (camp / "world" / "geography.yaml").unlink()


def test_second_create_only_world_refuses_inside_the_queue(
        client, tmp_spine_root, monkeypatch):
    """The R1 race, end to end on the real queue: first world blocked
    mid-FMG, second create-only POST joins the FIFO, first finishes — the
    second job must REFUSE under the campaign lock (visible in its status
    and log) and the first world must stay intact."""
    camp = tmp_spine_root / "testvale"
    _create_mode(camp)
    entered, release, calls = _blocking_fmg(monkeypatch, tmp_spine_root)

    r1 = client.post("/api/campaign/testvale/world", json={"seed": "one"})
    assert r1.status_code == 202, r1.text
    assert entered.wait(timeout=5), "the first world-gen never started"

    # geography.yaml is still absent: the second create-only POST passes the
    # submit-time gate and queues (this is the race the route cannot see)
    assert not (camp / "world" / "geography.yaml").exists()
    r2 = client.post("/api/campaign/testvale/world", json={"seed": "two"})
    assert r2.status_code == 202, r2.text
    assert r2.json()["queued"] is True
    assert r2.json()["queue_position"] == 1

    release.set()
    job1 = wait_for_job(client, r1.json()["job_id"], timeout=15.0)
    assert job1["status"] == "done", job1["error"]
    job2 = wait_for_job(client, r2.json()["job_id"], timeout=15.0)
    assert job2["status"] == "error", "the queued create-only job must refuse"
    # the route's own message, plus the nothing-was-spent note
    assert "geography.yaml already exists" in job2["error"]
    assert "force:true" in job2["error"]
    assert "nothing was run or spent" in job2["error"]
    assert any("refusing" in ln for ln in job2["log_tail"]), \
        "the refusal must be visible in the job's own log"

    # the first world is intact: FMG ran exactly once and the artifacts on
    # disk still carry the FIRST seed (the refused job wrote nothing)
    assert calls["node"] == 1, "the refused job must not reach the engine"
    dump = json.loads((camp / "world" / "source.json").read_text(
        encoding="utf-8"))
    assert dump["info"]["seed"] == "one"
    assert "seed: 'one'" in (camp / "world" / "geography.yaml").read_text(
        encoding="utf-8")
    assert (camp / "world" / "map.svg").is_file()


def test_forced_second_world_still_replaces(client, tmp_spine_root,
                                            monkeypatch):
    """Force semantics preserved: a FORCED second job queued behind the
    first runs and replaces the world, exactly as a forced submit does."""
    camp = tmp_spine_root / "testvale"
    _create_mode(camp)
    entered, release, calls = _blocking_fmg(monkeypatch, tmp_spine_root)

    r1 = client.post("/api/campaign/testvale/world", json={"seed": "one"})
    assert r1.status_code == 202, r1.text
    assert entered.wait(timeout=5)

    r2 = client.post("/api/campaign/testvale/world",
                     json={"seed": "two", "force": True})
    assert r2.status_code == 202, r2.text

    release.set()
    job1 = wait_for_job(client, r1.json()["job_id"], timeout=15.0)
    assert job1["status"] == "done", job1["error"]
    job2 = wait_for_job(client, r2.json()["job_id"], timeout=15.0)
    assert job2["status"] == "done", job2["error"]

    assert calls["node"] == 2, "the forced job ran its own generation"
    dump = json.loads((camp / "world" / "source.json").read_text(
        encoding="utf-8"))
    assert dump["info"]["seed"] == "two"
    assert "seed: 'two'" in (camp / "world" / "geography.yaml").read_text(
        encoding="utf-8")


# -------------------------------------------------- R2: who holds the engine


def _advancing_tick(entered, release):
    """A fake tick that advances whatever week the engine home holds by one
    (weeks and forecasts both copy-in first) and writes the next free
    chronicle — the shapes the studio's per-week validation expects. The
    first invocation blocks until released, so a REAL queued week can be
    caught mid-run holding the campaign lock AND the real engine lock."""
    def tick(engine_root_, script, *args, timeout=600, on_line=None):
        assert (script,) + args == ("src/tick.py", "--live")
        world = engine_root_ / "world"
        world.mkdir(parents=True, exist_ok=True)
        prev = yaml.safe_load(
            world.joinpath("state.yaml").read_text(encoding="utf-8")) or {}
        week = int(((prev.get("meta") or {}).get("clock") or {})["week"]) + 1
        world.joinpath("state.yaml").write_text(yaml.safe_dump({
            "meta": {"clock": {"week": week, "season": "late autumn"},
                     "rng_seeds": {str(week): 123456789}},
            "events": [],
        }), encoding="utf-8")
        chron = engine_root_ / "chronicle"
        chron.mkdir(exist_ok=True)
        taken = {p.name for p in chron.glob("*.md")}
        n = 0
        while True:
            name = f"{week:04d}-the-gate.md" if n == 0 \
                else f"{week:04d}-the-gate-{n + 1}.md"
            if name not in taken:
                break
            n += 1
        (chron / name).write_text(
            f"# Week {week} — The Gate\n\nProse.\n", encoding="utf-8")
        if entered is not None:
            entered.set()
        if release is not None:
            assert release.wait(timeout=15), "test never released the tick"
        return 0, [f"week {week} written"]
    return tick


def _start_blocked_week(client, monkeypatch, tmp_spine_root):
    """POST a real auto-accept week and return (response, entered, release)
    once it is mid-tick — holding testvale's campaign lock and the engine
    home (recorded on the lock as testvale's)."""
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(engines, "run_py", _advancing_tick(entered, release))
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True, "auto_accept": True})
    assert r.status_code == 202, r.text
    assert entered.wait(timeout=5), "the first week never reached the engine"
    assert engines.ENGINE_LOCK.holder == "testvale", \
        "the running week must name itself as the engine-home holder"
    return r, release


def test_second_week_auto_accept_queues_and_runs_after_the_first(
        client, tmp_spine_root, monkeypatch):
    """Same-campaign contention JOINS the queue: a week running for THIS
    campaign no longer 409s the next (auto-accept) week — it queues and
    runs after it, chaining the canon clock one week at a time."""
    r1, release = _start_blocked_week(client, monkeypatch, tmp_spine_root)

    r2 = client.post("/api/campaign/testvale/simulation/week",
                     json={"confirm": True, "auto_accept": True})
    assert r2.status_code == 202, \
        f"the same-campaign probe must not 409 — the request queues: {r2.text}"
    assert r2.json()["queued"] is True
    assert r2.json()["queue_position"] == 1

    release.set()
    job1 = wait_for_job(client, r1.json()["job_id"], timeout=30.0)
    assert job1["status"] == "done", job1["error"]
    job2 = wait_for_job(client, r2.json()["job_id"], timeout=30.0)
    assert job2["status"] == "done", job2["error"]
    assert job2["result"]["week"] == 5, "week 5 chained behind week 4"
    assert job2["result"]["auto_accept"] == "approved"

    # both weeks are canon and nothing is left pending for review
    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert state["meta"]["clock"]["week"] == 5
    assert client.get("/api/campaign/testvale/simulation/proposal"
                      ).json()["proposal"] is None


def test_forecast_queues_behind_a_running_week(client, tmp_spine_root,
                                               monkeypatch):
    """Forecasts queue the same way: this campaign's week holding the
    engine home means the forecast waits its turn, not a 409 — and the
    projection starts from the week the auto-accept just published."""
    r1, release = _start_blocked_week(client, monkeypatch, tmp_spine_root)

    r2 = client.post("/api/campaign/testvale/simulation/forecast",
                     json={"confirm": True, "weeks": 2})
    assert r2.status_code == 202, \
        f"the same-campaign probe must not 409 — the forecast queues: {r2.text}"
    assert r2.json()["queued"] is True

    release.set()
    job1 = wait_for_job(client, r1.json()["job_id"], timeout=30.0)
    assert job1["status"] == "done", job1["error"]
    job2 = wait_for_job(client, r2.json()["job_id"], timeout=30.0)
    assert job2["status"] == "done", job2["error"]

    # the week published (canon week 4) and the forecast projected from it
    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert state["meta"]["clock"]["week"] == 4, \
        "exactly the week published — the forecast never touches canon"
    f = client.get("/api/campaign/testvale/simulation/forecast"
                   ).json()["forecast"]
    assert f is not None and f["base_week"] == 4


def test_cross_campaign_engine_busy_keeps_the_409_and_estimate(
        client, tmp_spine_root):
    """Cross-campaign contention is unchanged: ANOTHER campaign's week
    holding the engine home still answers the honest 409 with the
    Blocked-by estimate line — the queue is per-campaign and cannot absorb
    it. The lock is the real one, held through engine_lock_logged exactly
    the way a faked week job holds it."""
    entered = threading.Event()
    release = threading.Event()

    def foreign_week(log):
        with engines.engine_lock_logged(log, "a week run or forecast",
                                        holder="othervale"):
            entered.set()
            assert release.wait(timeout=15), "test never released the week"

    jobs.submit("sim-week", foreign_week, campaign="othervale")
    assert entered.wait(timeout=5), "the foreign week never took the home"
    try:
        r = client.post("/api/campaign/testvale/simulation/week",
                        json={"confirm": True})
        assert r.status_code == 409, r.text
        err = r.json()["error"]
        assert "engine home is busy" in err
        assert "Blocked by: a week run" in err, \
            "the estimate line must survive the holder distinction"
        assert client.get(
            "/api/campaign/testvale/simulation/proposal"
        ).json()["proposal"] is None
    finally:
        release.set()
    assert jobs.wait_all(timeout=15.0)


# ------------------------------------------------------------------ cleanup


def teardown_module(module):
    # any queue state this module left behind must not leak into later
    # tests: cancel queued jobs, then wait the workers out
    jobs.cancel_queued()
    jobs.wait_all(timeout=15.0)
