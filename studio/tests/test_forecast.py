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
from routers.simulation import write_week_flag


def _fake_tick_series(engine_root, start_week=3):
    """Each tick --live advances the engine state one week and writes one
    new chronicle — the shape the studio's per-week validation expects.
    Like the real tick, each run writes the next FREE chronicle path
    (reruns in one engine home must not collide)."""
    calls = {"n": 0}

    def run(engine_root_, script, *args, timeout=600, on_line=None):
        assert (script,) + args == ("src/tick.py", "--live")
        calls["n"] += 1
        week = start_week + calls["n"]
        world = engine_root_ / "world"
        world.mkdir(parents=True, exist_ok=True)
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

    def dispatch(engine_root_, script, *args, timeout=600, on_line=None):
        if fail_on_call is not None and calls["n"] + 1 == fail_on_call:
            return 1, ["tick exploded mid-forecast"]
        return run(engine_root_, script, *args, timeout=timeout,
                   on_line=on_line)

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

    # a real week is NOT blocked by a forecast (the whole point)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code != 409 or "proposal" not in r.json()["error"]


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
