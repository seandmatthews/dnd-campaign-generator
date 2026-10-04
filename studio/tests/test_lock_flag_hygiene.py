"""Lock & flag hygiene II (ticket 26): style writes under the campaign
lock, the week flag's write-after-lock lifecycle, the engine home's
cross-process lock file, and recovery-under-lock.

Everything runs against temp dirs — no real engine, no real engine home
(engines.AGENTIC_SIMULATOR is relocated per test exactly the way the
neighboring suites relocate it)."""

import json
import threading
import time
from unittest import mock

import engines
import jobs
import publication
import spine
from conftest import wait_for_job


def fake_tick_ok(engine_root, script, *args, timeout=600, on_line=None,
                 **kw):
    """The smallest engine that advances the clock to week 4."""
    world = engines.ENGINE_HOME
    world.mkdir(parents=True, exist_ok=True)
    world.joinpath("state.yaml").write_text(
        "meta:\n  clock:\n    week: 4\n", encoding="utf-8")
    chron = engines.engine_chronicle_dir()
    chron.mkdir(exist_ok=True)
    (chron / "0004-the-gate.md").write_text("# Week 4 — The Gate\n",
                                            encoding="utf-8")
    return 0, ["wardens HOLDS (quiet week)", "week 4 written"]


# ------------------------------------------------- style writes (P3-12)


def test_style_put_and_post_409_while_campaign_lock_held(client,
                                                         tmp_spine_root):
    """AC: style writes are canon writers with a snapshot attached, so they
    fast-fail 409 like every sibling interactive writer while a job holds
    the campaign lock — and go through on their merits once it frees."""
    lock = spine.campaign_lock("testvale")
    assert lock.acquire(blocking=False), "the test needs the free lock"
    try:
        r = client.put("/api/campaign/testvale/lore/style/voice.md",
                       json={"text": "terse"})
        assert r.status_code == 409, r.text
        assert "retry when it finishes" in r.json()["error"]
        r = client.post("/api/campaign/testvale/lore/style/names-new.txt",
                        json={"text": "Ari\nBela\nCade\n"})
        assert r.status_code == 409, r.text
        assert "retry when it finishes" in r.json()["error"]
        # nothing was written while refused
        assert not (tmp_spine_root / "testvale" / "lore" / "style"
                    / "names-new.txt").exists()
    finally:
        lock.release()

    # lock free: the same writes land (PUT edits the fixture's voice.md,
    # POST stages a seed list)
    r = client.put("/api/campaign/testvale/lore/style/voice.md",
                   json={"text": "terse"})
    assert r.status_code == 200, r.text
    r = client.post("/api/campaign/testvale/lore/style/names-new.txt",
                    json={"text": "Ari\nBela\nCade\n"})
    assert r.status_code == 201, r.text
    assert (tmp_spine_root / "testvale" / "lore" / "style"
            / "names-new.txt").read_text(encoding="utf-8") == \
        "Ari\nBela\nCade\n"


# ------------------------------------------- week-flag lifecycle (P3-29)


def test_engine_lock_deadline_mid_queue_leaves_no_dangling_flag(
        client, tmp_spine_root, monkeypatch):
    """AC: a queued week whose bounded wait for the engine home gives up
    must not leave the week flag behind — the flag is written only once
    the engine lock is HELD, so GET status stays honest afterwards (the
    old code wrote it before the wait and it dangled until reboot)."""
    monkeypatch.setattr(engines, "ENGINE_HOME",
                        tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME",
                        tmp_spine_root / "chronicle")

    # this campaign's own week holds the engine home (holder=testvale), so
    # the probe lets a second week QUEUE behind it instead of 409ing
    entered = threading.Event()
    release = threading.Event()

    def first_week(log):
        with engines.engine_lock_logged(log, "a week run or forecast",
                                        holder="testvale"):
            entered.set()
            assert release.wait(timeout=15), "test never released the week"

    jobs.submit("sim-week", first_week, campaign="testvale")
    assert entered.wait(timeout=5), "the first week never took the home"

    # shrink the bounded waits to test time: the queued run's dead-man
    # deadline fires instead of waiting the real 93 minutes
    monkeypatch.setattr(engines, "ENGINE_LOCK_FIRST_WAIT_S", 0.05)
    monkeypatch.setattr(engines, "engine_lock_deadline_s", lambda: 0.05)
    try:
        r = client.post("/api/campaign/testvale/simulation/week",
                        json={"confirm": True})
        assert r.status_code == 202, r.text
        assert r.json()["queued"] is True, "it queues behind its own week"
        job = wait_for_job(client, r.json()["job_id"], timeout=30.0)
    finally:
        release.set()
    assert job["status"] == "error", job
    assert "stayed busy" in job["error"], job["error"]

    # the flag was never written: no dangling marker, honest health
    assert not engines.week_flag_path().exists(), \
        "a run that never held the home must not leave a week flag"
    assert client.get("/api/health").json()["week_run_in_progress"] is False


def test_queued_week_waits_without_touching_the_running_flag(
        client, tmp_spine_root, monkeypatch):
    """The attribution half of P3-29: the flag is written only once the
    engine lock is HELD, so a week queued behind the running one waits
    without overwriting the live flag (the old pre-lock write let a queued
    job rewrite it mid-run — a crash then made boot restore blame the
    wrong campaign)."""
    monkeypatch.setattr(engines, "ENGINE_HOME",
                        tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME",
                        tmp_spine_root / "chronicle")
    monkeypatch.setattr(engines, "run_py", fake_tick_ok)
    entered = threading.Event()
    release = threading.Event()

    def first_week(log):
        with engines.engine_lock_logged(log, "a week run or forecast",
                                        holder="testvale"):
            engines.write_week_flag("testvale")
            entered.set()
            assert release.wait(timeout=15), "test never released the week"

    jobs.submit("sim-week", first_week, campaign="testvale")
    assert entered.wait(timeout=5)
    first_started = json.loads(
        engines.week_flag_path().read_text(encoding="utf-8"))["started"]

    # same-campaign holder: the second week QUEUEs behind it (ticket 24)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202 and r.json()["queued"] is True, r.text
    time.sleep(0.3)  # let the queued job actually reach its wait
    doc = json.loads(engines.week_flag_path().read_text(encoding="utf-8"))
    assert doc == {"campaign": "testvale", "started": first_started}, \
        "the queued run must not rewrite the running job's flag"

    release.set()
    job = wait_for_job(client, r.json()["job_id"], timeout=30.0)
    assert job["status"] == "done", job["error"]
    jobs.wait_all(timeout=30.0)  # the first week job settles too
    assert not engines.week_flag_path().exists(), \
        "the second run cleared the flag in its finally, as always"


def test_clear_week_flag_leaves_a_foreign_flag_alone(tmp_path, monkeypatch):
    """The compare-and-unlink guard (module lock + attribution): clearing
    campaign A's flag must not delete the live flag a later run wrote."""
    monkeypatch.setattr(engines, "ENGINE_HOME", tmp_path / "home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME", tmp_path / "chronicle")
    engines.write_week_flag("aval")
    engines.clear_week_flag("othervale")
    doc = json.loads(engines.week_flag_path().read_text(encoding="utf-8"))
    assert doc["campaign"] == "aval", "a foreign clear must not delete it"
    engines.clear_week_flag("aval")
    assert not engines.week_flag_path().exists()


# ------------------------------------- engine-home cross-process (P3-11)


def test_engine_job_holds_the_home_lock_and_marks_its_children(
        client, tmp_spine_root, monkeypatch):
    """AC: while a studio engine job runs, the home's lock FILE is held
    (what a standalone tick.py --live / effects.py --apply refuses on), and
    the engine child carries STUDIO_ENGINE_HOME_HELD so it does not
    re-take — and deadlock on — its parent's claim. Released after."""
    monkeypatch.setattr(engines, "ENGINE_HOME",
                        tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME",
                        tmp_spine_root / "chronicle")
    seen = {}

    def fake_tick(engine_root, script, *args, timeout=600, on_line=None,
                  env=None, **kw):
        seen["home_locked"] = engines.engine_home_lock_held()
        seen["child_env"] = {**(env or {})}
        return fake_tick_ok(engine_root, script, *args, timeout=timeout,
                            on_line=on_line)

    monkeypatch.setattr(engines, "run_py", fake_tick)
    assert not engines.engine_home_lock_held()  # control: free before
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert seen["home_locked"] is True, \
        "the whole copy-in/run/diff section must hold the lock file"
    assert seen["child_env"].get(engines.STUDIO_ENGINE_HOME_HELD) == "1"
    assert not engines.engine_home_lock_held(), \
        "the lock file must be released when the job ends"


def test_engine_job_refuses_when_a_standalone_holds_the_home(
        client, tmp_spine_root, monkeypatch):
    """The symmetric half: a standalone process holding the home lock file
    (what tick.py --live / effects.py --apply do when run outside the
    studio) makes the studio's engine job fail with the readable message
    instead of interleaving — nothing run, nothing spent."""
    monkeypatch.setattr(engines, "ENGINE_HOME",
                        tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME",
                        tmp_spine_root / "chronicle")
    monkeypatch.setattr(engines, "run_py", fake_tick_ok)

    fh, held = spine.try_file_lock(engines.engine_home_lock_path())
    assert not held and fh is not None
    try:
        r = client.post("/api/campaign/testvale/simulation/week",
                        json={"confirm": True})
        assert r.status_code == 202, r.text
        job = wait_for_job(client, r.json()["job_id"], timeout=30.0)
    finally:
        spine.release_file_lock(fh)
    assert job["status"] == "error", job
    assert "outside the studio" in job["error"], job["error"]
    # the flag never landed (the run refused before writing it)
    assert not engines.week_flag_path().exists()


# -------------------------------------------- recovery under lock (P3-30)


def test_recover_campaign_holds_the_campaign_lock(tmp_spine_root):
    """AC: boot recovery is a campaign writer (live replaces + history
    snapshots), so it rolls forward UNDER the campaign lock — asserted
    from inside the write it performs."""
    import shutil
    d = tmp_spine_root / "testvale"
    tx_root = d / publication.TX_DIR_NAME / "tx-lock-check"
    image = tx_root / "after" / "lore" / "staged.yaml"
    image.parent.mkdir(parents=True)
    image.write_text("entries: [recovered]\n", encoding="utf-8")
    target = d / "lore" / "staged.yaml"
    target.write_text("entries: []\n", encoding="utf-8")
    (tx_root / "manifest.json").write_text(json.dumps({
        "transaction": "tx-lock-check", "status": "open", "staged": True,
        "operation": "lock check",
        "targets": [{
            "path": "lore/staged.yaml",
            "before": publication.file_revision(target),
            "after": publication.file_revision(image),
        }],
    }), encoding="utf-8")

    locked_during_write = []
    real_apply = publication._apply_text

    def probing_apply(t, text):
        locked_during_write.append(
            spine.campaign_lock("testvale").locked())
        return real_apply(t, text)

    with mock.patch.object(publication, "_apply_text", probing_apply):
        out = publication.recover_campaign("testvale")
    assert out["recovered"] == ["tx-lock-check"], out
    assert locked_during_write and all(locked_during_write), \
        "the roll-forward write must happen with the campaign lock held"
    assert target.read_text(encoding="utf-8") == "entries: [recovered]\n", \
        "the roll-forward actually landed (the probe saw the real write)"
    # and released afterwards
    assert spine.campaign_lock("testvale").acquire(blocking=False)
    spine.campaign_lock("testvale").release()
    shutil.rmtree(tx_root, ignore_errors=True)


def test_recover_campaign_defers_when_the_lock_cannot_be_taken(
        tmp_spine_root, monkeypatch):
    """A campaign still locked after the bounded boot wait is left
    untouched — its transactions stay open for the next boot, never
    half-rolled under someone else's write."""
    monkeypatch.setattr(publication, "RECOVERY_LOCK_WAIT_S", 0.05)
    # a transactions area exists (recovery has something to look at), so
    # the deferral is the lock refusing — not "nothing to do"
    (tmp_spine_root / "testvale" / publication.TX_DIR_NAME).mkdir(
        parents=True, exist_ok=True)
    lock = spine.campaign_lock("testvale")
    assert lock.acquire(blocking=False), "the test needs the free lock"
    try:
        out = publication.recover_campaign("testvale")
    finally:
        lock.release()
    assert out.get("skipped") is True
    assert out["recovered"] == [] and out["conflicts"] == []
