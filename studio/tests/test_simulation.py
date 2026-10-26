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


def test_cp1252_chronicle_degrades_not_500s(client, tmp_spine_root):
    """The regression: one Notepad-resaved (cp1252) chronicle raised
    UnicodeDecodeError from the whole Simulation tab's GET."""
    chron = tmp_spine_root / "testvale" / "simulation" / "chronicle"
    (chron / "0004-resaved-by-notepad.md").write_bytes(
        "TITLE: The Curly \u2018Quote\u2019 Week\n\nThe doge\u2019s ledger — cp1252."
        .encode("cp1252"))
    # the list degrades that file but keeps serving the good one
    r = client.get("/api/campaign/testvale/simulation")
    assert r.status_code == 200
    entries = {c["week"]: c for c in r.json()["chronicles"]}
    assert entries[3]["title"] == "The Ash Crossing"
    assert entries[4]["title"] == "(unreadable — not UTF-8)"
    # the read degrades to a lossless latin-1 decode instead of a 500
    # (cp1252 smart quotes come back as control chars — readable prose wins)
    r2 = client.get("/api/campaign/testvale/simulation/chronicle/4")
    assert r2.status_code == 200
    assert "The doge" in r2.json()["markdown"]


def test_week_post_422_without_confirm_exactly_true(client, spine_root):
    base = "/api/campaign/testvale/simulation/week"
    for body in ({}, {"confirm": False}, {"confirm": "true"}):
        r = client.post(base, json=body)
        assert r.status_code == 422, body
        assert "error" in r.json(), body
    assert "error" in client.post(base).json()  # no body at all


def test_week_job_error_keeps_log_and_sets_error(client, tmp_spine_root, monkeypatch):
    monkeypatch.setattr(engines, "ENGINE_HOME", tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME", tmp_spine_root / "chronicle")

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


def test_week_job_happy_path_proposal_not_copy_out(
        client, tmp_spine_root, monkeypatch):
    """Phase 2 contract: the week job copies the campaign state INTO the
    engine home (scratch), runs, and stages a PROPOSAL — the engine's output
    is never copied back into the campaign directly."""
    calls = {}

    def fake_tick(engine_root, script, *args, timeout=600, on_line=None,
                  env=None):
        calls["cwd"] = engine_root
        calls["args"] = (script,) + args
        # engine "wrote" its fixed home state + a new chronicle
        world = engines.ENGINE_HOME
        world.mkdir(parents=True, exist_ok=True)
        prev = world / "state.yaml"
        if prev.exists():
            (world / "state.yaml.bak").write_text(prev.read_text(encoding="utf-8"),
                                                  encoding="utf-8")
        prev.write_text("meta:\n  clock:\n    week: 4\n"
                        "meta_extra: kept\n", encoding="utf-8")
        engines.engine_chronicle_dir().mkdir(exist_ok=True)
        (engines.engine_chronicle_dir() / "0004-the-gate.md").write_text(
            "# Week 4 — The Gate\n\nProse.\n\nHeld (scheduler): ferrymen\n",
            encoding="utf-8")
        return 0, ["wardens       HOLDS (quiet week)", "week 4 written"]

    monkeypatch.setattr(engines, "run_py", fake_tick)
    monkeypatch.setattr(engines, "ENGINE_HOME", tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME", tmp_spine_root / "chronicle")
    campaign_state_before = (tmp_spine_root / "testvale" / "simulation" /
                             "state.yaml").read_bytes()
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert calls["args"] == ("src/tick.py", "--live")
    assert job["result"]["week"] == 4
    assert job["result"]["status"] == "pending"
    assert job["result"]["proposal_id"]
    # held: from the log's HOLDS line and the chronicle's Held line
    assert job["result"]["held"] == ["ferrymen", "wardens"]
    # copy-in happened (the engine home saw the campaign state) ...
    engine_state = engines.engine_state_path()
    assert engine_state.is_file()
    # ... but nothing was copied OUT: the campaign is byte-identical
    assert (tmp_spine_root / "testvale" / "simulation" / "state.yaml"
            ).read_bytes() == campaign_state_before
    assert not (tmp_spine_root / "testvale" / "simulation" / "chronicle" /
                "0004-the-gate.md").exists()

    # ticket 66: the run record carries the LLM tally even when this fake
    # engine printed no meter line (zero metered calls) — the field is part
    # of the record's shape, not a sometimes-thing
    rec = client.get(
        "/api/campaign/testvale/simulation/proposal"
    ).json()["proposal"]["run_record"]
    assert rec["llm_calls"] == 0 and rec["llm_tokens"] == 0


def test_week_run_record_carries_the_engine_tally(client, tmp_spine_root,
                                                  monkeypatch):
    """Ticket 66: the week's run record carries the engine's per-run LLM
    tally — llm.py prints one cumulative `run meter` line per POST into the
    transcript, and the LAST one is the run's total. Identity without usage
    made the priciest flow the least metered one."""
    def fake_tick(engine_root, script, *args, timeout=600, on_line=None,
                  env=None):
        world = engines.ENGINE_HOME
        world.mkdir(parents=True, exist_ok=True)
        (world / "state.yaml").write_text(
            "meta:\n  clock:\n    week: 4\n", encoding="utf-8")
        engines.engine_chronicle_dir().mkdir(exist_ok=True)
        (engines.engine_chronicle_dir() / "0004-the-gate.md").write_text(
            "# Week 4 — The Gate\n\nProse.\n", encoding="utf-8")
        return 0, [
            "  [llm] tokens (run meter ~3 calls / ~12000 tokens — estimates,"
            " not the provider's bill): prompt=3100 completion=900",
            "  [llm] tokens (run meter ~9 calls / ~54321 tokens — estimates,"
            " not the provider's bill): prompt=4000 completion=800",
        ]

    monkeypatch.setattr(engines, "run_py", fake_tick)
    monkeypatch.setattr(engines, "ENGINE_HOME", tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME", tmp_spine_root / "chronicle")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    rec = client.get(
        "/api/campaign/testvale/simulation/proposal"
    ).json()["proposal"]["run_record"]
    assert rec["llm_calls"] == 9 and rec["llm_tokens"] == 54321, \
        "the last cumulative meter line is the run's tally"


def test_views_unknown_faction_404_and_stub_paths(client, spine_root, monkeypatch,
                                                  tmp_path):
    # never touch the real engine home from tests (engine-home is scratch, D4)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", tmp_path / "engine-home")  # run_py cwd only
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
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", tmp_path / "engine-home")  # run_py cwd only
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
