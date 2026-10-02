"""Forecast: the real engine projected 2-3 weeks forward on scratch data,
staged as a propose-and-DISCARD projection — never canon, never occupying
the pending-proposal slot, git-ignored. Flow tests run the forecast job
with a fake tick that advances one week per invocation (a real forecast
costs API credit and belongs to the human user).
"""

import yaml

import engines
import forecasts
import proposals
from conftest import wait_for_job
from engines import write_week_flag


def _fake_tick_series(engine_root, start_week=3):
    """Each tick --live advances the ENGINE HOME's state one week and writes
    one new chronicle — the shape the studio's per-week validation expects.
    Like the real tick, each run advances whatever state it finds (a forecast
    scratch and a later real week each start from their own copy-in) and
    writes the next FREE chronicle path (reruns in one engine home must not
    collide)."""
    calls = {"n": 0}

    def run(engine_root_, script, *args, timeout=600, on_line=None,
                env=None):
        assert (script,) + args == ("src/tick.py", "--live")
        calls["n"] += 1
        world = engine_root_ / "world"
        world.mkdir(parents=True, exist_ok=True)
        prev = yaml.safe_load(
            world.joinpath("state.yaml").read_text(encoding="utf-8")) or {}
        week = int(((prev.get("meta") or {}).get("clock") or {})["week"]) + 1
        world.joinpath("state.yaml").write_text(yaml.safe_dump({
            "meta": {"clock": {"week": week, "season": "late autumn"}},
            "factions": [{"id": "wardens", "goal": "hold", "resources":
                          {"coin": "medium", "muscle": "low",
                           "legitimacy": "high"}}],
            "events": [{"week": week, "actor": "wardens",
                        "text": f"projected week {week} happened — "
                                f"succeeded, margin +4"}],
        }), encoding="utf-8")
        chron = engine_root_ / "chronicle"
        chron.mkdir(exist_ok=True)
        taken = {p.name for p in chron.glob("*.md")}
        n = 0
        while True:
            name = f"{week:04d}-projection.md" if n == 0 \
                else f"{week:04d}-projection-{n + 1}.md"
            if name not in taken:
                break
            n += 1
        (chron / name).write_text(
            f"# Week {week} — The Projection\n\nProjected prose.\n",
            encoding="utf-8")
        return 0, [f"projected week {week} written"]

    return run, calls


def _run_forecast(client, monkeypatch, tmp_spine_root, weeks=2,
                  start_week=3, fail_on_call=None):
    run, calls = _fake_tick_series(None, start_week=start_week)

    def dispatch(engine_root_, script, *args, timeout=600, on_line=None,
                 env=None):
        if fail_on_call is not None and calls["n"] + 1 == fail_on_call:
            return 1, ["tick exploded mid-forecast"]
        return run(engine_root_, script, *args, timeout=timeout,
                   on_line=on_line, env=env)

    monkeypatch.setattr(engines, "run_py", dispatch)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/forecast",
                    json={"confirm": True, "weeks": weeks})
    assert r.status_code == 202, r.text
    return wait_for_job(client, r.json()["job_id"])


def _live_state_bytes(tmp_spine_root):
    return (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
            ).read_bytes()


# ------------------------------------------------------------------ flow

def test_forecast_runs_n_weeks_stages_projection_and_touches_nothing(
        client, tmp_spine_root, monkeypatch):
    before = _live_state_bytes(tmp_spine_root)
    job = _run_forecast(client, monkeypatch, tmp_spine_root, weeks=2)
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["status"] == "active" and result["base_week"] == 3
    assert [w["week"] for w in result["weeks"]] == [4, 5]
    assert "The Projection" in result["weeks"][0]["markdown"]
    assert result["weeks"][0]["diff"]["clock"] == {"before": 3, "after": 4}

    # canon is byte-identical; no pending proposal was staged
    assert _live_state_bytes(tmp_spine_root) == before
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] is None

    # the bundle is on disk, loadable, and reported by GET /simulation
    bundle = forecasts.load("testvale", result["forecast_id"])
    assert bundle["manifest"]["kind"] == "forecast"
    assert len(bundle["weeks"]) == 2
    sim = client.get("/api/campaign/testvale/simulation").json()
    assert sim["active_forecast"] == result["forecast_id"]

    # a real week is NOT blocked by a forecast (the whole point) — and the
    # job MUST be waited out: an unwaited job thread races the monkeypatch
    # teardown, holds the campaign lock into every later test, and after
    # teardown runs the REAL engine with the REAL llm.env key (flake found
    # and reproduced 5/7 full-tier runs, 2026-09-25)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202, r.text
    week_job = wait_for_job(client, r.json()["job_id"])
    assert week_job["status"] == "done", week_job["error"]


def test_forecast_survives_restart_and_discards(
        client, tmp_spine_root, monkeypatch):
    job = _run_forecast(client, monkeypatch, tmp_spine_root, weeks=3)
    fid = job["result"]["forecast_id"]

    r = client.get("/api/campaign/testvale/simulation/forecast")
    f = r.json()["forecast"]
    assert f["forecast_id"] == fid and f["status"] == "active"
    assert [w["week"] for w in r.json()["weeks"]] == [4, 5, 6]

    r = client.post(
        f"/api/campaign/testvale/simulation/forecast/{fid}/discard")
    assert r.status_code == 200, r.text
    assert client.get(
        "/api/campaign/testvale/simulation/forecast").json()["forecast"] is None
    assert client.get(
        "/api/campaign/testvale/simulation").json()["active_forecast"] is None
    # the receipt stays on disk, flipped
    manifest = forecasts.load("testvale", fid)["manifest"]
    assert manifest["status"] == "discarded"

    # a discarded forecast stops blocking a new one
    job2 = _run_forecast(client, monkeypatch, tmp_spine_root, weeks=2)
    assert job2["status"] == "done", job2["error"]


def test_forecast_refusals(client, tmp_spine_root, monkeypatch):
    # 422: the expensive-button gate and the weeks range
    r = client.post("/api/campaign/testvale/simulation/forecast",
                    json={"confirm": True, "weeks": 4})
    assert r.status_code == 422
    r = client.post("/api/campaign/testvale/simulation/forecast",
                    json={"confirm": "true", "weeks": 2})
    assert r.status_code == 422
    r = client.post("/api/campaign/testvale/simulation/forecast",
                    json={"weeks": 2})
    assert r.status_code == 422

    # 409: a pending proposal must be resolved first (the forecast reads
    # the canon that proposal would change)
    d = tmp_spine_root / "testvale"
    state_text = (d / "simulation" / "state.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(state_text)
    proposals.create(
        "testvale", kind="session_effects", base_week=3, proposed_week=3,
        base_revisions={}, input_set_revisions={},
        artifacts={"state": state_text, "chronicles": {}, "entries": []},
        run_record={"kind": "session", "source": "session:test"},
        before_state=doc)
    r = client.post("/api/campaign/testvale/simulation/forecast",
                    json={"confirm": True, "weeks": 2})
    assert r.status_code == 409 and "awaiting review" in r.json()["error"]


def test_forecast_refused_while_one_is_active(
        client, tmp_spine_root, monkeypatch):
    _run_forecast(client, monkeypatch, tmp_spine_root, weeks=2)
    r = client.post("/api/campaign/testvale/simulation/forecast",
                    json={"confirm": True, "weeks": 2})
    assert r.status_code == 409 and "already on the table" in r.json()["error"]


def test_failed_forecast_week_is_a_job_error_and_stages_nothing(
        client, tmp_spine_root, monkeypatch):
    job = _run_forecast(client, monkeypatch, tmp_spine_root, weeks=2,
                        fail_on_call=2)
    assert job["status"] == "error"
    assert "forecast week" in job["error"]
    assert client.get(
        "/api/campaign/testvale/simulation/forecast").json()["forecast"] is None
    # the week flag did not survive the failure (boot restore is not due)
    assert not (tmp_spine_root / "engine-home" / ".week-run-flag").exists()


def test_forecast_needs_a_simulation(client, tmp_spine_root, monkeypatch):
    r = client.post("/api/campaign/no-such-camp/simulation/forecast",
                    json={"confirm": True, "weeks": 2})
    assert r.status_code == 404


# follow-ups (2026-09-25 review): gates, damaged bundles, id validation


def test_forecast_confirm_false_is_refused_before_any_spend(
        client, tmp_spine_root, monkeypatch):
    """ForecastBody declared confirm: StrictBool but nothing enforced it —
    {"confirm": false} accepted the request and would spend a full N-week
    budget. The gate is the same one a week has."""
    called = {"n": 0}

    def dispatch(engine_root_, script, *args, timeout=600, on_line=None):
        called["n"] += 1
        return 1, ["must never run"]

    monkeypatch.setattr(engines, "run_py", dispatch)
    r = client.post("/api/campaign/testvale/simulation/forecast",
                    json={"confirm": False, "weeks": 3})
    assert r.status_code == 422, r.text
    assert called["n"] == 0


def test_damaged_forecast_bundle_answers_422_not_500(
        client, tmp_spine_root, monkeypatch):
    """A corrupt week file made GET /forecast raise ForecastError all the
    way to the generic 500 handler on every poll."""
    job = _run_forecast(client, monkeypatch, tmp_spine_root, weeks=2)
    fid = job["result"]["forecast_id"]
    d = (tmp_spine_root / "testvale" / "simulation" / "forecasts" / fid)
    (d / "week-0004.yaml").write_text("{{{{ not yaml", encoding="utf-8")
    r = client.get("/api/campaign/testvale/simulation/forecast")
    assert r.status_code == 422, r.text
    assert "damaged" in r.json()["error"]
    # discard still works (it flips status only) and clears the slot
    r = client.post(
        f"/api/campaign/testvale/simulation/forecast/{fid}/discard")
    assert r.status_code == 200, r.text
    assert client.get(
        "/api/campaign/testvale/simulation").json()["active_forecast"] is None


def test_forecast_id_validation_rejects_traversal(tmp_spine_root):
    """forecasts._bundle joined the raw id — a '..' or drive-absolute id
    could leave the forecasts root (on Windows a drive-absolute join
    replaces the base entirely). proposals._bundle always validated."""
    for bad in ("..", "a/../b", r"C:\Users\evil", ""):
        try:
            forecasts.load("testvale", bad)
        except forecasts.ForecastError as e:
            assert "invalid forecast id" in str(e), (bad, e)
        else:
            raise AssertionError(f"id {bad!r} did not raise")


def test_copy_in_clears_engine_home_run_outputs(
        client, tmp_spine_root, monkeypatch):
    """The engine home's chronicle dir and replay trail are shared scratch:
    leftovers from a discarded forecast used to pollute real weeks' chronicle
    names (-2 suffixes) and a stale replay/week-N.yaml could ride into an
    unrelated campaign's proposal."""
    from engines import copy_state_in
    home = tmp_spine_root / "engine-home"
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", home)
    chron = home / "chronicle"
    chron.mkdir(parents=True)
    (chron / "0004-stale-projection.md").write_text("stale\n", encoding="utf-8")
    replay = home / "world" / "replay"
    replay.mkdir(parents=True)
    (replay / "week-4.yaml").write_text("stale: true\n", encoding="utf-8")
    (home / "world").mkdir(parents=True, exist_ok=True)
    (home / "world" / "state.yaml").write_text("old: true\n", encoding="utf-8")
    copy_state_in("testvale")
    assert list(chron.glob("*.md")) == []
    assert list(replay.glob("week-*.yaml")) == []
    # the copy-in itself still works: canon state now lives in the home
    assert "clock" in (home / "world" / "state.yaml").read_text(encoding="utf-8")
