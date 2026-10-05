"""Lore reads, entity pages, and the fed-or-refused generation guards."""

import json


def test_lore_get_shape(client, spine_root):
    r = client.get("/api/campaign/testvale/lore")
    assert r.status_code == 200
    lore = r.json()
    assert [e["id"] for e in lore["timeline"]] == ["t0001", "t0002"]
    assert lore["timeline"][0]["source"] == "authored"
    names = {e["name"] for e in lore["entities"]}
    assert "Alweric Tealeaf" in names
    assert {e["file"] for e in lore["entities"]} == {
        "lore/entities/Alweric Tealeaf.md",
        "lore/entities/Besiana Crow.md",
        "lore/entities/The Bridge Wardens.md",
        "lore/entities/The Vane.md",
    }
    assert lore["style"]["voice.md"].startswith("Excerpts")
    assert "Alweric" in lore["style"]["names-shadian.txt"]


def test_lore_entities_put_is_gone(client, tmp_spine_root):
    """The raw entities.yaml textarea is retired with the file: pages are
    hand-edited in Obsidian (or migrated/stubbed), not form-written."""
    r = client.put("/api/campaign/testvale/lore/entities",
                   json={"text": "people: []"})
    assert r.status_code == 405  # route no longer exists


def test_entity_page_route_with_backlinks(client, spine_root):
    """The 'everything about this' view: one page + who links to it."""
    r = client.get("/api/campaign/testvale/lore/pages/The Vane")
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["name"] == "The Vane"
    assert page["kind"] == "places"
    assert "river" in page["note"]
    assert isinstance(page["backlinks"], list)

    r = client.get("/api/campaign/testvale/lore/pages/Nonesuch")
    assert r.status_code == 404
    r = client.get("/api/campaign/testvale/lore/pages/..%2F..%2Fcampaign.yaml")
    assert r.status_code in (404, 422)  # traversal never resolves


def test_lore_put_style(client, tmp_spine_root):
    url = "/api/campaign/testvale/lore/style/voice.md"
    r = client.put(url, json={"text": "New voice: shorter sentences."})
    assert r.status_code == 200 and r.json() == {"ok": True}
    on_disk = (tmp_spine_root / "testvale" / "lore" / "style" / "voice.md")
    assert on_disk.read_text(encoding="utf-8").startswith("New voice")

    # must already exist
    r = client.put("/api/campaign/testvale/lore/style/new-file.txt",
                   json={"text": "x"})
    assert r.status_code == 404
    # filename constrained to [A-Za-z0-9._-]
    r = client.put("/api/campaign/testvale/lore/style/bad*name.txt",
                   json={"text": "x"})
    assert r.status_code == 422


def test_lore_generation_is_fed_or_refused(client, tmp_spine_root):
    """M2 wired the endpoints (see test_loregen.py for the full flows);
    what stays tested here is guardrail 4: without ground truth the routes
    refuse — no fallback to free invention, and no LLM call."""
    (tmp_spine_root / "testvale" / "world" / "geography.yaml").unlink()
    r = client.post("/api/campaign/testvale/lore/entries",
                    json={"focus": "the Vane"})
    assert r.status_code == 422
    assert "error" in r.json()
    r = client.post("/api/campaign/testvale/lore/name",
                    json={"culture": "Nonsuch", "count": 5})
    assert r.status_code == 422
    assert "error" in r.json()


def test_name_provider_failure_is_a_503(client, tmp_spine_root, monkeypatch):
    """P3-14: a provider outage on the synchronous /lore/name call answers
    503 like its sibling LLM endpoints (simulation, maps) — llm_client's
    readable RuntimeError used to escape as a raw 500."""
    import llm_client

    def outage(*a, **kw):
        raise RuntimeError("HTTP 503: upstream unavailable")

    monkeypatch.setattr(llm_client, "complete", outage)
    # the fed-or-refused gate passes: shadian has its seed list in the
    # fixtures, so the call really reaches the (broken) provider
    r = client.post("/api/campaign/testvale/lore/name",
                    json={"culture": "shadian", "count": 3})
    assert r.status_code == 503, r.text
    assert "upstream unavailable" in r.json()["error"]


def test_review_now_lives_in_test_review(client, spine_root):
    """M4 replaced the 501 review stubs — the queue's tests are in
    test_review.py; this only pins the route's existence."""
    r = client.get("/api/campaign/testvale/review")
    assert r.status_code == 200


def test_lore_path_carries_obsidian_registration(client, spine_root,
                                                 tmp_path, monkeypatch):
    """GET /lore/path tells the authoring card whether the folder is
    already one of Obsidian's vaults (OBSIDIAN_CONFIG points the service
    at a throwaway config)."""
    monkeypatch.setenv("OBSIDIAN_CONFIG", str(tmp_path / "obsidian.json"))
    r = client.get("/api/campaign/testvale/lore/path")
    assert r.status_code == 200
    body = r.json()
    assert body["obsidian_uri"].startswith("obsidian://open?path=")
    assert body["obsidian_registered"] is False


def test_lore_obsidian_register_roundtrip(client, spine_root, tmp_path,
                                          monkeypatch):
    """POST /lore/obsidian registers the campaign's RESOLVED lore folder
    (no path argument — the server resolves it), is idempotent, and the
    GET flips to registered."""
    monkeypatch.setenv("OBSIDIAN_CONFIG", str(tmp_path / "obsidian.json"))
    path = client.get("/api/campaign/testvale/lore/path").json()["path"]

    r = client.post("/api/campaign/testvale/lore/obsidian")
    assert r.status_code == 201, r.text
    assert r.json()["registered"] is True
    assert r.json()["already"] is False

    again = client.post("/api/campaign/testvale/lore/obsidian")
    assert again.status_code == 201
    assert again.json()["already"] is True

    assert client.get("/api/campaign/testvale/lore/path") \
        .json()["obsidian_registered"] is True
    # the entry names the folder the GET resolved, byte for byte
    doc = json.loads((tmp_path / "obsidian.json").read_text(encoding="utf-8"))
    assert [v["path"] for v in doc["vaults"].values()] == [path]


def test_lore_obsidian_refuses_corrupt_config(client, spine_root, tmp_path,
                                              monkeypatch):
    """A corrupt obsidian.json is a 409 that says so — never a clobber."""
    bad = tmp_path / "obsidian.json"
    bad.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_CONFIG", str(bad))
    r = client.post("/api/campaign/testvale/lore/obsidian")
    assert r.status_code == 409
    assert "refusing" in r.json()["error"]
    assert bad.read_text(encoding="utf-8") == "{broken"
