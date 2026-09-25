"""Phase 1: revision checks, recoverable multi-file publication, receipts,
and visible snapshot status.

Behavior under test (IMPLEMENTATION-HANDOFF.md Phase 1):
- a revision is sha256 of exact bytes with an explicit absent sentinel;
  creating/deleting a source page invalidates a generated draft's
  directory fingerprint;
- a stale source / changed pending record / changed membership each
  produce a conflict with NOTHING written;
- a crash mid-publication leaves an open transaction that boot recovery
  rolls forward, or parks as a conflict when a file was edited externally
  (external bytes are never overwritten on their own);
- duplicate submission applies once (receipts survive restart);
- git failure vs git absence vs nothing-to-commit are distinguishable and
  visible in the API result, not just logs;
- a partial transaction is never readable as a completed approval.

The testvale fixture ships two timeline entries (t0001, t0002) — approvals
below assert against that baseline. Failure injection uses
mock.patch.object (NOT monkeypatch: undoing a shared monkeypatch instance
would also undo the tmp_spine_root fixture's CAMPAIGNS_ROOT patch).
"""

import json
import pathlib
import unittest.mock as mock

import canon
import publication
import spine
import yaml

FIXTURE_TIMELINE_LEN = 2


def _pending(root, rel, entries, **extra):
    path = root / "testvale" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"entries": entries, "created": "2026-09-24T00:00:00+00:00",
           **extra}
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _entry(eid="pw9-1", title="T", text="x"):
    return {"id": eid, "when": "week 9", "title": title, "text": text,
            "source": "sim:week-9"}


def _timeline_entries(root):
    p = root / "testvale" / "lore" / "timeline.yaml"
    if not p.exists():
        return []
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    return doc if isinstance(doc, list) else (doc or {}).get("entries") or []


def _crash_after(n_writes):
    """Patch publication._apply_text to raise once n_writes live writes have
    landed (simulating a crash mid-publication)."""
    real = publication._apply_text
    calls = {"n": 0}

    def flaky(target, text):
        calls["n"] += 1
        if calls["n"] > n_writes:
            raise RuntimeError("injected crash")
        return real(target, text)

    return mock.patch.object(publication, "_apply_text", flaky)


def _crash_immediately():
    return mock.patch.object(
        publication, "_apply_text",
        mock.Mock(side_effect=RuntimeError("injected crash")))


# ------------------------------------------------------------- revisions


def test_file_revision_absent_sentinel_and_content_change(tmp_path):
    f = tmp_path / "a.yaml"
    assert publication.file_revision(f) == publication.ABSENT
    f.write_bytes(b"hello")
    rev1 = publication.file_revision(f)
    assert rev1 != publication.ABSENT and len(rev1) == 64
    f.write_bytes(b"hello ")
    assert publication.file_revision(f) != rev1  # exact bytes, not mtime


def test_dir_fingerprint_invalidated_by_membership_change(tmp_path):
    d = tmp_path / "entities"
    d.mkdir()
    (d / "One.md").write_text("one", encoding="utf-8")
    fp1 = publication.fingerprint_dir(d)
    (d / "Two.md").write_text("two", encoding="utf-8")
    assert publication.fingerprint_dir(d) != fp1  # creating a page invalidates
    (d / "Two.md").unlink()
    assert publication.fingerprint_dir(d) == fp1  # deleting restores it here
    assert publication.fingerprint_dir(tmp_path / "nope") == \
        publication.ABSENT


# ------------------------------------------------------------ publication


def test_publish_writes_verifies_and_records_manifest(tmp_spine_root):
    rel_a = "lore/approved-entry.yaml"
    rel_b = "lore/pending-entries-77.yaml"
    (tmp_spine_root / "testvale" / "lore").mkdir(parents=True, exist_ok=True)
    (tmp_spine_root / "testvale" / rel_b).write_text("old pending\n",
                                                     encoding="utf-8")
    pub = publication.publish(
        "testvale",
        [{"path": rel_a, "text": "entries: [a]\n"},
         {"path": rel_b, "delete": True}],
        operation="test publish")
    assert pub["state"] == "published"
    d = tmp_spine_root / "testvale"
    assert (d / rel_a).read_bytes() == b"entries: [a]\n"  # exact bytes, LF
    assert not (d / rel_b).exists()
    m = json.loads(
        (d / publication.TX_DIR_NAME / pub["transaction"] / "manifest.json")
        .read_text(encoding="utf-8"))
    assert m["status"] == "complete" and len(m["targets"]) == 2
    deleted = [t for t in m["targets"] if t["delete"]]
    assert deleted and deleted[0]["after"] == publication.ABSENT
    # before-images are preserved for both targets (created stays absent,
    # the deleted file's exact bytes are kept)
    images = d / publication.TX_DIR_NAME / pub["transaction"] / "before"
    assert not (images / "lore" / "approved-entry.yaml").exists()
    assert (images / "lore" / "pending-entries-77.yaml").read_text(
        encoding="utf-8") == "old pending\n"


def test_publish_noop_when_all_targets_already_match(tmp_spine_root):
    (tmp_spine_root / "testvale" / "lore").mkdir(parents=True, exist_ok=True)
    (tmp_spine_root / "testvale" / "lore" / "x.yaml").write_bytes(b"same\n")
    pub = publication.publish(
        "testvale", [{"path": "lore/x.yaml", "text": "same\n"}],
        operation="noop test")
    assert pub["state"] == "noop" and pub["transaction"] is None


def test_publish_refuses_unsafe_targets(tmp_spine_root):
    # every rejection class of _safe_target: traversal, absolute, the
    # transaction area, drive-colon (Windows: "C:evil" or "C:/evil" joins
    # as a drive path), and control characters
    for bad in ("../escape.yaml", "/abs.yaml", ".studio-transactions/x.yaml",
                "C:/evil.yaml", "C:evil.yaml", "lore/ctrl\x01.yaml"):
        try:
            publication.publish("testvale", [{"path": bad, "text": "x"}],
                                operation="bad")
        except publication.PublicationError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"unsafe target accepted: {bad!r}")
    assert not (tmp_spine_root.parent / "escape.yaml").exists()
    assert not (tmp_spine_root.parent / "evil.yaml").exists()
    assert not pathlib.Path("C:/evil.yaml").exists(), \
        "a drive-qualified target must be refused before any write"


def test_stale_base_revision_conflicts_and_writes_nothing(tmp_spine_root):
    geo = tmp_spine_root / "testvale" / "world" / "geography.yaml"
    before = geo.read_bytes()
    stale_rev = publication.file_revision(geo)
    geo.write_bytes(before + b"CHANGED\n")
    target = tmp_spine_root / "testvale" / "lore" / "timeline.yaml"
    before_timeline = target.read_bytes()
    try:
        publication.publish(
            "testvale", [{"path": "lore/timeline.yaml", "text": "entries\n"}],
            operation="stale",
            base_revisions={"world/geography.yaml": stale_rev})
    except publication.RevisionConflict as e:
        assert "geography.yaml" in str(e)
    else:  # pragma: no cover
        raise AssertionError("stale base revision did not conflict")
    assert target.read_bytes() == before_timeline  # nothing was written


# --------------------------------------------------------------- recovery


def test_crash_mid_publish_recovers_forward(tmp_spine_root):
    with _crash_after(1):
        try:
            publication.publish(
                "testvale",
                [{"path": "lore/week-entry.yaml", "text": "entries: [x]\n"},
                 {"path": "lore/other.yaml", "text": "two\n"}],
                operation="crash test")
        except RuntimeError:
            pass
        else:  # pragma: no cover
            raise AssertionError("injected failure did not fire")

    d = tmp_spine_root / "testvale"
    txs = publication.transactions("testvale")
    assert [t["status"] for t in txs] == ["open"]  # left for recovery
    # a partial transaction is not silently readable as a completed approval:
    # the second target is still at its before-state on disk
    assert not (d / "lore" / "other.yaml").exists()

    out = publication.recover_campaign("testvale")  # what boot does
    assert out["recovered"] and not out["conflicts"]
    assert (d / "lore" / "week-entry.yaml").read_bytes() == b"entries: [x]\n"
    assert (d / "lore" / "other.yaml").read_bytes() == b"two\n"
    assert all(t["status"] != "open"
               for t in publication.transactions("testvale"))


def test_external_edit_parks_conflict_and_never_overwrites(tmp_spine_root):
    with _crash_after(1):
        try:
            publication.publish(
                "testvale",
                [{"path": "lore/first.yaml", "text": "staged one\n"},
                 {"path": "lore/second.yaml", "text": "staged two\n"}],
                operation="conflict test")
        except RuntimeError:
            pass

    d = tmp_spine_root / "testvale"
    # an external editor touches the untouched target while the tx is open
    (d / "lore" / "second.yaml").write_text("external edit\n",
                                            encoding="utf-8")

    out = publication.recover_campaign("testvale")
    assert out["conflicts"] and not out["recovered"]
    # the external edit is preserved, byte for byte
    assert (d / "lore" / "second.yaml").read_text(
        encoding="utf-8") == "external edit\n"
    conflict = publication.unresolved_conflicts("testvale")[0]
    assert "lore/second.yaml" in conflict["conflicts"]

    # publish-path writers refuse while unresolved
    try:
        publication.publish("testvale",
                            [{"path": "lore/x.yaml", "text": "y"}],
                            operation="blocked")
    except publication.PublicationConflictError:
        pass
    else:  # pragma: no cover
        raise AssertionError("publication did not refuse on conflict")

    # discard: live bytes stand, the transaction is abandoned
    publication.resolve("testvale", conflict["transaction"], "discard")
    assert (d / "lore" / "second.yaml").read_text(
        encoding="utf-8") == "external edit\n"
    assert publication.unresolved_conflicts("testvale") == []
    assert (d / "lore" / "first.yaml").exists()  # the applied half stands


def test_resolve_apply_forces_staged_outcome_over_external_edit(
        tmp_spine_root):
    with _crash_immediately():
        try:
            publication.publish(
                "testvale",
                [{"path": "lore/first.yaml", "text": "staged\n"}],
                operation="apply test")
        except RuntimeError:
            pass
    d = tmp_spine_root / "testvale"
    (d / "lore" / "first.yaml").write_text("external\n", encoding="utf-8")
    publication.recover_campaign("testvale")
    tx = publication.unresolved_conflicts("testvale")[0]["transaction"]
    out = publication.resolve("testvale", tx, "apply")
    assert out["resolution"] == "apply"
    assert (d / "lore" / "first.yaml").read_text(
        encoding="utf-8") == "staged\n"


# ----------------------------------------------------------------- routes


def test_get_review_exposes_revision_and_stale_submit_conflicts(
        client, tmp_spine_root):
    path = _pending(tmp_spine_root, "simulation/pending-week-9.yaml",
                    [_entry()])
    r = client.get("/api/campaign/testvale/review")
    files = {f["file"]: f for f in r.json()["files"]}
    rev = files["simulation/pending-week-9.yaml"]["revision"]
    assert rev and rev != publication.ABSENT

    timeline = (tmp_spine_root / "testvale" / "lore" / "timeline.yaml")
    before = timeline.read_bytes()
    path.write_text(
        yaml.safe_dump({"entries": [_entry(title="EDITED")],
                        "created": "x", "week": 9}, sort_keys=False),
        encoding="utf-8")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "simulation/pending-week-9.yaml",
                          "all": True, "expected_revision": rev})
    assert r.status_code == 409 and "changed on disk" in r.json()["error"]
    # nothing was written: the timeline is byte-identical, the pending edit stands
    assert timeline.read_bytes() == before
    assert "EDITED" in path.read_text(encoding="utf-8")


def test_approve_with_matching_revision_succeeds(client, tmp_spine_root):
    _pending(tmp_spine_root, "simulation/pending-week-9.yaml", [_entry()])
    rev = {f["file"]: f["revision"]
           for f in client.get("/api/campaign/testvale/review").json()["files"]
           }["simulation/pending-week-9.yaml"]
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "simulation/pending-week-9.yaml",
                          "all": True, "expected_revision": rev})
    assert r.status_code == 200
    assert len(_timeline_entries(tmp_spine_root)) == FIXTURE_TIMELINE_LEN + 1


def test_generated_draft_stale_source_and_membership_conflict(
        client, tmp_spine_root):
    entities = tmp_spine_root / "testvale" / "lore" / "entities"
    (entities / "Old Page.md").write_text("old", encoding="utf-8")
    stale_fp = publication.fingerprint_dir(entities)
    timeline = tmp_spine_root / "testvale" / "lore" / "timeline.yaml"
    doc = {"entries": [_entry()], "created": "x",
           "base_revisions": {"lore/timeline.yaml": "0" * 64},
           "input_fingerprints": {"lore/entities": stale_fp}}
    _pending(tmp_spine_root, "lore/pending-entries-5.yaml", doc["entries"],
             base_revisions=doc["base_revisions"],
             input_fingerprints=doc["input_fingerprints"])

    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-5.yaml", "all": True})
    assert r.status_code == 409  # stale source revision -> regenerate

    # fix the source revision but delete a page: membership changed
    doc["base_revisions"]["lore/timeline.yaml"] = publication.file_revision(
        timeline)
    _pending(tmp_spine_root, "lore/pending-entries-5.yaml", doc["entries"],
             base_revisions=doc["base_revisions"],
             input_fingerprints=doc["input_fingerprints"])
    (entities / "Old Page.md").unlink()
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-5.yaml", "all": True})
    assert r.status_code == 409 and "membership changed" in r.json()["error"]


def test_duplicate_approve_applies_once(client, tmp_spine_root):
    path = _pending(tmp_spine_root, "simulation/pending-week-9.yaml",
                    [_entry()])
    body = {"file": "simulation/pending-week-9.yaml", "all": True,
            "expected_revision": publication.file_revision(path)}
    r1 = client.post("/api/campaign/testvale/review/approve", json=body)
    assert r1.status_code == 200
    r2 = client.post("/api/campaign/testvale/review/approve", json=body)
    assert r2.status_code == 200 and r2.json().get("idempotent") is True
    assert len(_timeline_entries(tmp_spine_root)) == FIXTURE_TIMELINE_LEN + 1
    # and the retry works even after the pending file is gone (deleted
    # when emptied): a restart-safe answer, not a 404
    assert not path.exists()


def test_approve_after_restart_still_idempotent(client, tmp_spine_root):
    """A genuine restart: a fresh TestClient entered as a context manager
    runs the lifespan (boot recovery over every campaign) before the
    repeat — the answer comes from the on-disk receipt, not a cache."""
    path = _pending(tmp_spine_root, "lore/pending-entries-3.yaml",
                    [{"id": "x1", "when": "w", "title": "T", "text": "x",
                      "source": "authored"}])
    body = {"file": "lore/pending-entries-3.yaml", "all": True,
            "expected_revision": publication.file_revision(path)}
    r1 = client.post("/api/campaign/testvale/review/approve", json=body)
    assert r1.status_code == 200
    assert len(_timeline_entries(tmp_spine_root)) == FIXTURE_TIMELINE_LEN + 1

    from fastapi.testclient import TestClient
    import server
    with TestClient(server.app) as restarted:  # lifespan: recover_campaign
        r2 = restarted.post("/api/campaign/testvale/review/approve", json=body)
    assert r2.status_code == 200 and r2.json().get("idempotent") is True
    assert len(_timeline_entries(tmp_spine_root)) == FIXTURE_TIMELINE_LEN + 1


def test_partial_crash_never_presents_as_completed(client, tmp_spine_root):
    path = _pending(tmp_spine_root, "simulation/pending-week-9.yaml",
                    [_entry()])
    rev = publication.file_revision(path)

    with _crash_immediately():
        r = client.post("/api/campaign/testvale/review/approve",
                        json={"file": "simulation/pending-week-9.yaml",
                              "all": True, "expected_revision": rev})
    assert r.status_code == 500  # surfaced as a failure, not a success
    assert "recovered automatically" in r.json()["error"]
    # the queue still shows the entry; the timeline has nothing new
    assert len(_timeline_entries(tmp_spine_root)) == FIXTURE_TIMELINE_LEN
    assert "pw9-1" in path.read_text(encoding="utf-8")
    # recovery (as boot does) completes the approval exactly once
    out = publication.recover_campaign("testvale")
    assert out["recovered"]
    assert len(_timeline_entries(tmp_spine_root)) == FIXTURE_TIMELINE_LEN + 1
    assert not path.exists()


def test_campaign_summary_reports_publication_conflict(client, tmp_spine_root):
    with _crash_immediately():
        try:
            publication.publish(
                "testvale", [{"path": "lore/x.yaml", "text": "x"}],
                operation="summary test")
        except RuntimeError:
            pass
    (tmp_spine_root / "testvale" / "lore" / "x.yaml").write_text(
        "external\n", encoding="utf-8")
    publication.recover_campaign("testvale")
    r = client.get("/api/campaign/testvale")
    assert r.status_code == 200
    pub = r.json().get("publication")
    assert pub and pub["transactions"][0]["status"] == "conflict"


# ------------------------------------------------------ snapshot reporting


def test_snapshot_unavailable_is_visible_and_retry_prunes(
        client, tmp_spine_root, monkeypatch):
    monkeypatch.setattr(canon, "AVAILABLE", False)
    _pending(tmp_spine_root, "simulation/pending-week-9.yaml", [_entry()])
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "simulation/pending-week-9.yaml",
                          "all": True})
    assert r.status_code == 200
    assert r.json()["snapshot"]["state"] == "unavailable"
    assert r.json()["snapshot"]["commit"] is None
    tx = r.json()["transaction"]
    d = tmp_spine_root / "testvale"
    assert (d / publication.TX_DIR_NAME / tx / "manifest.json").exists(), \
        "payload retained while history snapshot is missing"

    monkeypatch.setattr(canon, "AVAILABLE", True)
    r = client.post("/api/campaign/testvale/snapshot", json={})
    assert r.status_code == 200
    assert r.json()["snapshot"]["state"] in ("committed", "no_change")
    assert not (d / publication.TX_DIR_NAME / tx).exists()  # pruned


def test_git_failure_distinguishable_from_no_change(
        client, tmp_spine_root):
    _pending(tmp_spine_root, "simulation/pending-week-9.yaml", [_entry()])

    def failing_git(root, *args):
        if args[0] == "commit":
            return 128, "", "fatal: injected commit failure"
        return 0, "", ""

    with mock.patch.object(canon, "_git", failing_git):
        r = client.post("/api/campaign/testvale/review/approve",
                        json={"file": "simulation/pending-week-9.yaml",
                              "all": True})
    assert r.status_code == 200  # the publication itself succeeded
    assert r.json()["snapshot"]["state"] == "failed"
    assert "injected commit failure" in r.json()["snapshot"]["detail"]
    # the payload is retained for the snapshot retry
    tx = r.json()["transaction"]
    assert (tmp_spine_root / "testvale" / publication.TX_DIR_NAME / tx
            / "manifest.json").exists()

    r = client.post("/api/campaign/testvale/snapshot", json={})
    assert r.json()["snapshot"]["state"] in ("committed", "no_change")
    # the retry finished the bookkeeping: payload pruned
    assert not (tmp_spine_root / "testvale" / publication.TX_DIR_NAME / tx
                ).exists()


def test_no_change_state_distinguishable(client, tmp_spine_root):
    # an approve that changes nothing in git's eyes reports no_change,
    # a success state, distinct from failed/unavailable — pinned exactly:
    # the first snapshot commits (the fixture copy has no repo yet), and
    # an immediate second one has nothing to commit
    r1 = client.post("/api/campaign/testvale/snapshot", json={})
    assert r1.status_code == 200
    assert r1.json()["snapshot"]["state"] == "committed", r1.json()
    r2 = client.post("/api/campaign/testvale/snapshot", json={})
    assert r2.status_code == 200
    assert r2.json()["snapshot"]["state"] == "no_change", r2.json()


def test_boot_retries_a_failed_snapshot_over_a_complete_transaction(
        client, tmp_spine_root):
    """The snapshot-failed half of boot recovery (_retry_snapshot): a
    COMPLETE transaction whose git snapshot failed keeps its payload; boot
    retries once — success prunes, failure retains."""
    _pending(tmp_spine_root, "simulation/pending-week-9.yaml", [_entry()])

    def failing_git(root, *args):
        if args[0] == "commit":
            return 128, "", "fatal: injected commit failure"
        return 0, "", ""

    with mock.patch.object(canon, "_git", failing_git):
        r = client.post("/api/campaign/testvale/review/approve",
                        json={"file": "simulation/pending-week-9.yaml",
                              "all": True})
    assert r.status_code == 200 and r.json()["snapshot"]["state"] == "failed"
    tx = r.json()["transaction"]
    tx_dir = tmp_spine_root / "testvale" / publication.TX_DIR_NAME / tx
    assert tx_dir.exists(), "payload retained while the snapshot failed"

    # boot with git still failing: the payload is retained
    with mock.patch.object(canon, "_git", failing_git):
        out = publication.recover_campaign("testvale")
    assert out["pruned"] == [] and tx_dir.exists()

    # boot with git healthy: the retry commits and prunes the payload
    out = publication.recover_campaign("testvale")
    assert out["pruned"] == [tx]
    assert not tx_dir.exists()


def test_publication_resolve_route_applies_over_external_edits(
        client, tmp_spine_root):
    """The only UI way to clear a parked conflict. Bad resolution / unknown
    transaction are 409s; 'apply' forces the staged outcome over the
    external bytes (an explicit DM choice — recovery never does it alone)."""
    with _crash_immediately():
        try:
            publication.publish("testvale",
                                [{"path": "lore/x.yaml", "text": "staged\n"}],
                                operation="resolve test")
        except RuntimeError:
            pass
    (tmp_spine_root / "testvale" / "lore" / "x.yaml").write_text(
        "external\n", encoding="utf-8")
    publication.recover_campaign("testvale")
    summary = client.get("/api/campaign/testvale").json()
    tx = summary["publication"]["transactions"][0]["transaction"]

    r = client.post("/api/campaign/testvale/publication/resolve",
                    json={"transaction": tx, "resolution": "nonsense"})
    assert r.status_code == 409 and "apply" in r.json()["error"]
    r = client.post("/api/campaign/testvale/publication/resolve",
                    json={"transaction": "no-such-tx", "resolution": "apply"})
    assert r.status_code == 409 and "no such transaction" in r.json()["error"]

    r = client.post("/api/campaign/testvale/publication/resolve",
                    json={"transaction": tx, "resolution": "apply"})
    assert r.status_code == 200 and r.json()["resolved"] == tx
    assert (tmp_spine_root / "testvale" / "lore" / "x.yaml").read_text(
        encoding="utf-8") == "staged\n", "apply forces the staged outcome"


def test_publication_resolve_discard_keeps_live_bytes(client, tmp_spine_root):
    with _crash_immediately():
        try:
            publication.publish("testvale",
                                [{"path": "lore/y.yaml", "text": "staged\n"}],
                                operation="resolve discard test")
        except RuntimeError:
            pass
    (tmp_spine_root / "testvale" / "lore" / "y.yaml").write_text(
        "external\n", encoding="utf-8")
    publication.recover_campaign("testvale")
    summary = client.get("/api/campaign/testvale").json()
    tx = summary["publication"]["transactions"][0]["transaction"]

    r = client.post("/api/campaign/testvale/publication/resolve",
                    json={"transaction": tx, "resolution": "discard"})
    assert r.status_code == 200 and r.json()["resolution"] == "discard"
    assert (tmp_spine_root / "testvale" / "lore" / "y.yaml").read_text(
        encoding="utf-8") == "external\n", "discard keeps the live bytes"
    # the conflict is cleared: publication writers work again
    publication.publish("testvale", [{"path": "lore/z.yaml", "text": "z"}],
                        operation="post-discard")
