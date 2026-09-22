"""POST /api/campaigns — creating a campaign from nothing (the flow-A door).

The spine's create_campaign is the one writer that starts from nothing;
everything else fills sections of an existing campaign. These tests pin the
route semantics: 201 + spine files on disk, 409 on duplicates, 422 on names
that would make bad directories/URLs.
"""

import yaml


def test_create_writes_spine_and_lists(tmp_spine_root, client):
    r = client.post("/api/campaigns",
                    json={"name": "ember-coast", "title": "Ember Coast"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "ember-coast"
    assert body["title"] == "Ember Coast"

    camp_yaml = tmp_spine_root / "ember-coast" / "campaign.yaml"
    assert camp_yaml.is_file()
    meta = yaml.safe_load(camp_yaml.read_text(encoding="utf-8"))
    assert meta["name"] == "ember-coast"
    assert meta["title"] == "Ember Coast"
    assert meta["created"]  # ISO date string

    # the section dirs exist so later generation never meets a missing parent
    for sub in ("world", "lore", "lore/style", "simulation",
                "simulation/chronicle", "maps/cities", "maps/sites"):
        assert (tmp_spine_root / "ember-coast" / sub).is_dir(), sub

    listed = client.get("/api/campaigns").json()["campaigns"]
    assert any(c["name"] == "ember-coast" and c["title"] == "Ember Coast"
               for c in listed)


def test_summary_and_reads_work_on_fresh_campaign(tmp_spine_root, client):
    client.post("/api/campaigns", json={"name": "fresh-one"})
    summary = client.get("/api/campaign/fresh-one")
    assert summary.status_code == 200
    body = summary.json()
    assert body["name"] == "fresh-one"
    # an empty campaign has nothing in any section — reads stay 200/404-clean
    assert client.get("/api/campaign/fresh-one/lore").status_code == 200
    assert client.get("/api/campaign/fresh-one/review").json()["items"] == []
    assert client.get(
        "/api/campaign/fresh-one/simulation").status_code == 404


def test_duplicate_is_409(tmp_spine_root, client):
    assert client.post("/api/campaigns",
                       json={"name": "ember-coast"}).status_code == 201
    r = client.post("/api/campaigns", json={"name": "ember-coast"})
    assert r.status_code == 409
    assert "already exists" in r.json()["error"]


def test_bad_names_are_422(tmp_spine_root, client):
    for bad in ["My World", "", "  ", "-lead", "trailing-", "UPPER",
                "dot.name", "a" * 50, "slash/slash"]:
        r = client.post("/api/campaigns", json={"name": bad})
        assert r.status_code == 422, (bad, r.status_code, r.text)
        assert "error" in r.json()
    # nothing was created by any of those
    listed = client.get("/api/campaigns").json()["campaigns"]
    assert all(c["name"] not in ("my-world", "upper", "dot.name")
               for c in listed)


def test_title_defaults_to_name(tmp_spine_root, client):
    r = client.post("/api/campaigns", json={"name": "solo"})
    assert r.status_code == 201
    assert r.json()["title"] == "solo"
