"""Campaign discovery, summary shape, geography, 404 mapping."""

import canon
import spine


def test_campaigns_list_contains_fixture(client, spine_root):
    r = client.get("/api/campaigns")
    assert r.status_code == 200
    campaigns = r.json()["campaigns"]
    names = [c["name"] for c in campaigns]
    assert "testvale" in names
    tv = next(c for c in campaigns if c["name"] == "testvale")
    assert tv["title"] == "The Test Vale"
    assert tv["created"] == "2026-09-22"
    assert tv["has"] == {"world": True, "lore": True,
                         "simulation": True, "maps": True}


def test_campaign_summary_shape(client, spine_root):
    r = client.get("/api/campaign/testvale")
    assert r.status_code == 200
    s = r.json()
    assert s["world"]["nations"] == 1
    burgs = s["world"]["burgs"]
    assert [b["name"] for b in burgs] == ["Provis", "Dunmore"]
    assert burgs[0]["port"] is True
    assert burgs[0]["population"] == 12.3
    assert s["lore"]["timeline_entries"] == 2
    assert s["lore"]["entities"] == 4  # entity pages: 2 people, 1 faction, 1 place
    assert "voice.md" in s["lore"]["style_files"]
    assert s["simulation"]["clock"] == {"week": 3, "season": "early spring"}
    assert [f["id"] for f in s["simulation"]["factions"]] == ["wardens", "ferrymen"]
    assert s["simulation"]["rumors"] == 1
    assert s["maps"]["cities"] == ["provis"]
    assert s["maps"]["sites"] == []


def test_geography_endpoint(client, spine_root):
    r = client.get("/api/campaign/testvale/geography")
    assert r.status_code == 200
    geo = r.json()
    assert geo["meta"]["seed"] == "12345"
    assert len(geo["major_settlements"]) == 2


def test_unknown_campaign_is_404_with_error_body(client, spine_root):
    for path in ("/api/campaign/nowhere",
                 "/api/campaign/nowhere/geography",
                 "/api/campaign/nowhere/maps",
                 "/api/campaign/nowhere/simulation",
                 "/api/campaign/nowhere/lore"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert "error" in r.json(), path
    # path tricks must not escape into the tree either
    assert client.get("/api/campaign/..%2Fetc").status_code == 404


def test_odd_sibling_folder_does_not_break_the_list(client, tmp_spine_root):
    """A hand-made campaigns/My Campaign (space in the name, no geography —
    the README invites zip-and-edit) must be skipped, not 500 the list.
    Found live 2026-09-23: every GET /api/campaigns returned
    'internal error: CampaignNotFound' and the whole app showed
    'Unavailable' until the folder was renamed."""
    bad = tmp_spine_root / "My Campaign"
    bad.mkdir()
    (bad / "campaign.yaml").write_text(
        "name: My Campaign\ntitle: A hand-made folder\n", encoding="utf-8")
    assert spine.skipped_campaign_folders() == ["My Campaign"]

    r = client.get("/api/campaigns")
    assert r.status_code == 200, r.text
    names = [c["name"] for c in r.json()["campaigns"]]
    assert "My Campaign" not in names
    assert "testvale" in names, "the healthy sibling must survive"


def test_hand_edited_campaign_name_falls_back_to_addressable_slug(
        client, tmp_spine_root):
    """campaign.yaml edited to a name the routes can't address must not
    poison the dropdown: the identity falls back to the folder name."""
    y = tmp_spine_root / "testvale" / "campaign.yaml"
    y.write_text("name: My Test Vale\ntitle: The Test Vale\n", encoding="utf-8")
    r = client.get("/api/campaigns")
    assert r.status_code == 200
    tv = next(c for c in r.json()["campaigns"] if c["title"] == "The Test Vale")
    assert tv["name"] == "testvale", "identity stays addressable"
    assert client.get("/api/campaign/testvale").status_code == 200


def test_alias_request_takes_the_canonical_identity(client, tmp_spine_root):
    """Ticket 56: on NTFS 'TestVale' / 'testvale.' address the same
    directory as 'testvale', but the lock file name keyed on the
    REQUESTED spelling — a write under the alias held testvale..lock
    while every writer on testvale held testvale.lock: same canon,
    different locks, mutual exclusion silently bypassed. The boundary now
    resolves the request against the campaigns root, so files, locks, and
    snapshots all flow under the directory's real name."""
    # the aliases resolve to the SAME directory as the canonical name
    assert spine.campaign_dir("TestVale") == tmp_spine_root / "testvale"
    assert spine.campaign_dir("testvale.") == tmp_spine_root / "testvale"
    # ... and take the CANONICAL lock, not a minted sibling beside it
    assert spine.campaign_lock("TestVale") is spine.campaign_lock("testvale")
    assert spine.campaign_lock("testvale.")._lock_file().name \
        == "testvale.lock"
    # a name no directory answers stays itself — the 404 path decides it
    assert spine.canonical_name("nowhere") == "nowhere"
    assert client.get("/api/campaign/nowhere").status_code == 404
    # the API serves requests under either alias through the canonical flow
    assert client.get("/api/campaign/TestVale").status_code == 200
    assert client.get("/api/campaign/testvale.").status_code == 200
    # the ticket's write probe: while the canonical lock is held (a week
    # job), an alias-addressed interactive write COLLIDES instead of
    # slipping past on a minted testvale..lock — and once the lock is
    # free, the alias write lands in the REAL directory
    lock = spine.campaign_lock("testvale")
    assert lock.acquire(blocking=False)
    try:
        assert client.put("/api/campaign/TestVale/lore/style/voice.md",
                          json={"text": "alias"}).status_code == 409
        assert client.put("/api/campaign/testvale./lore/style/voice.md",
                          json={"text": "alias"}).status_code == 409
    finally:
        lock.release()
    r = client.put("/api/campaign/TestVale/lore/style/voice.md",
                   json={"text": "alias"})
    assert r.status_code == 200, r.text
    assert (tmp_spine_root / "testvale" / "lore" / "style" / "voice.md") \
        .read_text(encoding="utf-8") == "alias"


def test_listed_name_stays_the_directory_and_alias_snapshots_commit(
        client, tmp_spine_root, monkeypatch):
    """Ticket 56: a hand-edited campaign.yaml `name:` that is merely VALID
    (case-variant 'TestVale' for folder 'testvale') used to become the
    app's address for the campaign — `git add -A -- TestVale` silently
    stages nothing and `git commit --only` fails pathspec, killing the
    undo trail from a one-line hand edit, reported only as snapshot
    'failed'. The listing now always advertises the directory name, and
    the snapshot path canonicalizes the same way."""
    monkeypatch.setattr(canon, "AVAILABLE", True)
    y = tmp_spine_root / "testvale" / "campaign.yaml"
    y.write_text("name: TestVale\ntitle: The Test Vale\n", encoding="utf-8")
    r = client.get("/api/campaigns")
    assert r.status_code == 200, r.text
    names = [c["name"] for c in r.json()["campaigns"]]
    assert "testvale" in names
    assert "TestVale" not in names, \
        "a valid-but-wrong name never becomes the app's address"
    # a snapshot that arrives under the hand-edited alias commits the
    # REAL directory: the git pathspec is the canonical one
    snap = canon.snapshot_result("TestVale", "campaign/testvale: alias probe")
    assert snap["state"] == "committed", snap
    # and the canonical name keeps snapshotting (first commit landed above)
    (tmp_spine_root / "testvale" / "lore" / "timeline.yaml").write_text(
        "entries: []\n", encoding="utf-8")
    snap = canon.snapshot_result("testvale", "campaign/testvale: canonical")
    assert snap["state"] == "committed", snap
