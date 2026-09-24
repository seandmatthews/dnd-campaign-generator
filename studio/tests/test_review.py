"""Review queue (M4): the union GET, approve into the timeline, delete,
pending-file traversal guards. check.py is real here (structural layer is
zero-dep); the one findings-attachment test fakes it for determinism."""

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
    snapshot status) — intentional contract change, asserted separately."""
    d = dict(r.json())
    d.pop("transaction", None)
    d.pop("snapshot", None)
    d.pop("noop", None)
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


def test_approve_keeps_t_ids_and_refuses_duplicates(client, tmp_spine_root):
    path = _write_pending(tmp_spine_root, "lore/pending-entries-1.yaml",
                          LORE_PENDING)
    dup = dict(LORE_PENDING)
    dup["entries"] = [dict(LORE_PENDING["entries"][0], id="t0002")]
    path.write_text(yaml.safe_dump(dup, sort_keys=False), encoding="utf-8")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-1.yaml", "all": True})
    assert r.status_code == 409 and "t0002" in r.json()["error"]
    assert path.exists()  # refused: the pending file is untouched

    dup["entries"] = [dict(LORE_PENDING["entries"][0], id="t0005")]
    path.write_text(yaml.safe_dump(dup, sort_keys=False), encoding="utf-8")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-1.yaml", "all": True})
    assert r.status_code == 200 and _core(r) == {
        "approved": 1, "entries": 1, "pages": 0, "pages_skipped": 0,
        "stubs": 0}
    entries = _timeline_entries(tmp_spine_root)
    assert [e["id"] for e in entries] == ["t0001", "t0002", "t0005"]
    assert entries[2]["source"] == "authored"  # source kept
    assert not path.exists()


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
