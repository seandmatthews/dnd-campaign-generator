"""Tests for the hardening fixes: lock coverage, name/slug validation, the
catch-all error handler, llm_client HTTP behavior, and the engine lock.
"""

import io
import json
import logging
import threading
import urllib.error
from pathlib import Path

import pytest

from conftest import wait_for_job

import engines
import llm_client
import spine


# ------------------------------------------------------------- lock coverage


def test_lore_entries_takes_the_campaign_lock(tmp_spine_root, client, monkeypatch):
    """lore-entries stages canon (pending-entries-N.yaml): it must go through
    submit_campaign_job like every other mutating job — a second mutating job
    while it runs answers 409 instead of racing the pending path."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "geography.yaml").write_text("nations: []\n", encoding="utf-8")

    release = threading.Event()
    entered = threading.Event()

    def fake_generate(campaign_dir, focus, log=None):
        entered.set()
        release.wait(timeout=5)
        return {"entries": 0, "findings": []}

    monkeypatch.setattr("routers.lore.loregen.generate_entries", fake_generate)
    r = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r.status_code == 202, r.text
    first = r.json()["job_id"]

    assert entered.wait(timeout=5), "lore job never started"
    r2 = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r2.status_code == 409, "second lore-entries must 409 while the first runs"

    release.set()
    job = wait_for_job(client, first)
    assert job["status"] == "done", job


def test_world_map_takes_the_campaign_lock(tmp_spine_root, client, monkeypatch):
    """world-map writes world/map.svg, which force world-gen also replaces:
    same lock, or the two jobs race the same file."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "source.json").write_text("{}", encoding="utf-8")
    release = threading.Event()
    entered = threading.Event()

    def fake_render(source, out):
        entered.set()
        release.wait(timeout=5)
        return {"mode": "skeleton"}

    monkeypatch.setattr("routers.maps.worldmap.render_world_map", fake_render)
    r = client.post("/api/campaign/testvale/maps/world-map")
    assert r.status_code == 202, r.text
    assert entered.wait(timeout=5)
    r2 = client.post("/api/campaign/testvale/maps/world-map")
    assert r2.status_code == 409, "second world-map must 409 while the first runs"
    release.set()
    wait_for_job(client, r.json()["job_id"])


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
