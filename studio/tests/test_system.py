"""System routes: health (with the real build version), the doctor probe
lock, and the error body convention."""

import re
import threading
import time
from pathlib import Path

import pytest

from conftest import wait_for_job


@pytest.fixture()
def isolated_week_flag(monkeypatch, tmp_path):
    """Point the week-run flag at an empty tmp home. The health endpoint
    reads the REAL flag path by design (it is a runtime signal), so without
    this a live week anywhere on the same checkout fails the fast tier
    (found 2026-09-23: 'works in CI, fails on my machine')."""
    import engines
    fake = tmp_path / "engine-home"
    fake.mkdir()
    monkeypatch.setattr(engines, "ENGINE_HOME", fake)
    return fake


def test_health_ok(client, isolated_week_flag, monkeypatch, tmp_path):
    """Shape + the version fallback: a checkout with no VERSION stamp
    answers pyproject's version — never a hardcoded guess (ticket 69)."""
    import routers.system as system
    monkeypatch.setattr(system, "_VERSION_PATH", tmp_path / "no-stamp-here")
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["week_run_in_progress"] is False
    pyproject = (Path(system.STUDIO_DIR).parent / "pyproject.toml").read_text(
        encoding="utf-8")
    expected = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
    assert body["version"] == expected


def test_health_carries_the_install_version(client, isolated_week_flag,
                                            monkeypatch, tmp_path):
    """An installed copy answers its full VERSION stamp
    (release/build_release.py writes it beside studio/; install.cmd mirrors
    it on every update) — a friend reporting a bug can quote what
    /api/health says and name the build (ticket 69)."""
    import routers.system as system
    stamp = tmp_path / "VERSION"
    stamp.write_text("0.1.0+20261023120000+v0.2.0+atlas-0.7.1\n",
                     encoding="ascii")
    monkeypatch.setattr(system, "_VERSION_PATH", stamp)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["version"] == "0.1.0+20261023120000+v0.2.0+atlas-0.7.1"


def test_install_version_stages_win(monkeypatch, tmp_path):
    """The stamp wins over pyproject when both resolve; a blank stamp is
    no stamp; with neither file the honest answer is "unknown"."""
    import routers.system as system
    home = tmp_path / "bundle-root"
    home.mkdir()
    stamp = home / "VERSION"
    monkeypatch.setattr(system, "_VERSION_PATH", stamp)
    monkeypatch.setattr(system, "STUDIO_DIR", home / "studio")
    assert system.install_version() == "unknown"  # no stamp, no pyproject
    stamp.write_text("   \n", encoding="ascii")
    assert system.install_version() == "unknown"  # blank is no stamp
    stamp.write_text("0.1.0+stamped-build\n", encoding="ascii")
    assert system.install_version() == "0.1.0+stamped-build"


def test_health_reads_the_real_flag(client, isolated_week_flag):
    """The flag is a live signal: present means a week is in flight (or was
    interrupted)."""
    (isolated_week_flag / ".week-run-flag").write_text("x",
                                                       encoding="utf-8")
    r = client.get("/api/health")
    assert r.json()["week_run_in_progress"] is True


def test_doctor_endpoint_wired(client, monkeypatch):
    """M5 wired /api/doctor to studio/doctor.py — the report is faked here
    (no live probes); doctor.py's own logic is covered in test_doctor.py."""
    import doctor
    monkeypatch.setattr(doctor, "report", lambda: {
        "ok": True,
        "checks": [{"name": "python", "ok": True, "detail": "Python 3.10",
                    "fix": None, "required": True}]})
    r = client.get("/api/doctor")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["checks"][0]["name"] == "python"


def test_concurrent_doctor_calls_collapse_to_one_probe(client, monkeypatch):
    """The full probe is minutes of subprocesses on request threads (engine
    CLIs + one live LLM probe). Two concurrent calls must run it ONCE: the
    second waits on the probe lock, re-checks the cache, and answers from
    the winner's fresh report — it must not start a second probe while the
    first is in flight (ticket 69; the threadpool-drain hazard review.py's
    check.py budget fixed)."""
    import doctor
    import routers.system as system
    calls = []
    probing = threading.Event()
    release = threading.Event()

    def slow_probe(*a, **kw):
        calls.append(1)
        probing.set()
        assert release.wait(timeout=5), "the test never released the probe"
        return {"ok": True, "checks": []}

    monkeypatch.setattr(doctor, "report", slow_probe)
    answers = []

    def ask():
        answers.append(system.get_doctor())

    first = threading.Thread(target=ask)
    first.start()
    assert probing.wait(timeout=5), "the first caller never started probing"
    second = threading.Thread(target=ask)
    second.start()
    time.sleep(0.2)  # the second caller is now past the cache, on the lock
    assert len(calls) == 1, "a waiting caller must not start a second probe"
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert len(calls) == 1, "the probe must have run exactly once"
    assert answers[0] == {"ok": True, "checks": []}
    assert answers[1] == {"ok": True, "checks": []}


def test_doctor_cache_still_serves_within_the_window(client, monkeypatch):
    """The lock must not break the cache: a second call inside 60s answers
    from the cache without probing again (the pre-lock behavior)."""
    import doctor
    import routers.system as system
    calls = []
    monkeypatch.setattr(doctor, "report",
                        lambda: calls.append(1) or {"ok": True, "checks": []})
    system.get_doctor()
    system.get_doctor()
    assert len(calls) == 1


def test_unknown_job_404(client):
    r = client.get("/api/jobs/nope")
    assert r.status_code == 404
    assert "error" in r.json()


def test_jobs_lifecycle_with_fake_engine(client):
    import jobs

    def fake_engine(log):
        log("engine starting")
        log("step 1 ok")
        return {"answer": 42}

    job_id = jobs.submit("fake", fake_engine)
    job = wait_for_job(client, job_id)
    assert job["status"] == "done"
    assert job["kind"] == "fake"
    assert job["result"] == {"answer": 42}
    assert job["error"] is None
    assert job["log_tail"][-1] == "step 1 ok"
    assert job["started"]
