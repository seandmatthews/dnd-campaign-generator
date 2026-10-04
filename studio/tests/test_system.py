"""System routes: health, doctor stub, and the error body convention."""

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


def test_health_ok(client, isolated_week_flag):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": "0.1.0",
                        "week_run_in_progress": False}


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
