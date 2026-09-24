"""Hardening wave: per-campaign mutation locks (409 on busy), the
interrupted-week flag + restore-on-boot, git-backed canon snapshots, the
Host allowlist middleware, and the rotating studio log.

Everything runs against temp dirs / temp git repos — no real engine, and
the real Documents/D&D repo is never committed to (canon skips campaigns
that live outside the repo root)."""

import json
import logging
import os
import subprocess
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import canon
import engines
import jobs
import pytest
import server
import spine
from conftest import wait_for_job
from routers.simulation import (clear_week_flag, restore_interrupted_week,
                                write_week_flag)

BUSY = {"error": "another job is already modifying this campaign"}


@pytest.fixture(autouse=True)
def _no_leaked_campaign_locks():
    """Locks are process-global: a test that leaves one held would deadlock
    the next test's blocking acquire. threading.Lock has no owner thread,
    so release() clears it from here even when the test thread held it."""
    yield
    for lock in list(spine._CAMPAIGN_LOCKS.values()):
        try:
            lock.release()
        except RuntimeError:
            pass  # was not held


def fake_tick_ok(engine_root, script, *args, timeout=600, on_line=None, **kw):
    """The smallest engine that advances the clock to week 4."""
    world = engine_root / "world"
    world.mkdir(parents=True, exist_ok=True)
    world.joinpath("state.yaml").write_text(
        "meta:\n  clock:\n    week: 4\n", encoding="utf-8")
    chron = engine_root / "chronicle"
    chron.mkdir(exist_ok=True)
    (chron / "0004-the-gate.md").write_text("# Week 4 — The Gate\n",
                                            encoding="utf-8")
    return 0, ["wardens HOLDS (quiet week)", "week 4 written"]


# ------------------------------------------------------- per-campaign locks


def test_week_409_while_campaign_lock_held(client, tmp_spine_root, monkeypatch):
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "run_py", fake_tick_ok)
    lock = spine.campaign_lock("testvale")
    assert lock.acquire(blocking=False)
    try:
        r = client.post("/api/campaign/testvale/simulation/week",
                        json={"confirm": True})
        assert r.status_code == 409
        assert r.json() == BUSY  # nothing queued while a mutation is in flight
    finally:
        lock.release()

    # once free, the submit goes through and the job releases the lock
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert spine.campaign_lock("testvale").acquire(blocking=False)


def test_world_gen_409_while_campaign_lock_held(client, tmp_spine_root):
    lock = spine.campaign_lock("testvale")
    lock.acquire()
    try:
        # force:true so the only 409 left is the busy one
        r = client.post("/api/campaign/testvale/world",
                        json={"seed": "42", "force": True})
        assert r.status_code == 409
        assert r.json() == BUSY
    finally:
        lock.release()
    assert spine.campaign_lock("testvale").acquire(blocking=False)


def test_approve_waits_for_running_job_lock(client, tmp_spine_root):
    pending = tmp_spine_root / "testvale" / "simulation" / "pending-week-7.yaml"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({"entries": [
        {"id": "pw7-1", "when": "week 7", "title": "T", "text": "x",
         "source": "sim:week-7"}], "created": "2026-09-22T00:00:00+00:00",
        "week": 7}), encoding="utf-8")

    lock = spine.campaign_lock("testvale")
    lock.acquire()
    threading.Timer(0.2, lock.release).start()  # "the job finishes"

    started = time.monotonic()
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "simulation/pending-week-7.yaml",
                          "all": True})
    # Phase 1 contract: approve also returns `transaction` + `snapshot`
    body = r.json()
    body.pop("transaction", None)
    body.pop("snapshot", None)
    assert r.status_code == 200 and body == {
        "approved": 1, "entries": 1, "pages": 0, "pages_skipped": 0,
        "stubs": 0}
    assert time.monotonic() - started >= 0.15  # it genuinely waited


def test_lock_registry_returns_one_lock_per_campaign():
    assert spine.campaign_lock("a") is spine.campaign_lock("a")
    assert spine.campaign_lock("a") is not spine.campaign_lock("b")


# ------------------------------------------------- host allowlist middleware


def test_host_allowlist_accepts_loopback_and_testclient(client):
    assert client.get("/api/health").status_code == 200  # TestClient default
    for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765",
                 "127.0.0.1", "localhost", "[::1]", "127.0.0.1:8765"):
        r = client.get("/api/health", headers={"host": host})
        assert r.status_code == 200, host


def test_host_allowlist_rejects_rebind_and_wrong_port(client):
    for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil:8765",
                 "localhost:9999", ""):
        r = client.get("/api/health", headers={"host": host})
        assert r.status_code == 403, host
        assert r.json() == {"error": "unknown host header"}


def test_host_allowlist_port_follows_bound_port():
    assert server._host_allowed("127.0.0.1:8791", port=8791)
    assert server._host_allowed("localhost:8791", port=8791)
    assert server._host_allowed("[::1]:8791", port=8791)
    assert not server._host_allowed("127.0.0.1:8765", port=8791)
    assert not server._host_allowed("evil.example", port=8791)


def test_host_allowlist_studio_extra_hosts_env(client, monkeypatch):
    monkeypatch.setenv("STUDIO_EXTRA_HOSTS", "dm.example, partner.local")
    assert client.get("/api/health",
                      headers={"host": "dm.example"}).status_code == 200
    assert client.get("/api/health",
                      headers={"host": "partner.local"}).status_code == 200
    assert client.get("/api/health",
                      headers={"host": "still-evil.example"}).status_code == 403


# -------------------------------------------- interrupted-week flag/restore


def _fake_engine_home(tmp_path):
    home = tmp_path / "engine-home"
    world = home / "world"
    world.mkdir(parents=True, exist_ok=True)
    return home, world


def test_week_job_writes_flag_and_clears_it_in_finally(client, tmp_spine_root,
                                                       monkeypatch):
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    seen = {}

    def fake_tick(engine_root, script, *args, timeout=600, on_line=None, **kw):
        flag = engines.week_flag_path()
        seen["during"] = flag.is_file()
        if seen["during"]:
            seen["doc"] = json.loads(flag.read_text(encoding="utf-8"))
        return fake_tick_ok(engine_root, script, *args, timeout=timeout,
                            on_line=on_line)

    monkeypatch.setattr(engines, "run_py", fake_tick)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert seen["during"] is True
    assert seen["doc"]["campaign"] == "testvale"
    assert seen["doc"]["started"]
    assert not engines.week_flag_path().exists()  # cleared after copy-out


def test_week_job_failure_clears_flag(client, tmp_spine_root, monkeypatch):
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")

    def failing_tick(*args, **kw):
        return 1, ["boom"]

    monkeypatch.setattr(engines, "run_py", failing_tick)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert not engines.week_flag_path().exists()  # finally ran anyway


def test_restore_restores_engine_state_from_bak(tmp_path, monkeypatch):
    home, world = _fake_engine_home(tmp_path)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", home)
    (world / "state.yaml").write_text("half a tick", encoding="utf-8")
    (world / "state.yaml.bak").write_text("pre-week engine state",
                                          encoding="utf-8")
    write_week_flag("testvale")
    doc = json.loads(engines.week_flag_path().read_text(encoding="utf-8"))
    assert doc["campaign"] == "testvale" and doc["started"]

    info = restore_interrupted_week()
    assert info == {"campaign": "testvale", "restored": True}
    assert (world / "state.yaml").read_text(encoding="utf-8") == \
        "pre-week engine state"
    assert not engines.week_flag_path().exists()


def test_restore_without_bak_still_clears_flag(tmp_path, monkeypatch):
    home, world = _fake_engine_home(tmp_path)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", home)
    (world / "state.yaml").write_text("half a tick", encoding="utf-8")
    write_week_flag("othervale")

    info = restore_interrupted_week()
    assert info == {"campaign": "othervale", "restored": False}
    assert not engines.week_flag_path().exists()
    # nothing destroyed when there is no backup to restore from
    assert (world / "state.yaml").read_text(encoding="utf-8") == "half a tick"


def test_restore_noop_without_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        _fake_engine_home(tmp_path)[0])
    assert restore_interrupted_week() is None


def test_boot_lifespan_restores_and_reports_health(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    home, world = _fake_engine_home(tmp_path)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", home)
    (world / "state.yaml").write_text("mid-tick garbage", encoding="utf-8")
    (world / "state.yaml.bak").write_text("last good engine state",
                                          encoding="utf-8")
    write_week_flag("testvale")

    with TestClient(server.app) as c:  # entering the client runs the lifespan
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["week_run_in_progress"] is False  # restored + cleared
    assert (world / "state.yaml").read_text(encoding="utf-8") == \
        "last good engine state"


def test_health_reflects_flag_outside_boot(client, tmp_spine_root, monkeypatch):
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    write_week_flag("testvale")
    try:
        assert client.get("/api/health").json()["week_run_in_progress"] is True
    finally:
        clear_week_flag()
    assert client.get("/api/health").json()["week_run_in_progress"] is False


# --------------------------------------------------- canon git snapshots


def _git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, timeout=60)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), \
        proc.stderr.decode("utf-8", "replace")


def _campaigns_root(tmp_path):
    """A temp campaigns root with a seeded campaign and NO git repo yet —
    canon must init it on demand (the workspace is its own nested repo)."""
    root = tmp_path / "campaigns"
    (root / "testvale" / "lore").mkdir(parents=True)
    (root / "testvale" / "lore" / "timeline.yaml").write_text(
        "entries: []\n", encoding="utf-8")
    return root


def test_canon_snapshots_into_a_campaigns_repo_it_creates(tmp_path, monkeypatch):
    root = _campaigns_root(tmp_path)
    monkeypatch.setattr(canon, "AVAILABLE", True)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    assert not (root / ".git").exists(), "test starts without the nested repo"

    timeline = root / "testvale" / "lore" / "timeline.yaml"
    timeline.write_text("entries:\n  - id: t0001\n", encoding="utf-8")
    sha = canon.snapshot("testvale", "campaign/testvale: approve 1 pending entries")
    assert sha
    rc, out, _ = _git(root, "log", "-1", "--format=%s")
    assert out.strip() == "campaign/testvale: approve 1 pending entries"
    rc, out, _ = _git(root, "log", "-1", "--format=%h")
    assert out.strip() == sha

    # only the campaign path is committed: a stray sibling is not swept in
    (root / "stray.txt").write_text("not canon", encoding="utf-8")
    timeline.write_text("entries:\n  - id: t0002\n", encoding="utf-8")
    canon.snapshot("testvale", "campaign/testvale: week 7 — the gate")
    rc, out, _ = _git(root, "status", "--porcelain")
    assert "?? stray.txt" in out  # still untracked -> was never committed

    # nothing to commit: a logged no-op, never an error
    assert canon.snapshot("testvale", "campaign/testvale: no-op") is None

    # an identity exists (set locally by canon if git had none configured)
    rc, out, _ = _git(root, "config", "--get", "user.email")
    assert out.strip()


def test_canon_message_truncated_to_72(tmp_path, monkeypatch):
    root = _campaigns_root(tmp_path)
    monkeypatch.setattr(canon, "AVAILABLE", True)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    (root / "testvale" / "lore" / "timeline.yaml").write_text(
        "entries:\n  - id: t0009\n", encoding="utf-8")
    canon.snapshot("testvale", "campaign/testvale: " + "x" * 200)
    rc, out, _ = _git(root, "log", "-1", "--format=%s")
    assert len(out.strip()) <= 72


def test_canon_noop_when_git_unavailable(monkeypatch):
    monkeypatch.setattr(canon, "AVAILABLE", False)
    assert canon.snapshot("testvale", "campaign/testvale: whatever") is None


def test_canon_works_wherever_the_campaigns_root_lives(tmp_spine_root, monkeypatch):
    """A relocated campaigns root (tests, portable set-ups) snapshots into
    ITS OWN nested repo — never into this (the real) parent repo."""
    monkeypatch.setattr(canon, "AVAILABLE", True)
    before = subprocess.run(
        ["git", "-C", str(spine.STUDIO_DIR.parent), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout
    sha = canon.snapshot("testvale", "campaign/testvale: relocated root")
    assert sha, "snapshot must work from a temp campaigns root"
    after = subprocess.run(
        ["git", "-C", str(spine.STUDIO_DIR.parent), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout
    assert before == after, "the parent repo must not move"


# --------------------------------------------------------- rotating log


def test_log_file_created_with_rotating_handler():
    handlers = [h for h in logging.getLogger("studio").handlers
                if isinstance(h, RotatingFileHandler)]
    assert handlers, "the studio logger must carry the rotating file handler"
    assert handlers[0].maxBytes == 1_000_000
    assert handlers[0].backupCount == 5
    # conftest sets STUDIO_LOG_FILE before importing server: the handler must
    # target that test-owned file, never the user's active studio.log
    assert Path(handlers[0].baseFilename).resolve() == \
        Path(os.environ["STUDIO_LOG_FILE"]).resolve()
    assert Path(handlers[0].baseFilename).is_file()


def test_log_path_env_override(tmp_path, monkeypatch):
    """STUDIO_LOG_FILE decides where the rotating log lives (absolute or
    studio-relative); the default stays the user's studio/logs/studio.log."""
    custom = tmp_path / "logs" / "custom.log"
    monkeypatch.setenv("STUDIO_LOG_FILE", str(custom))
    assert server._log_file_path() == custom
    monkeypatch.setenv("STUDIO_LOG_FILE", "logs/relative.log")
    assert server._log_file_path() == server.STUDIO_DIR / "logs" / "relative.log"
    monkeypatch.delenv("STUDIO_LOG_FILE")
    assert server._log_file_path() == server.STUDIO_DIR / "logs" / "studio.log"


def test_job_log_lines_mirror_into_the_log(client):
    """Job lines must reach the studio logger (which carries the rotating
    file handler). Asserting on our own handler instead of the shared
    studio.log file: a concurrently running studio server holds that file
    open, and its rotation then fails under the test (found 2026-09-23)."""
    marker = f"hardening-mirror-{time.monotonic_ns()}"
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = Capture()
    logging.getLogger("studio.jobs").addHandler(handler)
    try:
        def announce(log):
            log(f"mirror check {marker}")

        job_id = jobs.submit("fake", announce)
        assert wait_for_job(client, job_id)["status"] == "done"
    finally:
        logging.getLogger("studio.jobs").removeHandler(handler)
    assert any(marker in m for m in captured), captured[-5:]


def test_hostile_site_and_style_names_are_422_not_500(client, tmp_spine_root):
    """Reserved Windows device names and trailing dot/space cannot exist as
    directories — the studio knows this (campaign names refuse them) and now
    refuses them for site dirs and style files too, with the same readable
    422 instead of a raw OSError 500 from deep inside the pipeline."""
    for name in ("con", "nul", "trailing dot."):  # a trailing space cannot survive .strip()
        r = client.post("/api/campaign/testvale/maps/sites",
                        json={"kind": "dungeon", "name": name})
        assert r.status_code == 422, (name, r.text)
        assert "error" in r.json()
    r = client.post("/api/campaign/testvale/lore/style/con.txt",
                    json={"text": "a\nb\nc\n"})
    assert r.status_code == 422, r.text
