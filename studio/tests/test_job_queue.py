"""Ticket 02: the per-campaign FIFO job queue and the wait estimates.

The queue: a submission while another mutation runs answers 202 with a
queue position (never 409), the jobs poll carries status "queued" +
position, queued jobs are cancellable, and the drain is strictly in
submission order.

The estimates: finished job durations roll into a small git-ignored
history next to the transactions dir; a busy 409 that CANNOT queue (a
short interactive writer) carries "Blocked by: <job> — typically <band>".
"""

import threading
import time

import job_history
import jobs
import spine
from conftest import wait_for_job


# ----------------------------------------------------------------- the queue

def test_second_submit_returns_202_with_position_not_409(
        tmp_spine_root, client, monkeypatch):
    """The headline contract: while a job runs, a second mutating submit
    queues (202 + queue position) instead of fast-failing 409."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "geography.yaml").write_text("nations: []\n",
                                                   encoding="utf-8")
    release = threading.Event()
    entered = threading.Event()

    def fake_generate(campaign_dir, focus, log=None):
        entered.set()
        release.wait(timeout=10)
        return {"entries": 0, "findings": []}

    monkeypatch.setattr("routers.lore.loregen.generate_entries",
                        fake_generate)
    r1 = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r1.status_code == 202, r1.text
    assert entered.wait(timeout=5), "the first job never started"

    r2 = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r2.status_code == 202, r2.text
    body = r2.json()
    assert body["job_id"] and body["queued"] is True
    assert body["queue_position"] == 1, "it runs right after the current job"

    # the poll shows queued + position while it waits
    polled = client.get(f"/api/jobs/{body['job_id']}").json()
    assert polled["status"] == "queued"
    assert polled["position"] == 1

    release.set()
    assert wait_for_job(client, r1.json()["job_id"],
                        timeout=15.0)["status"] == "done"
    assert wait_for_job(client, body["job_id"],
                        timeout=15.0)["status"] == "done"


def test_queue_drains_in_submission_order(tmp_spine_root, client,
                                          monkeypatch):
    """Three mutations submitted while one runs execute strictly FIFO.
    Each submission tags itself through the focus field (the queued jobs
    look the handler up at RUN time, so the fake must be stable)."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "geography.yaml").write_text("nations: []\n",
                                                   encoding="utf-8")
    order = []
    release_first = threading.Event()
    first_running = threading.Event()

    def fake(campaign_dir, focus, log=None):
        n = focus.strip()
        order.append(n)
        if n == "1":
            first_running.set()
            release_first.wait(timeout=10)
        return {"entries": 0, "findings": []}

    monkeypatch.setattr("routers.lore.loregen.generate_entries", fake)
    r1 = client.post("/api/campaign/testvale/lore/entries",
                     json={"focus": "1"})
    assert r1.status_code == 202
    assert first_running.wait(timeout=5)

    ids = [r1.json()["job_id"]]
    for n in (2, 3, 4):
        r = client.post("/api/campaign/testvale/lore/entries",
                        json={"focus": str(n)})
        assert r.status_code == 202, r.text
        assert r.json()["queue_position"] == n - 1, \
            "positions count the jobs ahead of this one"
        ids.append(r.json()["job_id"])

    release_first.set()
    for rid in ids:
        assert wait_for_job(client, rid, timeout=15.0)["status"] == "done"
    assert order == ["1", "2", "3", "4"], "FIFO, no overtaking"
    snap = client.get("/api/campaign/testvale/jobs").json()
    assert snap["campaign"] == "testvale"
    assert snap["running"] is None and snap["queued"] == []
    assert snap["running_estimate"] is None
    # the four jobs this test just finished are the newest of their kind
    assert snap["last_finished"]["lore-entries"]["status"] == "done"


def test_queued_job_is_cancellable(tmp_spine_root, client, monkeypatch):
    """Cancel = dequeue: the job flips to cancelled and never runs."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "geography.yaml").write_text("nations: []\n",
                                                   encoding="utf-8")
    release = threading.Event()
    ran = []

    def blocker(campaign_dir, focus, log=None):
        release.wait(timeout=10)
        ran.append("first")
        return {"entries": 0, "findings": []}

    monkeypatch.setattr("routers.lore.loregen.generate_entries", blocker)
    r1 = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r1.status_code == 202
    deadline = time.time() + 5.0
    while time.time() < deadline:  # the first job holds the campaign lock
        if jobs.running_jobs(campaign="testvale"):
            break
        time.sleep(0.02)

    r2 = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r2.status_code == 202
    queued_id = r2.json()["job_id"]

    r = client.post(f"/api/jobs/{queued_id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    # cancelling again: nothing left to dequeue
    r = client.post(f"/api/jobs/{queued_id}/cancel")
    assert r.status_code == 409 and "not queued" in r.json()["error"]
    # a made-up id is a 404
    r = client.post("/api/jobs/deadbeef0000/cancel")
    assert r.status_code == 404

    release.set()
    assert wait_for_job(client, r1.json()["job_id"],
                        timeout=15.0)["status"] == "done"
    time.sleep(0.2)
    assert ran == ["first"], "the cancelled job never ran"
    assert client.get(f"/api/jobs/{queued_id}").json()["status"] == \
        "cancelled"


# -------------------------------------------------------------- ticket 41

def test_last_finished_carries_the_newest_job_per_kind(
        tmp_spine_root, client):
    """last_finished answers "how did the last one go": the newest finished
    job of each kind, failed or not. The Maps tab renders a failed map job
    as a persistent section error from it; a later finished run of the
    kind replaces it. Other campaigns' jobs never leak in."""
    def boom(log):
        raise RuntimeError(
            'burg "Stormberg": ambiguous, 2 burgs share the name (ids 2, 266)')

    jobs.submit("map-city", boom, campaign="testvale")
    assert jobs.wait_all(timeout=10.0)
    jobs.submit("map-site", lambda log: {"slug": "crypt"},
                campaign="testvale")
    assert jobs.wait_all(timeout=10.0)
    jobs.submit("map-city", lambda log: {"slug": "stormberg"},
                campaign="testvale")
    assert jobs.wait_all(timeout=10.0)
    jobs.submit("map-city", lambda log: None, campaign="othervale")
    assert jobs.wait_all(timeout=10.0)

    lf = client.get("/api/campaign/testvale/jobs").json()["last_finished"]
    # kinds other tests finished (lore-entries) may sit here too — the two
    # this test created must both be present and newest-of-their-kind
    assert {"map-city", "map-site"} <= set(lf)
    # the later run of the kind replaced the earlier one
    assert lf["map-city"]["status"] == "done"
    assert lf["map-city"]["result"] == {"slug": "stormberg"}
    assert lf["map-site"]["status"] == "done"

    # the error leg: a failing run is exactly what the section error reads
    jobs.submit("map-city", boom, campaign="testvale")
    assert jobs.wait_all(timeout=10.0)
    lf = client.get("/api/campaign/testvale/jobs").json()["last_finished"]
    assert lf["map-city"]["status"] == "error"
    assert "ambiguous" in lf["map-city"]["error"]
    # other campaigns stay out of testvale's answer
    other = tmp_spine_root / "othervale"
    other.mkdir()
    (other / "campaign.yaml").write_text("name: othervale\n", encoding="utf-8")
    assert client.get("/api/campaign/othervale/jobs").json()[
        "last_finished"]["map-city"]["status"] == "done"


def test_cancel_running_job_is_refused(tmp_spine_root, client, monkeypatch):
    """A job that already started cannot be cancelled — the campaign's
    worker is executing it."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "geography.yaml").write_text("nations: []\n",
                                                   encoding="utf-8")
    release = threading.Event()

    def blocker(campaign_dir, focus, log=None):
        release.wait(timeout=10)
        return {"entries": 0, "findings": []}

    monkeypatch.setattr("routers.lore.loregen.generate_entries", blocker)
    r1 = client.post("/api/campaign/testvale/lore/entries", json={})
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if jobs.running_jobs(campaign="testvale"):
            break
        time.sleep(0.02)
    r = client.post(f"/api/jobs/{r1.json()['job_id']}/cancel")
    assert r.status_code == 409 and "not queued" in r.json()["error"]
    release.set()
    wait_for_job(client, r1.json()["job_id"], timeout=15.0)


# ------------------------------------------------------------- the estimates

def test_eta_store_median_band_and_roster_scaling(tmp_spine_root, monkeypatch):
    """The rolling history: median of the last few runs, roster-scaled for
    weeks/forecasts, an honest band, and None before anything finished."""
    job_history.record("testvale", "sim-week", 600.0)
    job_history.record("testvale", "sim-week", 900.0)
    job_history.record("testvale", "sim-week", 1200.0)
    est = job_history.estimate("testvale", "sim-week")
    assert est is not None and est["samples"] == 3
    assert est["median_s"] == 900.0
    assert est["roster"] is not None  # the fixture sandbox has factions
    # roster scaling: the fixture roster is small; halving it may only move
    # the estimate by the clamped factor — assert the honest shape, not a
    # magic number
    assert est["low_s"] < est["median_s"] < est["high_s"]
    text = job_history.band_text(est)
    assert text.startswith("typically "), text

    # non-scaled kinds use the raw median
    job_history.record("testvale", "map-site", 30.0)
    job_history.record("testvale", "map-site", 90.0)
    est2 = job_history.estimate("testvale", "map-site")
    assert est2["median_s"] == 60.0 and est2["roster"] is None

    # nothing recorded -> no estimate (never a made-up number)
    assert job_history.estimate("testvale", "world-gen") is None
    assert "no duration history yet" in job_history.blocked_line(
        "world-gen", None)

    # the window rolls: only the newest entries survive
    for s in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0):
        job_history.record("testvale", "forecast", s)
    est3 = job_history.estimate("testvale", "forecast")
    assert est3["samples"] <= job_history.WINDOW
    assert est3["median_s"] >= 40.0  # the old 600/900/1200 set is gone


def test_eta_store_is_git_ignored_next_to_transactions(tmp_spine_root):
    """The history lives beside the transactions dir, outside the campaigns
    repo's canon: a .gitignore of its own, like the recovery payloads."""
    job_history.record("testvale", "map-site", 12.0)
    d = tmp_spine_root / "testvale" / ".studio-jobs"
    assert (d / "history.json").is_file()
    assert (d / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_record_failure_never_raises(tmp_spine_root, monkeypatch):
    """History is an optimization: a failing store must not take the job
    (or the caller) down."""
    monkeypatch.setattr(job_history, "roster_of",
                        lambda name: (_ for _ in ()).throw(RuntimeError("x")))
    job_history.record("testvale", "map-site", 5.0)  # must not raise
    assert job_history.estimate("testvale", "map-site") is not None


# ------------------------------------------------- the estimate in busy 409s

def test_busy_409_carries_blocked_by_estimate(tmp_spine_root, client,
                                              monkeypatch):
    """A short interactive writer racing a job gets the 409 PLUS what is
    running and how long it usually takes — phrased as a wait a human can
    plan around, and labeled as an estimate."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "geography.yaml").write_text("nations: []\n",
                                                   encoding="utf-8")
    job_history.record("testvale", "lore-entries", 600.0)
    job_history.record("testvale", "lore-entries", 660.0)

    release = threading.Event()

    def blocker(campaign_dir, focus, log=None):
        release.wait(timeout=10)
        return {"entries": 0, "findings": []}

    monkeypatch.setattr("routers.lore.loregen.generate_entries", blocker)
    r1 = client.post("/api/campaign/testvale/lore/entries", json={})
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if jobs.running_jobs(campaign="testvale"):
            break
        time.sleep(0.02)

    # a pending-entries file + an approve attempt = the interactive-writer 409
    pending = camp / "lore" / "pending-9.yaml"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(
        "entries:\n- id: pw9-1\n  when: week 9\n  title: T\n  text: x\n"
        "  source: hand\n", encoding="utf-8")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-9.yaml", "all": True})
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert "retry when it finishes" in err
    assert "Blocked by: a backstory generation" in err
    assert "typically" in err and "an estimate from past runs" in err

    release.set()
    wait_for_job(client, r1.json()["job_id"], timeout=15.0)


def test_busy_409_without_history_still_honest(tmp_spine_root, client,
                                               monkeypatch):
    """No history yet: the 409 says what is running and admits it has no
    estimate, instead of inventing one."""
    camp = tmp_spine_root / "testvale"
    (camp / "world" / "geography.yaml").write_text("nations: []\n",
                                                   encoding="utf-8")
    release = threading.Event()

    def blocker(campaign_dir, focus, log=None):
        release.wait(timeout=10)
        return {"entries": 0, "findings": []}

    monkeypatch.setattr("routers.lore.loregen.generate_entries", blocker)
    r1 = client.post("/api/campaign/testvale/lore/entries", json={})
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if jobs.running_jobs(campaign="testvale"):
            break
        time.sleep(0.02)

    pending = camp / "lore" / "pending-9.yaml"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(
        "entries:\n- id: pw9-1\n  when: week 9\n  title: T\n  text: x\n"
        "  source: hand\n", encoding="utf-8")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-9.yaml", "all": True})
    assert r.status_code == 409, r.text
    assert "Blocked by:" in r.json()["error"]
    assert "no duration history yet" in r.json()["error"]

    release.set()
    wait_for_job(client, r1.json()["job_id"], timeout=15.0)


# ------------------------------------------------------------------ cleanup

def teardown_module(module):
    # any queue state this module left behind must not leak into later
    # tests: cancel queued jobs, then let the hardening-style release pass
    jobs.cancel_queued()
    jobs.wait_all(timeout=15.0)


# ------------------------------------------------------ ticket 13: honesty


def test_locked_and_position_see_an_external_process_holder(tmp_spine_root):
    """locked() probes the FILE lock, not just the threading lock: a CLI
    process mid-write must make submit_campaign_job answer queued:true
    (position 1) instead of 'running now' — and locked() say held."""
    import pathlib
    import subprocess
    import sys
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp(prefix="lock-ext-"))
    holder = root / "holder.py"
    holder.write_text(
        "import sys, time, pathlib\n"
        f"sys.path.insert(0, r'{spine.STUDIO_DIR}')\n"
        "import spine\n"
        f"spine.CAMPAIGNS_ROOT = pathlib.Path(r'{root}')\n"
        "lk = spine.campaign_lock('exthold')\n"
        "assert lk.acquire(timeout=5)\n"
        "print('held', flush=True)\n"
        "time.sleep(6)\n")
    p = subprocess.Popen([sys.executable, str(holder)],
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        saved = spine.CAMPAIGNS_ROOT
        spine.CAMPAIGNS_ROOT = root
        try:
            lock = spine.campaign_lock("exthold")
            assert lock.locked() is True, \
                "an external (CLI) holder must read as locked"
        finally:
            spine.CAMPAIGNS_ROOT = saved
    finally:
        p.kill()
        p.wait()


def test_submit_position_is_honest_behind_external_holder(
        tmp_spine_root, client, monkeypatch):
    """The 202 for the only studio-side job, queued behind a CLI holding
    the campaign's file lock, says queued (position 1) — not 'running
    now'."""
    import pathlib
    import subprocess
    import sys
    import tempfile

    def instant(campaign_dir, focus, log=None):
        return {"entries": 0, "findings": []}

    # the queued job must run the mocked generator the moment the holder
    # dies — never a real loregen pass in a test
    monkeypatch.setattr("routers.lore.loregen.generate_entries", instant)
    saved = spine.CAMPAIGNS_ROOT
    root = pathlib.Path(tempfile.mkdtemp(prefix="lock-202-"))
    holder = root / "holder.py"
    holder.write_text(
        f"import sys, time, pathlib\n"
        f"sys.path.insert(0, r'{spine.STUDIO_DIR}')\n"
        "import spine\n"
        f"spine.CAMPAIGNS_ROOT = pathlib.Path(r'{root}')\n"
        "lk = spine.campaign_lock('testvale')\n"
        "assert lk.acquire(timeout=5)\n"
        "print('held', flush=True)\n"
        "time.sleep(30)\n")
    p = subprocess.Popen([sys.executable, str(holder)],
                         stdout=subprocess.PIPE, text=True)
    job_id = None
    try:
        assert p.stdout.readline().strip() == "held"
        spine.CAMPAIGNS_ROOT = root
        (root / "testvale").mkdir(parents=True, exist_ok=True)
        # the submit only needs the campaign to exist far enough to build
        # the lock; give the endpoint a real campaign dir to accept
        (root / "testvale" / "campaign.yaml").write_text(
            "name: testvale\ntitle: testvale\n", encoding="utf-8")
        (root / "testvale" / "world").mkdir(exist_ok=True)
        (root / "testvale" / "world" / "geography.yaml").write_text(
            "nations: []\n", encoding="utf-8")
        r = client.post("/api/campaign/testvale/lore/entries", json={})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["queued"] is True and body["queue_position"] == 1, body
        job_id = body["job_id"]
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "queued"
    finally:
        spine.CAMPAIGNS_ROOT = saved
        p.kill()
        p.wait()
    if job_id:
        wait_for_job(client, job_id, timeout=15.0)


def test_lock_file_unusable_degrades_to_process_local(
        tmp_spine_root, monkeypatch):
    """An OSError ANYWHERE in the file-lock path (bootstrap or open)
    degrades loudly to process-local locking — never a raw 500, and the
    threading lock still excludes."""
    import pathlib
    blocker = tmp_spine_root / "iamafile"
    blocker.write_text("x", encoding="utf-8")

    def unusable_root():
        return pathlib.Path(str(blocker) + "/locks")  # under a FILE:
        # mkdir raises OSError

    monkeypatch.setattr(spine, "_locks_root", unusable_root)
    lock = spine.campaign_lock("degradevale")
    assert lock.acquire(blocking=False) is True
    try:
        assert lock._no_file_lock is True, "the degrade must have fired"
        second = lock.acquire(blocking=False)
        assert second is False, "process-local exclusion must hold"
    finally:
        lock.release()


def test_proposal_flow_409_carries_the_wait_line(tmp_spine_root):
    """proposal_flows._lock's 409 (accept/discard racing a running job)
    carries the same Blocked-by line every other busy-409 has (ticket 13
    — it used to be the static guess only)."""
    import proposal_flows
    (tmp_spine_root / "testvale").mkdir(parents=True, exist_ok=True)
    release = threading.Event()

    def slow(log):
        release.wait(timeout=10)

    jobs.submit("sim-week", slow, campaign="testvale")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if jobs.running_jobs(campaign="testvale"):
            break
        time.sleep(0.02)
    try:
        with spine.campaign_lock("testvale"):
            try:
                with proposal_flows._lock("testvale",
                                          "accepting the proposal"):
                    raise AssertionError("the lock must have fast-failed")
            except proposal_flows.ProposalFlowError as e:
                assert "Blocked by: a week run" in e.detail, e.detail
                assert "no duration history yet" in e.detail, e.detail
    finally:
        release.set()
    assert jobs.wait_all(timeout=15.0)
