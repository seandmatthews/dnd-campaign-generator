"""POST /maps/world-map — the render-only paths from an EXISTING source.

A JSON dump renders the schematic atlas; a .map save yields FMG's embedded
art; no source at all is 422 (fed-or-refused). No engine is ever invoked:
this route is pure reads of the genesis record by studio/worldmap.py.
"""

import json

from conftest import tiny_fmg_dump, wait_for_job


def test_world_map_422_without_source(client, spine_root):
    # testvale has geography but no world/source.* genesis record
    r = client.post("/api/campaign/testvale/maps/world-map")
    assert r.status_code == 422
    assert "world/source.*" in r.json()["error"]
    assert "fed-or-refused" in r.json()["error"]


def test_world_map_renders_schematic_from_json_dump(client, tmp_spine_root):
    camp = tmp_spine_root / "testvale" / "world"
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "source.json").write_text(
        json.dumps(tiny_fmg_dump("42")), encoding="utf-8")

    r = client.post("/api/campaign/testvale/maps/world-map")
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["kind"] == "world-map"
    assert job["result"]["mode"] == "schematic-render"
    assert job["result"]["svg"] == "/campaign-file/testvale/world/map.svg"

    svg = (camp / "map.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert "#aa4040" in svg            # the dump's own state color
    assert 'cx="50.0"' in svg          # the dump's own burg
    # geography untouched by the render
    assert (camp / "geography.yaml").is_file()
    # (serving via /campaign-file is covered by test_files.py and the live
    # sample-campaign flow — the mount binds the real campaigns root at boot)


def test_world_map_extracts_embedded_svg_from_map_save(client, tmp_spine_root):
    camp = tmp_spine_root / "testvale" / "world"
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "source.map").write_text(
        "v1.153.1|3fa|548|878636343|false|878636343|map-01\n"
        '{"seed":"878636343","graph":{"width":100,"height":80}}\n'
        "nameBases...\n"
        '<svg id="map" width="100" height="80" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M0 0L100 80Z" fill="#5b8ea6"/></svg>\n'
        '["positional section"]\n',
        encoding="utf-8")

    r = client.post("/api/campaign/testvale/maps/world-map")
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["result"]["mode"] == "fmg-embedded"
    svg = (camp / "map.svg").read_text(encoding="utf-8")
    assert svg.startswith('<svg id="map"')
    assert svg.rstrip().endswith("</svg>")
    assert "nameBases" not in svg  # the save's other sections stay out


def test_world_map_unrenderable_dump_fails_the_job_honestly(client,
                                                            tmp_spine_root):
    camp = tmp_spine_root / "testvale" / "world"
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "source.json").write_text("{}", encoding="utf-8")

    r = client.post("/api/campaign/testvale/maps/world-map")
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "unsupported dump" in job["error"]
    assert "cells" in job["error"]
