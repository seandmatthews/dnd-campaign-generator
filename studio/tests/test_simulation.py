"""Simulation reads, the D5 confirm gate, and the clean 501/503 paths
for the engines whose CLIs land in parallel (views.py, why.py --json)."""

import engines
from conftest import wait_for_job


def test_simulation_read_shape(client, spine_root):
    r = client.get("/api/campaign/testvale/simulation")
    assert r.status_code == 200
    sim = r.json()
    assert sim["clock"] == {"week": 3, "season": "early spring"}
    assert sim["premise"].startswith("The toll bridge burned")
    assert [f["id"] for f in sim["factions"]] == ["wardens", "ferrymen"]
    assert len(sim["events"]) == 2
    assert sim["rumors"][0]["fidelity"] == "hearsay"
    assert sim["chronicles"] == [{"week": 3, "title": "The Ash Crossing",
                                  "file": "simulation/chronicle/0003-the-ash-crossing.md"}]
    assert sim["views_available"] == ["wardens", "ferrymen"]


def test_chronicle_read_and_unknown_week_404(client, spine_root):
    r = client.get("/api/campaign/testvale/simulation/chronicle/3")
    assert r.status_code == 200
    c = r.json()
    assert c["week"] == 3
    assert c["title"] == "The Ash Crossing"
    assert "## Behind the screen" in c["markdown"]
    assert client.get(
        "/api/campaign/testvale/simulation/chronicle/99").status_code == 404


def test_week_post_422_without_confirm_exactly_true(client, spine_root):
    base = "/api/campaign/testvale/simulation/week"
    for body in ({}, {"confirm": False}, {"confirm": "true"}):
        r = client.post(base, json=body)
        assert r.status_code == 422, body
        assert "error" in r.json(), body
    assert "error" in client.post(base).json()  # no body at all


def test_week_job_error_keeps_log_and_sets_error(client, tmp_spine_root, monkeypatch):
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", tmp_spine_root / "engine-home")

    def failing_tick(*args, on_line=None, **kwargs):
        if on_line:
            on_line("boom")
            on_line("traceback line")
        return 1, ["boom", "traceback line"]

    monkeypatch.setattr(engines, "run_py", failing_tick)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "tick --live failed" in job["error"]
    assert "boom" in job["log_tail"]
    # nothing copied back into the campaign on failure
    state = (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
    assert "week: 3" in state.read_text(encoding="utf-8")


def test_week_job_happy_path_copy_in_out(client, tmp_spine_root, monkeypatch):
    calls = {}

    def fake_tick(engine_root, script, *args, timeout=600, on_line=None):
        calls["cwd"] = engine_root
        calls["args"] = (script,) + args
        # engine "wrote" its fixed home state + a new chronicle
        world = engine_root / "world"
        world.mkdir(parents=True, exist_ok=True)
        prev = world / "state.yaml"
        if prev.exists():
            (world / "state.yaml.bak").write_text(prev.read_text(encoding="utf-8"),
                                                  encoding="utf-8")
        prev.write_text("meta:\n  clock:\n    week: 4\n", encoding="utf-8")
        (engine_root / "chronicle").mkdir(exist_ok=True)
        (engine_root / "chronicle" / "0004-the-gate.md").write_text(
            "# Week 4 — The Gate\n\nProse.\n\nHeld (scheduler): ferrymen\n",
            encoding="utf-8")
        return 0, ["wardens       HOLDS (quiet week)", "week 4 written"]

    monkeypatch.setattr(engines, "run_py", fake_tick)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert calls["args"] == ("src/tick.py", "--live")
    assert job["result"]["week"] == 4
    assert job["result"]["chronicle"] == {
        "week": 4, "title": "The Gate",
        "file": "simulation/chronicle/0004-the-gate.md"}
    # held: from the log's HOLDS line and the chronicle's Held line
    assert job["result"]["held"] == ["ferrymen", "wardens"]
    # copy-out landed in the campaign
    state = (tmp_spine_root / "testvale" / "simulation" / "state.yaml").read_text(
        encoding="utf-8")
    assert "week: 4" in state
    assert (tmp_spine_root / "testvale" / "simulation" / "chronicle" /
            "0004-the-gate.md").is_file()


def test_views_unknown_faction_404_and_stub_paths(client, spine_root, monkeypatch,
                                                  tmp_path):
    # never touch the real engine home from tests (engine-home is scratch, D4)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", tmp_path / "engine-home")
    r = client.get("/api/campaign/testvale/simulation/views/ghosts")
    assert r.status_code == 404

    # CLI landed: YAML passthrough
    monkeypatch.setattr(engines, "run_py", lambda *a, **k: (0, ["week: 3"]))
    ok = client.get("/api/campaign/testvale/simulation/views/wardens")
    assert ok.status_code == 200 and ok.json() == {"week": 3}

    # engine crash (nonzero rc) or empty output: honest 502, never a 500
    # and never a misleading "not landed yet" 501 (found 2026-09-23)
    monkeypatch.setattr(engines, "run_py", lambda *a, **k: (0, [""]))
    empty = client.get("/api/campaign/testvale/simulation/views/wardens")
    assert empty.status_code == 502
    monkeypatch.setattr(engines, "run_py",
                        lambda *a, **k: (1, ["Traceback ...", "KeyError: 'id'"]))
    crashed = client.get("/api/campaign/testvale/simulation/views/wardens")
    assert crashed.status_code == 502
    assert "rc=1" in crashed.json()["error"]
    assert "KeyError" in crashed.json()["error"]

    # engine binary missing entirely: clean 503
    def missing(*a, **k):
        raise RuntimeError("engine not found")
    monkeypatch.setattr(engines, "run_py", missing)
    assert client.get(
        "/api/campaign/testvale/simulation/views/wardens").status_code == 503


def test_why_passthrough_and_engine_failure_502(client, spine_root, monkeypatch,
                                                tmp_path):
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", tmp_path / "engine-home")
    body = {"week": 2, "faction": "ferrymen"}
    url = "/api/campaign/testvale/simulation/why"

    monkeypatch.setattr(engines, "run_py",
                        lambda *a, **k: (0, ['{"lines": ["WEEK 2 — one", "  built on: two"]}']))
    r = client.post(url, json=body)
    assert r.status_code == 200
    assert r.json() == {"week": 2, "faction": "ferrymen",
                        "lines": ["WEEK 2 — one", "  built on: two"]}

    # why.py has had --json since M3: a crash or non-JSON output is an
    # engine failure -> 502 with the engine's words, not a 501 "not landed"
    monkeypatch.setattr(engines, "run_py",
                        lambda *a, **k: (1, ["usage: python src/why.py <week> <actor>"]))
    r = client.post(url, json=body)
    assert r.status_code == 502
    assert "rc=1" in r.json()["error"]
    monkeypatch.setattr(engines, "run_py", lambda *a, **k: (0, ["not json at all"]))
    r = client.post(url, json=body)
    assert r.status_code == 502
    assert "non-JSON" in r.json()["error"]

    # unknown faction is checked server-side before the engine runs
    monkeypatch.setattr(engines, "run_py",
                        lambda *a, **k: (0, ['{"lines": []}']))
    r = client.post(url, json={"week": 2, "faction": "ghosts"})
    assert r.status_code == 404
