"""Tests for the hardening fixes: lock coverage, name/slug validation, the
catch-all error handler, llm_client HTTP behavior, and the engine lock.
"""

import io
import json
import logging
import threading
import time
import urllib.error
from pathlib import Path

import pytest
import yaml

from conftest import wait_for_job

import doctor
import engines
import jobs
import llm_client
import spine


# ------------------------------------------------------------- lock coverage


def test_lore_entries_takes_the_campaign_lock(tmp_spine_root, client, monkeypatch):
    """lore-entries stages canon (pending-entries-N.yaml): it must go through
    submit_campaign_job like every other mutating job — a second mutating
    job QUEUES behind it (202 + position, ticket 02) instead of racing the
    pending path, and runs only after the first releases the lock."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "geography.yaml").write_text("nations: []\n", encoding="utf-8")

    release = threading.Event()
    entered = threading.Event()
    order = []

    def fake_generate(campaign_dir, focus, log=None):
        entered.set()
        order.append(1)
        release.wait(timeout=10)
        return {"entries": 0, "findings": []}

    def fake_generate_2(campaign_dir, focus, log=None):
        order.append(2)
        return {"entries": 0, "findings": []}

    monkeypatch.setattr("routers.lore.loregen.generate_entries", fake_generate)
    r = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r.status_code == 202, r.text
    first = r.json()["job_id"]

    assert entered.wait(timeout=5), "lore job never started"
    monkeypatch.setattr("routers.lore.loregen.generate_entries",
                        fake_generate_2)
    r2 = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r2.status_code == 202, "the second lore-entries queues, not 409"
    assert r2.json()["queued"] is True and r2.json()["queue_position"] >= 1
    second = r2.json()["job_id"]
    job2 = client.get(f"/api/jobs/{second}").json()
    assert job2["status"] == "queued", "the second job waits its turn"

    release.set()
    job = wait_for_job(client, first, timeout=15.0)
    assert job["status"] == "done", job
    job2 = wait_for_job(client, second, timeout=15.0)
    assert job2["status"] == "done", job2
    assert order == [1, 2], "the queue drains in submission order"


def test_world_map_takes_the_campaign_lock(tmp_spine_root, client, monkeypatch):
    """world-map writes world/map.svg, which force world-gen also replaces:
    same lock, or the two jobs race the same file. The second submit now
    queues behind it (202 + position) instead of 409ing (ticket 02)."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "source.json").write_text("{}", encoding="utf-8")
    release = threading.Event()
    entered = threading.Event()

    def fake_render(source, out):
        entered.set()
        release.wait(timeout=10)
        return {"mode": "skeleton"}

    monkeypatch.setattr("routers.maps.worldmap.render_world_map", fake_render)
    r = client.post("/api/campaign/testvale/maps/world-map")
    assert r.status_code == 202, r.text
    assert entered.wait(timeout=5)
    r2 = client.post("/api/campaign/testvale/maps/world-map")
    assert r2.status_code == 202, "the second world-map queues, not 409"
    assert r2.json()["queued"] is True
    release.set()
    wait_for_job(client, r.json()["job_id"], timeout=15.0)
    wait_for_job(client, r2.json()["job_id"], timeout=15.0)


# ----------------------------------------------------------- name validation


def test_create_campaign_refuses_windows_reserved_names(tmp_spine_root, client):
    for name in ("con", "nul", "com1"):
        r = client.post("/api/campaigns", json={"name": name})
        assert r.status_code == 422, (name, r.status_code, r.text)
        assert "reserved" in r.json()["error"]


def test_create_campaign_refuses_uppercase(tmp_spine_root, client):
    r = client.post("/api/campaigns", json={"name": "Not ASlug"})
    assert r.status_code == 422


def test_post_city_refuses_slug_empty_burg(tmp_spine_root, client):
    """A punctuation-only name slugifies to '' — the old code wrote artifacts
    straight into maps/cities/."""
    r = client.post("/api/campaign/testvale/maps/cities", json={"burg": "!!!"})
    assert r.status_code == 422
    assert "slug" in r.json()["error"]
    assert not (tmp_spine_root / "testvale" / "maps" / "cities" / "params.yaml").exists()


def test_post_site_refuses_slug_empty_name(tmp_spine_root, client):
    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "camp", "name": "!!!"})
    assert r.status_code == 422
    assert not any(tmp_spine_root.glob("testvale/maps/sites/*.*"))


# -------------------------------------------------------- error contract


def test_unexpected_errors_are_json_not_plain_text(tmp_spine_root, client, monkeypatch):
    """server.py's catch-all: any unhandled exception must produce
    {"error": ...} with a 500, never Starlette's plain-text body."""
    from fastapi.testclient import TestClient
    import server

    def bad_summary(name: str):
        raise OSError("disk on fire")
    monkeypatch.setattr(spine, "summary", bad_summary)
    raw = TestClient(server.app, raise_server_exceptions=False)
    r = raw.get("/api/campaign/testvale")
    assert r.status_code == 500
    body = r.json()
    assert "error" in body, body
    assert "internal error" in body["error"]


# ------------------------------------------------------------- engine lock


def test_views_need_no_engine_lock(tmp_spine_root, client, monkeypatch):
    """views read the CAMPAIGN's own state via views.py --state: no copy-in,
    no engines.ENGINE_LOCK. The old contract (copy into the shared home
    under the lock) made every view 409 while any week ran; the new one
    must serve a view WHILE the shared engine home is locked, and the
    engine must be pointed at this campaign's canon state file."""
    import routers.simulation as sim

    # keep any hypothetical home-touching well away from the real tree
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    seen = {}

    def fake_run_py(engine_root, script, *args, timeout=600, on_line=None,
                    env=None):
        seen["script"] = script
        seen["args"] = args
        return 0, ["week: 4", "you:", "  id: wardens"]

    monkeypatch.setattr(engines, "run_py", fake_run_py)
    monkeypatch.setattr(sim.engines, "run_py", fake_run_py)

    # a week job holds the shared engine home: the view must still serve
    with engines.ENGINE_LOCK:
        r = client.get("/api/campaign/testvale/simulation/views/wardens")
    assert r.status_code == 200, r.text
    assert seen["script"] == "src/views.py"
    assert "--state" in seen["args"], (
        "views must pass --state (the campaign's own state), not rely on "
        "copy-in to the shared engine home")
    state_arg = Path(str(seen["args"][seen["args"].index("--state") + 1]))
    assert state_arg.name == "state.yaml"
    assert "testvale" in str(state_arg)


def test_interactive_writers_fastfail_409_while_a_job_holds_the_lock(
        tmp_spine_root, client):
    """The regression: approve/delete, proposal accept/discard, knowledge
    writes, publication resolve, and snapshot retry took the campaign lock
    BLOCKING — a week job holds it for its whole run (a 30-minute ceiling),
    so the DM's click hung the request until a browser timeout turned the
    would-be success into an error nobody could tell from a failure.
    Interactive writers now fast-fail with a 409 that says retrying is
    expected."""
    lock = spine.campaign_lock("testvale")
    assert lock.acquire(blocking=False), "the test needs the free lock"
    try:
        cases = [
            ("/api/campaign/testvale/review/approve",
             {"file": "pending-entries-1.yaml"}),
            ("/api/campaign/testvale/review/delete",
             {"file": "pending-entries-1.yaml"}),
            ("/api/campaign/testvale/simulation/proposal/p-x/accept", None),
            ("/api/campaign/testvale/simulation/proposal/p-x/discard", None),
            ("/api/campaign/testvale/simulation/session",
             {"source": "session:test", "events": [{"text": "x"}]}),
            ("/api/campaign/testvale/knowledge",
             {"text": "the harbor master lies", "visibility": "dm_only"}),
            ("/api/campaign/testvale/snapshot", None),
            ("/api/campaign/testvale/publication/resolve",
             {"transaction": "t-1", "resolution": "apply"}),
        ]
        for url, body in cases:
            r = client.post(url, json=body)
            assert r.status_code == 409, (url, r.status_code, r.text)
            assert "retry when it finishes" in r.json()["error"], url
    finally:
        lock.release()
    # lock free again: the same accept fails on its merits (no such bundle),
    # never on the lock — the 409 above is contention, not a broken route
    r = client.post("/api/campaign/testvale/simulation/proposal/p-x/accept")
    assert r.status_code == 404, r.text


def test_session_refuses_to_stage_when_canon_moves_during_prep(
        tmp_spine_root, client, monkeypatch):
    """The TOCTOU: post_session recorded base_revisions AFTER the engine ran
    — a proposal accept landing in between pinned a pre-accept after-image
    to post-accept revisions, so the accept-time staleness check passed and
    the publish silently overwrote the accepted week. The base state and
    its revision are now captured together under the lock, and staging
    re-checks that the canon did not move while the engine ran."""
    import routers.simulation as sim

    camp = tmp_spine_root / "testvale"
    state_path = camp / "simulation" / "state.yaml"
    moved = []

    def fake_run_py(engine_root, script, *args, timeout=600, on_line=None,
                    env=None):
        if "--apply" in args and not moved:
            moved.append(1)
            doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
            doc["meta"]["clock"]["season"] = "midsummer (moved on)"
            state_path.write_text(
                yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return 0, ["ok"]

    monkeypatch.setattr(engines, "run_py", fake_run_py)

    r = client.post("/api/campaign/testvale/simulation/session", json={
        "source": "session:test-09",
        "events": [{"text": "the harbor master lied"}]})
    assert r.status_code == 409, r.text
    assert "changed while" in r.json()["error"], r.json()

    # control: canon unmoved, the same request stages
    r2 = client.post("/api/campaign/testvale/simulation/session", json={
        "source": "session:test-10",
        "events": [{"text": "a quiet night"}]})
    assert r2.status_code == 200, r2.text  # the session route is sync 200


def test_doctor_skips_the_simulator_probe_while_a_week_is_in_flight(
        tmp_path, monkeypatch):
    """The probe must never overlap a week run. In-flight is the run flag OR
    the engine lock — the old check read only the flag, so a week in the
    gap between flag write and lock acquire was invisible to it."""
    def fake_run_py(engine_root, script, *args, timeout=600, on_line=None,
                    env=None):
        return 0, ["usage text"]

    monkeypatch.setattr(engines, "run_py", fake_run_py)

    def sim_entry(checks):
        return next(c for c in checks
                    if c["name"] == "engine: agentic-simulator")

    # control: nothing in flight, the probe runs (faked)
    checks = doctor.check_engines()
    assert "skipped" not in sim_entry(checks)["detail"]

    # a week holds the engine lock: skip, even with no flag on disk
    with engines.ENGINE_LOCK:
        checks = doctor.check_engines()
    assert "skipped" in sim_entry(checks)["detail"]

    # the flag is up (crash marker, lock free): skip
    flag = tmp_path / ".week-run-flag"
    flag.write_text("testvale", encoding="utf-8")
    monkeypatch.setattr(engines, "week_flag_path", lambda: flag)
    checks = doctor.check_engines()
    assert "skipped" in sim_entry(checks)["detail"]


# -------------------------------------------------------- bounded transcripts


def test_engine_run_transcript_is_bounded(tmp_path):
    """engines._run kept EVERY output line of a run in RAM — a chatty
    30-minute engine pinned its whole transcript (doubled by the job log).
    The run list is capped at RUN_LINE_CAP; the head is dropped with an
    honest marker, and tails still read correctly."""
    n = engines.RUN_LINE_CAP + 500
    rc, lines = engines.run_py(
        tmp_path, "-c",
        "import sys\n"
        f"for i in range({n}): print(f'line {{i}}')\n",
        timeout=60)
    assert rc == 0
    assert len(lines) == engines.RUN_LINE_CAP + 1, "cap + the dropped marker"
    assert lines[0] == (f"[studio] 500 earlier engine lines dropped "
                        f"(kept the last {engines.RUN_LINE_CAP})")
    assert lines[-1] == f"line {n - 1}", "the tail survives the cap"


def test_job_log_is_bounded_during_the_run():
    """The job's DURING-the-run log was an append-everything list trimmed
    only when the job finished; a 30-minute week pinned every engine line
    in RAM while the API only ever served the 40-line tail. The ring keeps
    the same tail live throughout, with bounded memory behind it."""
    release = threading.Event()

    def noisy(log):
        for i in range(500):
            log(f"line {i}")
        release.wait(timeout=5)
        return {"ok": True}

    jid = jobs.submit("test-noisy", noisy)
    deadline = time.time() + 5
    rec = None
    while time.time() < deadline:
        rec = jobs.get(jid)
        if rec["log_tail"] and rec["log_tail"][-1] == "line 499":
            break
        time.sleep(0.02)
    assert rec is not None and rec["log_tail"] and         rec["log_tail"][-1] == "line 499",         f"the producer never reached line 499 within 5s (descheduling on a loaded box, most likely): last={rec and rec['log_tail'] and rec['log_tail'][-1]!r}"
    assert rec["status"] == "running", rec
    assert len(rec["log_tail"]) == jobs.LOG_CAP, "bounded mid-run, not after"
    assert rec["log_tail"][0] == f"line {500 - jobs.LOG_CAP}"
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline and jobs.get(jid)["status"] != "done":
        time.sleep(0.02)
    rec = jobs.get(jid)
    assert rec["status"] == "done" and rec["result"] == {"ok": True}
    assert len(rec["log_tail"]) == jobs.LOG_CAP
    assert rec["log_tail"][-1] == "line 499"


# ------------------------------------------------------------- llm_client


def _http_error(code, body=b""):
    return urllib.error.HTTPError("url", code, "x", {}, io.BytesIO(body))


_ZAI_URL = llm_client.API_BASE_DEFAULT + "/chat/completions"


def test_llm_client_retries_transient_and_raises_readable_hard_errors(monkeypatch):
    calls = []
    def fake_urlopen(req, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(429, b'{"error":"rate"}')
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                 "usage": {}}).encode()
        return R()
    monkeypatch.setattr(llm_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm_client, "resolve_config",
                        lambda env_file=None: {"key": "k", "url": _ZAI_URL,
                                               "model": "m"})
    out = llm_client.complete("p")
    assert out == "ok"
    assert len(calls) == 3


def test_llm_client_401_carries_the_doctor_hint(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise _http_error(401, b"unauthorized")
    monkeypatch.setattr(llm_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm_client, "resolve_config",
                        lambda env_file=None: {"key": "k", "url": _ZAI_URL,
                                               "model": "m"})
    with pytest.raises(RuntimeError) as e:
        llm_client.complete("p")
    assert "401" in str(e.value)
    assert "doctor" in str(e.value)


def test_llm_client_silences_config_lookups_per_call(monkeypatch):
    count = {"n": 0}
    def fake_resolve(env_file=None):
        count["n"] += 1
        return {"key": "k", "url": _ZAI_URL, "model": "m"}
    def fake_urlopen(req, timeout=None):
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 2}}).encode()
        return R()
    monkeypatch.setattr(llm_client, "resolve_config", fake_resolve)
    monkeypatch.setattr(llm_client.urllib.request, "urlopen", fake_urlopen)
    logging.disable(logging.CRITICAL)
    try:
        llm_client.complete("p", max_tokens=8, max_budget=16)  # one budget retry
    finally:
        logging.disable(logging.NOTSET)
    assert count["n"] == 1, "config must be resolved once per call, not per retry"


# ------------------------------------------------------------------ spine


def test_write_text_atomic_leaves_no_tmp_and_reads_back(tmp_spine_root):
    p = tmp_spine_root / "testvale" / "lore" / "entities.yaml"
    spine.write_text_atomic(p, "kinds: [person]\n")
    assert p.read_text(encoding="utf-8") == "kinds: [person]\n"
    assert not p.with_name(p.name + ".tmp").exists()


# ------------------------------------------- unreadable campaign files 4xx

def test_corrupt_timeline_is_readable_422_not_500(client, tmp_spine_root):
    """A hand-edit that breaks lore/timeline.yaml must surface as a 422
    naming the file — it used to escape _safe_load as a bare YAMLError and
    500 with 'internal error: MarkedYAMLError'."""
    (tmp_spine_root / "testvale" / "lore" / "timeline.yaml").write_text(
        "entries: [ oops: unclosed\n", encoding="utf-8")
    r = client.get("/api/campaign/testvale/lore")
    assert r.status_code == 422, r.text
    assert "timeline.yaml" in r.json()["error"], r.text


def test_corrupt_campaign_yaml_degrades_in_listing(client, tmp_spine_root):
    """One unparsable campaign.yaml must not take the whole campaign list
    down — the listing degrades to folder-name identity."""
    (tmp_spine_root / "testvale" / "campaign.yaml").write_text(
        "name: [unclosed\n", encoding="utf-8")
    r = client.get("/api/campaigns")
    assert r.status_code == 200, r.text
    names = [c["name"] for c in r.json()["campaigns"]]
    assert "testvale" in names
