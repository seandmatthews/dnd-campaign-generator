"""Review queue (M4): the union GET, approve into the timeline, delete,
pending-file traversal guards. check.py is real here (structural layer is
zero-dep); the one findings-attachment test fakes it for determinism."""

import unittest.mock as mock

import pytest

import publication
import yaml

LORE_PENDING = {
    "entries": [
        {"id": "t0003", "when": "year 200", "title": "Vale Lore Pending",
         "text": "An authored entry waiting in lore/pending.",
         "source": "authored"},
    ],
    "created": "2026-09-22T00:00:00+00:00",
    "focus": "the Vane",
}

SIM_PENDING = {
    "entries": [
        {"id": "pw7-1", "when": "week 7", "title": "Week Seven Event",
         "text": "A proposed entry from the live week.",
         "source": "sim:week-7"},
    ],
    "created": "2026-09-22T00:00:00+00:00",
    "week": 7,
}


def _write_pending(root, rel, doc):
    path = root / "testvale" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return path


def _timeline_entries(root):
    doc = yaml.safe_load(
        (root / "testvale" / "lore" / "timeline.yaml").read_text(encoding="utf-8"))
    if isinstance(doc, dict):
        return doc["entries"]
    return doc


def test_review_unions_lore_and_sim_pending(client, tmp_spine_root):
    _write_pending(tmp_spine_root, "lore/pending-entries-1.yaml", LORE_PENDING)
    _write_pending(tmp_spine_root, "simulation/pending-week-7.yaml", SIM_PENDING)
    r = client.get("/api/campaign/testvale/review")
    assert r.status_code == 200
    body = r.json()
    items = body["items"]
    # real check.py runs here and may add file-level "finding" items
    # (e.g. vale/skipped) — the union is about the two pending ENTRIES
    assert {i["id"] for i in items if i["kind"] != "finding"} == {"t0003", "pw7-1"}
    by_id = {i["id"]: i for i in items}
    assert by_id["t0003"]["kind"] == "lore-entry"
    assert by_id["pw7-1"]["kind"] == "timeline-approval"
    assert by_id["t0003"]["source"] == "authored"
    assert by_id["pw7-1"]["source"] == "sim:week-7"
    for item in items:
        assert isinstance(item["findings"], list)
        assert item["file"].startswith(("lore/", "simulation/"))
        assert item["title"] and item["body"]
    files = {f["file"]: f for f in body["files"]}
    assert files["lore/pending-entries-1.yaml"]["focus"] == "the Vane"
    assert files["simulation/pending-week-7.yaml"]["week"] == 7


def test_review_attaches_findings_to_citing_entry(client, tmp_spine_root,
                                                  monkeypatch):
    _write_pending(tmp_spine_root, "lore/pending-entries-2.yaml", LORE_PENDING)
    import routers.review as review

    def fake_findings(path, inside, deadline, notes):
        # line 2 is inside the first entry's span; line 1 (the header) is not
        return [
            {"file": inside, "line": 2, "rule": "test/cited", "level": "warning",
             "quote": "q", "note": "cites the entry"},
            {"file": inside, "line": 1, "rule": "test/loose", "level": "suggestion",
             "quote": "", "note": "file-level"},
        ]

    monkeypatch.setattr(review, "_structural_findings", fake_findings)
    r = client.get("/api/campaign/testvale/review")
    items = r.json()["items"]
    entry = next(i for i in items if i["id"] == "t0003")
    assert entry["findings"] == [
        {"rule": "test/cited", "level": "warning", "note": "cites the entry",
         "line": 2}]
    loose = [i for i in items if i["kind"] == "finding"]
    assert len(loose) == 1 and loose[0]["title"] == "test/loose"
    assert loose[0]["file"] == "lore/pending-entries-2.yaml"


def test_findings_budget_skip_notes(tmp_spine_root):
    """A blown budget skips the check and says so — findings never block."""
    import routers.review as review
    path = tmp_spine_root / "testvale" / "lore" / "pending-entries-3.yaml"
    notes: list = []
    out = review._structural_findings(path, "lore/pending-entries-3.yaml",
                                      deadline=0.0, notes=notes)
    assert out is None
    assert notes and "skipped" in notes[0]


def _core(r):
    """The pre-transactional response fields. Approve/delete now also carry
    `transaction` and `snapshot` (Phase 1: recoverable publication + visible
    snapshot status) — intentional contract change, asserted separately.
    The statblock counters (ticket 49) are asserted in
    test_statblock_fence.py."""
    d = dict(r.json())
    d.pop("transaction", None)
    d.pop("snapshot", None)
    d.pop("noop", None)
    d.pop("fences", None)
    d.pop("fences_skipped", None)
    return d


def test_approve_assigns_next_free_tids_and_deletes_empty_pending(
        client, tmp_spine_root):
    _write_pending(tmp_spine_root, "simulation/pending-week-8.yaml", {
        "entries": [
            {"id": "pw8-1", "when": "week 8", "title": "First",
             "text": "First proposed entry.", "source": "sim:week-8"},
            {"id": "pw8-2", "when": "week 8", "title": "Second",
             "text": "Second proposed entry.", "source": "sim:week-8"},
        ],
        "created": "2026-09-22T00:00:00+00:00", "week": 8,
    })
    pending = tmp_spine_root / "testvale" / "simulation" / "pending-week-8.yaml"
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "simulation/pending-week-8.yaml", "all": True})
    assert r.status_code == 200 and _core(r) == {
        "approved": 2, "entries": 2, "pages": 0, "pages_skipped": 0,
        "stubs": 0}
    assert r.json()["snapshot"]["state"] == "committed"
    assert r.json()["transaction"]
    assert not pending.exists()
    entries = _timeline_entries(tmp_spine_root)
    assert [e["id"] for e in entries] == ["t0001", "t0002", "t0003", "t0004"]
    assert entries[2]["source"] == "sim:week-8"
    assert entries[3]["title"] == "Second"


def test_approve_keeps_t_ids_and_renumbers_collisions(client, tmp_spine_root):
    """Ticket 37: stage-time ids are advisory. An entry whose own t-number
    is free keeps it; a collision with canon (a week accepted in between)
    renumbers to the next free t-number instead of dead-ending the
    approval."""
    path = _write_pending(tmp_spine_root, "lore/pending-entries-1.yaml",
                          LORE_PENDING)
    coll = dict(LORE_PENDING)
    coll["entries"] = [dict(LORE_PENDING["entries"][0], id="t0002")]
    path.write_text(yaml.safe_dump(coll, sort_keys=False), encoding="utf-8")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-1.yaml", "all": True})
    assert r.status_code == 200 and _core(r) == {
        "approved": 1, "entries": 1, "pages": 0, "pages_skipped": 0,
        "stubs": 0}
    entries = _timeline_entries(tmp_spine_root)
    assert [e["id"] for e in entries] == ["t0001", "t0002", "t0003"]
    assert entries[2]["title"] == "Vale Lore Pending"
    assert entries[2]["source"] == "authored"  # source kept
    assert not path.exists()

    free = dict(LORE_PENDING)
    free["entries"] = [dict(LORE_PENDING["entries"][0], id="t0005")]
    path.write_text(yaml.safe_dump(free, sort_keys=False), encoding="utf-8")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-1.yaml", "all": True})
    assert r.status_code == 200 and _core(r) == {
        "approved": 1, "entries": 1, "pages": 0, "pages_skipped": 0,
        "stubs": 0}
    entries = _timeline_entries(tmp_spine_root)
    assert [e["id"] for e in entries] == ["t0001", "t0002", "t0003", "t0005"]
    assert entries[3]["source"] == "authored"
    assert not path.exists()


def test_approve_renumbers_stage_ids_after_week_accept(client, tmp_spine_root):
    """The ticket-37 repro, in the order the app's own Help recommends:
    loregen stages t0001-t000N against an empty timeline, a week accept
    publishes t0001-t0002 first, and approving the pending entries used to
    refuse every one ('id t0001 already exists in lore/timeline.yaml').
    The paid-for entries must land, renumbered to the next free t-numbers."""
    d = tmp_spine_root / "testvale"
    # stage time: the timeline was empty when loregen minted these ids
    (d / "lore" / "timeline.yaml").unlink()
    staged = {"entries": [
        {"id": f"t{i:04d}", "when": "before week 1", "title": f"Backstory {i}",
         "text": f"Authored backstory entry {i}.", "source": "authored"}
        for i in range(1, 4)],
        "created": "2026-10-04T00:00:00+00:00", "focus": "the founding"}
    path = _write_pending(tmp_spine_root, "lore/pending-entries-9.yaml", staged)
    # the week accept lands first: t0001-t0002 with different content
    (d / "lore" / "timeline.yaml").write_text(yaml.safe_dump(
        {"entries": [
            {"id": "t0001", "when": "week 1", "title": "Week One Event",
             "text": "The week's chronicle event.", "source": "sim:week-1"},
            {"id": "t0002", "when": "week 1", "title": "Week One Second",
             "text": "Another chronicle event.", "source": "sim:week-1"},
        ]}, sort_keys=False), encoding="utf-8")

    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-9.yaml", "all": True})
    assert r.status_code == 200, r.json()
    assert _core(r) == {"approved": 3, "entries": 3, "pages": 0,
                        "pages_skipped": 0, "stubs": 0}
    entries = _timeline_entries(tmp_spine_root)
    assert [e["id"] for e in entries] == [
        "t0001", "t0002", "t0003", "t0004", "t0005"]
    assert [e["title"] for e in entries[2:]] == [
        "Backstory 1", "Backstory 2", "Backstory 3"]
    assert all(e["source"] == "authored" for e in entries[2:])
    assert not path.exists()


def test_approve_lands_exact_canon_repeat(client, tmp_spine_root):
    """Ticket 40's decision: content already in the timeline is NOT
    refused. The old crash-between-writes guard misfired on legitimate
    repeats and its window is owned by publication transactions +
    receipts now — a pending entry identical to a canon entry lands
    (as a new t-number), exactly what the DM approved from the queue."""
    path = _write_pending(tmp_spine_root, "lore/pending-entries-1.yaml", {
        "entries": [
            {"id": "t0002", "when": "year 212", "title": "The Toll Bridge",
             "text": "Provis levied a toll on the only bridge across the "
                     "Vane.",
             "source": "authored"}],
        "created": "2026-09-22T00:00:00+00:00", "focus": "",
    })
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-1.yaml", "all": True})
    assert r.status_code == 200, r.json()
    entries = _timeline_entries(tmp_spine_root)
    assert [e["id"] for e in entries] == ["t0001", "t0002", "t0003"]
    assert entries[2]["title"] == "The Toll Bridge"  # the repeat, renumbered
    assert not path.exists()


def test_approve_refuses_in_batch_duplicate(client, tmp_spine_root):
    """What TimelineConflict means since ticket 40: the BATCH carries two
    identical entries — ambiguous input (which one is the real event?),
    refused with nothing written."""
    path = _write_pending(tmp_spine_root, "lore/pending-entries-1.yaml", {
        "entries": [
            {"id": "p1-1", "when": "year 213", "title": "The Riot",
             "text": "The docks rioted over the salt tax.", "source":
             "authored"},
            {"id": "p1-2", "when": "year 213", "title": "The Riot",
             "text": "The docks rioted over the salt tax.", "source":
             "authored"},
        ],
        "created": "2026-09-22T00:00:00+00:00", "focus": "",
    })
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-1.yaml", "all": True})
    assert r.status_code == 409
    assert "exact same text" in r.json()["error"]
    assert path.exists(), "refused: the pending file is untouched"
    assert len(_timeline_entries(tmp_spine_root)) == 2


def test_partial_approve_keeps_the_rest_approvable(client, tmp_spine_root):
    """The regression: approving ONE entry of a multi-entry draft rewrote
    timeline.yaml (and stubbed pages into pinned lore/entities), but the
    remaining draft kept its original provenance — approving the next
    entry 409'd, even after a refresh, against a base revision this very
    approval had moved. The remaining entries' provenance must carry
    forward across the approval's own writes."""
    d = tmp_spine_root / "testvale"
    doc = {
        "entries": [
            {"id": "p1-1", "when": "week 1", "title": "First",
             "text": "First authored entry.", "source": "authored",
             "new_names": ["Nocksbury"]},
            {"id": "p1-2", "when": "week 1", "title": "Second",
             "text": "Second authored entry.", "source": "authored"},
        ],
        "created": "2026-09-26T00:00:00+00:00", "focus": "",
        # the provenance loregen writes: sources pinned by revision, fed
        # directories pinned by membership fingerprint
        "base_revisions": {
            "lore/timeline.yaml":
                publication.file_revision(d / "lore" / "timeline.yaml"),
        },
        "input_fingerprints": {
            "lore/entities":
                publication.fingerprint_dir(d / "lore" / "entities"),
        },
    }
    path = _write_pending(tmp_spine_root, "lore/pending-entries-5.yaml", doc)
    rel = "lore/pending-entries-5.yaml"

    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "ids": ["p1-1"]})
    assert r.status_code == 200, r.json()
    assert r.json()["stubs"] == 1, "the new name was stubbed into the vault"
    assert path.exists(), "the remaining entry stays pending"

    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "all": True})
    assert r.status_code == 200, \
        f"the second approve must not 409 on its own approval's changes: {r.json()}"
    titles = [e["title"] for e in _timeline_entries(tmp_spine_root)][-2:]
    assert titles == ["First", "Second"], "both entries landed, in order"
    assert not path.exists()


def test_approve_refuses_case_colliding_page_drafts(client, tmp_spine_root):
    """Ticket 28 (P3-27): entity filenames keep display case, so two page
    drafts titled 'Ashen Order' and 'ashen order' plan two paths that
    differ only by letter case. On a case-insensitive filesystem both
    writes land on ONE file (the second replacing the first) while the
    response counts pages: 2 — one draft silently lost. The batch must
    refuse with a named conflict, writing nothing."""
    path = _write_pending(tmp_spine_root, "lore/pending-pages-1.yaml", {
        "entries": [
            {"id": "page-1", "when": "week 9", "title": "Ashen Order",
             "text": "The first draft's body.", "source": "authored",
             "page": {"kind": "faction", "aliases": []}},
            {"id": "page-2", "when": "week 9", "title": "ashen order",
             "text": "The second draft's body.", "source": "authored",
             "page": {"kind": "faction", "aliases": []}},
        ],
        "created": "2026-10-02T00:00:00+00:00", "mode": "missing-pages",
    })
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-pages-1.yaml", "all": True})
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert "conflict" in err.lower()
    assert "Ashen Order.md" in err and "ashen order.md" in err
    # nothing was written: no entity page, no timeline append, both drafts
    # stay pending for the DM to disambiguate
    ents = tmp_spine_root / "testvale" / "lore" / "entities"
    assert not [p for p in ents.glob("*shen*")]
    assert len(_timeline_entries(tmp_spine_root)) == 2
    kept = yaml.safe_load(path.read_text(encoding="utf-8"))["entries"]
    assert [e["id"] for e in kept] == ["page-1", "page-2"]


def test_approve_refuses_case_colliding_stub_and_page_draft(
        client, tmp_spine_root):
    """The same guard covers stub pages (ticket 28): an entry stubbing the
    new name 'ASHEN ORDER' while a page draft in the same batch writes
    'Ashen Order' would double-write one file on a case-insensitive
    filesystem — refused like the draft/draft collision, nothing written."""
    path = _write_pending(tmp_spine_root, "lore/pending-pages-2.yaml", {
        "entries": [
            {"id": "page-1", "when": "week 9", "title": "Ashen Order",
             "text": "The draft's body.", "source": "authored",
             "page": {"kind": "faction", "aliases": []}},
            {"id": "t0009", "when": "week 9", "title": "A petition is heard",
             "text": "An entry that names the order.", "source": "authored",
             "new_names": ["ASHEN ORDER"]},
        ],
        "created": "2026-10-02T00:00:00+00:00", "mode": "missing-pages",
    })
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-pages-2.yaml", "all": True})
    assert r.status_code == 409, r.text
    assert "conflict" in r.json()["error"].lower()
    ents = tmp_spine_root / "testvale" / "lore" / "entities"
    assert not [p for p in ents.glob("*shen*")]
    assert len(_timeline_entries(tmp_spine_root)) == 2
    assert path.exists()  # refused: the pending file is untouched


def test_delete_removes_entries_and_empty_file(client, tmp_spine_root):
    path = _write_pending(tmp_spine_root, "lore/pending-entries-9.yaml", {
        "entries": [
            {"id": "t0007", "when": "w", "title": "Keep", "text": "keep",
             "source": "authored"},
            {"id": "t0008", "when": "w", "title": "Drop", "text": "drop",
             "source": "authored"},
        ],
        "created": "2026-09-22T00:00:00+00:00", "focus": "",
    })
    r = client.post("/api/campaign/testvale/review/delete",
                    json={"file": "lore/pending-entries-9.yaml",
                          "ids": ["t0008"]})
    assert r.status_code == 200 and _core(r) == {"deleted": 1}
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [e["id"] for e in doc["entries"]] == ["t0007"]
    r = client.post("/api/campaign/testvale/review/delete",
                    json={"file": "lore/pending-entries-9.yaml", "all": True})
    assert r.status_code == 200 and _core(r) == {"deleted": 1}
    assert not path.exists()
    assert len(_timeline_entries(tmp_spine_root)) == 2  # timeline untouched

    r = client.post("/api/campaign/testvale/review/delete",
                    json={"file": "lore/pending-entries-9.yaml", "all": True})
    assert r.status_code == 404  # the file is gone now


def test_traversal_and_non_pending_refused(client, tmp_spine_root):
    for bad in ("../testvale/lore/pending-entries-1.yaml",
                "lore/../../evil.yaml",
                "lore/timeline.yaml",           # real file, not a pending file
                "C:/Windows/win.ini",           # absolute — outside the campaign
                "simulation/pending-week-7.yaml"):  # does not exist here
        r = client.post("/api/campaign/testvale/review/approve",
                        json={"file": bad, "all": True})
        assert 400 <= r.status_code < 500, bad
        assert "error" in r.json(), bad


def test_review_empty_queue(client, tmp_spine_root):
    r = client.get("/api/campaign/testvale/review")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_unknown_campaign_404(client, spine_root):
    r = client.get("/api/campaign/nosuch/review")
    assert r.status_code == 404 and "error" in r.json()


def test_approve_retry_after_crash_between_publish_and_receipt(
        client, tmp_spine_root, monkeypatch):
    """The crash window: the approval PUBLISHED, then record_receipt died.
    The retry's receipt lookup misses and the pending file is already gone
    — the answer must be reconstructed from the transaction recorded under
    the same idempotency key (mirroring the accept route), never a 404 with
    the work landed but unacknowledged."""
    _write_pending(tmp_spine_root, "lore/pending-entries-4.yaml",
                   LORE_PENDING)
    rel = "lore/pending-entries-4.yaml"
    revision = publication.file_revision(
        tmp_spine_root / "testvale" / rel)
    body = {"file": rel, "all": True, "expected_revision": revision}

    # record_receipt sits OUTSIDE the route's try: the injected crash
    # propagates (that IS the crash-window scenario) with the publish landed
    with mock.patch.object(publication, "record_receipt",
                           side_effect=RuntimeError("injected crash")):
        with pytest.raises(RuntimeError, match="injected crash"):
            client.post("/api/campaign/testvale/review/approve", json=body)
    assert not (tmp_spine_root / "testvale" / rel).exists()
    assert any(e["id"] == "t0003" for e in _timeline_entries(tmp_spine_root)), \
        "the approval actually landed"

    r = client.post("/api/campaign/testvale/review/approve", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["idempotent"] is True
    assert r.json()["reconstructed"] is True
    assert r.json()["approved"] == 1
    assert r.json()["transaction"], "the original transaction is cited"
    # a third retry is served from the recorded receipt: the same answer,
    # still idempotent, nothing re-applied
    r2 = client.post("/api/campaign/testvale/review/approve", json=body)
    assert r2.status_code == 200 and r2.json() == r.json()
    assert len(_timeline_entries(tmp_spine_root)) == 3,         "t0003 landed exactly once across all three calls"


# ------------------------------------------------- stale-draft advice (2026-10-03)

def _stale_fingerprint_draft(tmp_spine_root, rel):
    """A generated draft whose pinned lore/entities fingerprint went stale:
    a page landed in the directory AFTER the draft was generated (the map
    companion-note case). Returns the pending file path."""
    d = tmp_spine_root / "testvale"
    doc = {
        "entries": [dict(LORE_PENDING["entries"][0], id="st1")],
        "created": "2026-09-22T00:00:00+00:00", "focus": "",
        "input_fingerprints": {
            "lore/entities": publication.fingerprint_dir(
                d / "lore" / "entities")},
    }
    path = _write_pending(tmp_spine_root, rel, doc)
    # the canon under the draft moves AFTER generation
    (d / "lore" / "entities" / "The New Arrival.md").write_text(
        "# The New Arrival\n\nLinked in after the draft was cut.\n",
        encoding="utf-8")
    return path


def test_stale_fingerprint_approve_advises_delete_and_regenerate(
        client, tmp_spine_root):
    """The stale-draft 409 used to say "refresh the queue, re-read, submit
    again" — but a GENERATED draft pins its inputs, so once the canon under
    it moved, resubmitting failed identically forever (several confused
    clicks, 2026-10-03). The approve 409 must name the only real fix."""
    path = _stale_fingerprint_draft(tmp_spine_root,
                                    "lore/pending-entries-9.yaml")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-9.yaml", "all": True})
    assert r.status_code == 409, r.json()
    msg = r.json()["error"]
    assert "membership changed" in msg, "the underlying cause stays named"
    assert "delete the draft" in msg, "the only real fix is named"
    assert "generate a fresh one" in msg
    assert "submit again" not in msg, "the wrong advice is gone"
    assert path.exists(), "nothing was written or removed"


def test_stale_viewed_draft_delete_advises_refresh(client, tmp_spine_root):
    """The delete path never pins the draft's generation inputs — its
    stale 409 means the pending FILE changed since the DM viewed it — so
    there the refresh-and-retry advice is the honest one (the approve
    path's regenerate advice would be wrong here)."""
    doc = dict(LORE_PENDING, entries=[
        dict(LORE_PENDING["entries"][0], id="st1")])
    path = _write_pending(tmp_spine_root, "lore/pending-entries-8.yaml", doc)
    viewed = publication.file_revision(path)
    # the pending file changes after the DM viewed it
    path.write_text(yaml.safe_dump(dict(
        doc, entries=[dict(doc["entries"][0], text="edited while open")]),
        sort_keys=False, allow_unicode=True), encoding="utf-8")

    r = client.post("/api/campaign/testvale/review/delete",
                    json={"file": "lore/pending-entries-8.yaml", "all": True,
                          "expected_revision": viewed})
    assert r.status_code == 409, r.json()
    msg = r.json()["error"]
    assert "refresh the review queue, re-read the draft, and delete" in msg
    assert path.exists(), "nothing was written or removed"
