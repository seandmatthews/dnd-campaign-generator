"""Campaign discovery, summary shape, geography, 404 mapping."""


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
    assert s["lore"]["entities"] == 3  # top-level keys: people, factions, places
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
