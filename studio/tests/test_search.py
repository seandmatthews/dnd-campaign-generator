"""Campaign search (H): the route over the fixture campaign — hits, kinds,
heading context, the cap, and the empty-q refusal. Read-only fixtures except
the pending-kinds test, which adds two files to a writable copy."""

from routers import system


def _get(client, q=None, campaign="testvale"):
    params = {} if q is None else {"q": q}
    return client.get(f"/api/campaign/{campaign}/search", params=params)


def test_search_hits_chronicle_with_heading_context(client, spine_root):
    r = _get(client, "ash")
    assert r.status_code == 200
    body = r.json()
    assert body["q"] == "ash"
    chron = [h for h in body["results"]
             if h["file"] == "simulation/chronicle/0003-the-ash-crossing.md"]
    assert chron, "the chronicle must be searched"
    assert all(h["kind"] == "chronicle" for h in chron)
    # line 1 is the heading itself; line 3 is the prose under it
    by_line = {h["line"]: h for h in chron}
    assert 1 in by_line and 3 in by_line
    assert by_line[1]["context"] == ""  # a heading has no heading above it
    assert by_line[3]["context"] == "# Week 3 — The Ash Crossing"
    assert "ash" in by_line[3]["text"].lower()


def test_search_is_case_insensitive(client, spine_root):
    lower = _get(client, "toll").json()
    upper = _get(client, "TOLL").json()
    assert lower["results"] and len(lower["results"]) == len(upper["results"])


def test_search_kinds_cover_the_campaign(client, spine_root):
    body = _get(client, "provis").json()
    kinds = {h["kind"] for h in body["results"]}
    assert {"entities", "style", "geography"} <= kinds
    geo = [h for h in body["results"] if h["kind"] == "geography"]
    assert geo and geo[0]["file"] == "world/geography.yaml"


def test_search_timeline_and_campaign_kinds(client, spine_root):
    levied = _get(client, "levied").json()["results"]
    assert levied and levied[0]["kind"] == "timeline"
    assert levied[0]["file"] == "lore/timeline.yaml"
    assert levied[0]["line"] > 0

    named = _get(client, "testvale").json()["results"]
    assert any(h["kind"] == "campaign" and h["file"] == "campaign.yaml"
               for h in named)


def test_search_result_shape(client, spine_root):
    for h in _get(client, "toll").json()["results"]:
        assert {"file", "kind", "line", "text", "context"} == set(h)
        assert h["kind"] in {"chronicle", "timeline", "entities", "style",
                             "geography", "pending", "campaign"}


def test_search_cap(client, spine_root, monkeypatch):
    monkeypatch.setattr(system, "SEARCH_CAP", 3)
    body = _get(client, "toll").json()
    assert len(body["results"]) == 3  # the fixture has more hits than that


def test_search_empty_q_is_422(client, spine_root):
    assert _get(client, "").status_code == 422
    assert _get(client, "   ").status_code == 422
    assert _get(client).status_code == 422  # q missing entirely


def test_search_unknown_campaign_404(client, spine_root):
    assert _get(client, "toll", campaign="nope").status_code == 404


def test_search_no_hits_is_an_empty_list_not_an_error(client, spine_root):
    body = _get(client, "zzz-no-such-thing-zzz").json()
    assert body == {"q": "zzz-no-such-thing-zzz", "results": []}


def test_search_strips_the_query_but_keeps_its_case(client, spine_root):
    body = _get(client, "  Toll ").json()
    assert body["q"] == "Toll"


def test_search_finds_pending_files(tmp_spine_root, client):
    (tmp_spine_root / "testvale" / "lore" / "pending-entries-1.yaml").write_text(
        "entries:\n  - id: pending-1\n    text: a zephyrboat for the vale\n",
        encoding="utf-8")
    (tmp_spine_root / "testvale" / "simulation" / "pending-week-3.yaml").write_text(
        "entries:\n  - id: pw3-1\n    text: the zephyrboat sinks, insurers sigh\n",
        encoding="utf-8")
    body = _get(client, "zephyrboat").json()
    files = {h["file"]: h for h in body["results"]}
    assert set(files) == {"lore/pending-entries-1.yaml",
                          "simulation/pending-week-3.yaml"}
    assert all(h["kind"] == "pending" for h in files.values())
