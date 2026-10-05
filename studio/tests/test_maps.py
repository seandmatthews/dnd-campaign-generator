"""Maps listing and the city job (with a fake gen-city engine). Site
generation lives in test_sites_job.py; world generation in test_world_gen.py."""

import json

import engines
from conftest import wait_for_job


def test_maps_listing(client, spine_root):
    r = client.get("/api/campaign/testvale/maps")
    assert r.status_code == 200
    m = r.json()
    assert len(m["cities"]) == 1
    city = m["cities"][0]
    assert city["slug"] == "provis"
    assert city["label"] == "Provis"  # from params.yaml
    assert city["svg"].endswith("/campaign-file/testvale/maps/cities/provis/provis.svg")
    assert city["geojson"] and city["params"]
    assert m["sites"] == []


def test_city_post_422_without_world_source(client, spine_root):
    r = client.post("/api/campaign/testvale/maps/cities", json={"burg": "Dunmore"})
    assert r.status_code == 422  # fed-or-refused: no world/source.* to derive from
    assert "error" in r.json()


def test_sites_post_422_bad_kind(client, spine_root):
    # the M1 stub 501 is gone: sites generate for real now, so an unknown
    # kind is a validation error, not a missing feature
    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "volcano", "name": "The Ash Cellars"})
    assert r.status_code == 422
    assert "dungeon|cave|camp" in r.json()["error"]


def test_city_job_runs_engine_and_copies_outputs(client, tmp_spine_root, monkeypatch):
    # give the writable campaign a world dump to derive from
    (tmp_spine_root / "testvale" / "world" / "source.map").write_text("fmg", encoding="utf-8")
    seen = {}

    def fake_run_node(engine_root, *args, timeout=600, on_line=None):
        seen["cwd"] = engine_root
        seen["args"] = args
        out = None
        for flag, value in zip(args, args[1:]):
            if flag == "--out":
                out = value
        assert out, "gen-city must receive --out"
        (engine_root / "scripts").mkdir(parents=True, exist_ok=True)
        with open(f"{out}/dunmore.svg", "w", encoding="utf-8") as f:
            f.write("<svg/>")
        with open(f"{out}/dunmore.geojson", "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
        with open(f"{out}/params.yaml", "w", encoding="utf-8") as f:
            f.write("name: Dunmore\nseed: 42\n")
        if on_line:
            on_line("wrote dunmore.svg")
        return 0, ["wrote dunmore.svg"]

    monkeypatch.setattr(engines, "run_node", fake_run_node)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")

    r = client.post("/api/campaign/testvale/maps/cities", json={"burg": "Dunmore"})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["kind"] == "map-city"
    assert seen["cwd"] == tmp_spine_root / "dnd-mapping"
    assert seen["args"][:2] == ("scripts/gen-city.mjs", "--world")
    assert "--burg" in seen["args"] and "Dunmore" in seen["args"]
    assert job["result"]["slug"] == "dunmore"
    assert job["result"]["svg"] == \
        "/campaign-file/testvale/maps/cities/dunmore/dunmore.svg"
    dest = tmp_spine_root / "testvale" / "maps" / "cities" / "dunmore"
    assert (dest / "dunmore.svg").is_file()
    assert (dest / "params.yaml").is_file()


def _two_stormbergs(tmp_spine_root):
    """Seed 6161's trap (ticket 41): one generated world, two burgs named
    Stormberg in different nations — only the top-15 picker entry and its
    dump id tell them apart. Also the geography the id resolution reads."""
    camp = tmp_spine_root / "testvale"
    (camp / "world").mkdir(parents=True, exist_ok=True)
    (camp / "world" / "source.map").write_text("fmg", encoding="utf-8")
    (camp / "world" / "geography.yaml").write_text(
        "meta:\n"
        "  source: test fixture\n"
        "  seed: '6161'\n"
        "major_settlements:\n"
        "- name: Stormberg\n"
        "  burg_id: 2\n"
        "  nation: Sildgeria\n"
        "  population_thousands: 27.4\n"
        "- name: Stormberg\n"
        "  burg_id: 266\n"
        "  nation: Rathgar\n"
        "  population_thousands: 9.1\n",
        encoding="utf-8")
    return camp


def test_city_job_accepts_burg_id_when_names_duplicate(
        client, tmp_spine_root, monkeypatch):
    """Mapping a burg whose NAME is ambiguous succeeds, targeting the
    picked nation's burg: the picker submits the dump id (ticket 41), the
    router passes it to gen-city verbatim, and everything derived (the
    place default, the companion-note provenance) resolves to the NAME."""
    camp = _two_stormbergs(tmp_spine_root)
    seen = {}

    def fake_run_node(engine_root, *args, timeout=600, on_line=None):
        seen["args"] = args
        out = args[args.index("--out") + 1]
        (engine_root / "scripts").mkdir(parents=True, exist_ok=True)
        with open(f"{out}/stormberg.svg", "w", encoding="utf-8") as f:
            f.write("<svg/>")
        if on_line:
            on_line("wrote stormberg.svg")
        return 0, ["wrote stormberg.svg"]

    monkeypatch.setattr(engines, "run_node", fake_run_node)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")

    r = client.post("/api/campaign/testvale/maps/cities",
                    json={"burg": "266"})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert seen["args"][seen["args"].index("--burg") + 1] == "266"

    # the derived place is the RESOLVED NAME, never the id
    assert (camp / "lore" / "entities" / "Stormberg.md").is_file()
    assert not (camp / "lore" / "entities" / "266.md").exists()
    note = (camp / "maps" / "cities" / "stormberg" / "stormberg.md") \
        .read_text(encoding="utf-8")
    assert "place: '[[Stormberg]]'" in note or "place: [[Stormberg]]" in note
    assert "burg: Stormberg" in note
    assert "266" not in note.split("---")[1]  # no id leaks into the frontmatter


def test_city_job_rejects_unresolvable_burg_id(client, tmp_spine_root):
    """An id that geography.yaml cannot resolve refuses with a pointer at
    the picker — it must not become a place page named after the digits."""
    _two_stormbergs(tmp_spine_root)
    r = client.post("/api/campaign/testvale/maps/cities",
                    json={"burg": "999"})
    assert r.status_code == 422
    assert "geography.yaml" in r.json()["error"]
