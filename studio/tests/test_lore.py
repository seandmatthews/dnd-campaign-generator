"""Lore reads, text-form writes, and the honest 501 generation stubs."""


def test_lore_get_shape(client, spine_root):
    r = client.get("/api/campaign/testvale/lore")
    assert r.status_code == 200
    lore = r.json()
    assert [e["id"] for e in lore["timeline"]] == ["t0001", "t0002"]
    assert lore["timeline"][0]["source"] == "authored"
    assert "Alweric Tealeaf" in str(lore["entities"]["people"])
    assert lore["style"]["voice.md"].startswith("Excerpts")
    assert "Alweric" in lore["style"]["names-shadian.txt"]


def test_lore_put_entities_roundtrip_and_validation(client, tmp_spine_root):
    url = "/api/campaign/testvale/lore/entities"
    new_yaml = "people:\n  - name: Corvin Dane\n    note: sells rope\n"
    r = client.put(url, json={"text": new_yaml})
    assert r.status_code == 200 and r.json() == {"ok": True}
    on_disk = (tmp_spine_root / "testvale" / "lore" / "entities.yaml").read_text(
        encoding="utf-8")
    assert "Corvin Dane" in on_disk
    assert "Corvin Dane" in str(
        client.get("/api/campaign/testvale/lore").json()["entities"])

    bad = client.put(url, json={"text": "people: [unclosed"})
    assert bad.status_code == 422
    scalar = client.put(url, json={"text": "just a string"})
    assert scalar.status_code == 422


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


def test_review_now_lives_in_test_review(client, spine_root):
    """M4 replaced the 501 review stubs — the queue's tests are in
    test_review.py; this only pins the route's existence."""
    r = client.get("/api/campaign/testvale/review")
    assert r.status_code == 200
