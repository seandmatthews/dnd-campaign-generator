"""POST /maps/sites — the full battlemap chain with FAKE engines.

The fakes reproduce the documented CLI contract exactly (argv shapes and
the worlds/sites/<name> output location): gen-<kind> writes scene.json,
render writes scene.svg, render-scene-png.mjs writes scene.png (sharp),
wrap writes <slug>.uvtt. No real battlemap/sharp call ever runs.
"""

import json
import zlib
from pathlib import Path

import yaml

import engines
from conftest import wait_for_job

SITE = "Marsh Camp"  # deliberately un-slugified: engines get the human name


def fake_battlemap(engine_root, script, *args, timeout=600, on_line=None,
                   env=None):
    assert script == "src/battlemap.py"
    cmd, argv = args[0], list(args[1:])

    def arg(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    if cmd.startswith("gen-"):
        seed, name = arg("--seed"), arg("--name")
        site_dir = engine_root / "worlds" / "sites" / name
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "scene.json").write_text(json.dumps(
            {"name": name, "grids": {"x": 46, "y": 30},
             "pixels_per_grid": 70, "meters_per_grid": 1.524,
             "provenance": {"generator": f"battlemap.py {cmd} seed={seed}"},
             "features": [], "doors": []}), encoding="utf-8")
        return 0, [f"camp '{name}': 7 tents -> {site_dir}"]
    if cmd == "render":
        site_dir = engine_root / Path(arg("--dir"))
        (site_dir / "scene.svg").write_text("<svg>scene</svg>",
                                            encoding="utf-8")
        return 0, ["rendered scene.svg"]
    if cmd == "wrap":
        site_dir = engine_root / Path(arg("--dir"))
        scene = json.loads((site_dir / "scene.json").read_text(
            encoding="utf-8"))
        slug = "".join(c if c.isalnum() else "-" for c in scene["name"].lower())
        slug = "-".join(tok for tok in slug.split("-") if tok)
        (site_dir / f"{slug}.uvtt").write_text('{"format":0.3}',
                                               encoding="utf-8")
        return 0, [f"wrote {slug}.uvtt"]
    raise AssertionError(f"unexpected battlemap cmd: {cmd}")


def fake_png_ok(engine_root, *args, timeout=600, on_line=None, env=None):
    assert args[0] == "scripts/render-scene-png.mjs"
    (engine_root / Path(args[1]) / "scene.png").write_bytes(b"\x89PNG")
    return 0, [f"scene.png: 3220x2100 -> {args[1]}"]


def install(tmp_spine_root, monkeypatch, png=fake_png_ok):
    monkeypatch.setattr(engines, "run_py", fake_battlemap)
    monkeypatch.setattr(engines, "run_node", png)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")


def test_sites_job_happy_path(client, tmp_spine_root, monkeypatch):
    install(tmp_spine_root, monkeypatch)
    calls = []

    def recording_py(engine_root, script, *args, **kwargs):
        calls.append((script,) + args)
        return fake_battlemap(engine_root, script, *args, **kwargs)

    monkeypatch.setattr(engines, "run_py", recording_py)

    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "camp", "name": SITE})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["kind"] == "map-site"

    # the documented chain, in order, with the human name (list-form argv)
    assert calls[0] == ("src/battlemap.py", "gen-camp", "--seed",
                        calls[0][3], "--name", SITE)
    assert calls[1] == ("src/battlemap.py", "render", "--dir",
                        f"worlds/sites/{SITE}")
    assert calls[2] == ("src/battlemap.py", "wrap", "--dir",
                        f"worlds/sites/{SITE}")

    result = job["result"]
    assert result["slug"] == "marsh-camp"
    assert result["label"] == SITE
    base = "/campaign-file/testvale/maps/sites/marsh-camp/"
    assert result["svg"] == base + "marsh-camp.svg"
    assert result["png"] == base + "marsh-camp.png"
    assert result["uvtt"] == base + "marsh-camp.uvtt"
    assert result["scene"] == base + "marsh-camp.json"

    # vault-unique basenames (obsidian-integration.md §4): embeds resolve by
    # filename, so scene.* ships as <slug>.*; params + companion note ride along
    dest = tmp_spine_root / "testvale" / "maps" / "sites" / "marsh-camp"
    for name in ("marsh-camp.json", "marsh-camp.svg", "marsh-camp.png",
                 "marsh-camp.uvtt", "params.yaml", "marsh-camp.md"):
        assert (dest / name).is_file(), name
    params = yaml.safe_load((dest / "params.yaml").read_text(encoding="utf-8"))
    assert params["name"] == SITE
    assert params["kind"] == "camp"
    # the default seed is crc32(slug) % 10^7 — a derived artifact's seed,
    # recorded as provenance and passed to the engine
    assert params["seed"] == str(zlib.crc32(b"marsh-camp") % 10_000_000)
    note = (dest / "marsh-camp.md").read_text(encoding="utf-8")
    assert "![[marsh-camp.png]]" in note  # embed by vault-unique basename
    assert "place:" not in note  # no place attached — the field was empty

    # the listing serves the new site
    m = client.get("/api/campaign/testvale/maps").json()
    assert any(s["slug"] == "marsh-camp" and s["label"] == SITE
               for s in m["sites"])


def test_sites_job_explicit_seed_is_passed_through(client, tmp_spine_root,
                                                   monkeypatch):
    install(tmp_spine_root, monkeypatch)
    calls = []

    def recording_py(engine_root, script, *args, **kwargs):
        calls.append((script,) + args)
        return fake_battlemap(engine_root, script, *args, **kwargs)

    monkeypatch.setattr(engines, "run_py", recording_py)
    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "dungeon", "name": SITE, "seed": "999"})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    gen_call = calls[0]  # the gen- stage carries the seed
    assert "--seed" in gen_call and "999" in gen_call
    assert job["result"]["slug"] == "marsh-camp"


def test_sites_job_sharp_failure_carries_the_documented_repair(
        client, tmp_spine_root, monkeypatch):
    def fake_png_broken(engine_root, *args, timeout=600, on_line=None,
                        env=None):
        return 1, ["Error: Cannot find module 'sharp' imported from "
                   "scripts/render-scene-png.mjs"]

    install(tmp_spine_root, monkeypatch, png=fake_png_broken)
    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "camp", "name": SITE})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    # D7: the repair recipe is in the error text, exactly as documented
    assert "CI=true pnpm install --offline" in job["error"]
    assert "settlemaker" in job["error"]
    # nothing copied into the campaign on failure
    assert not (tmp_spine_root / "testvale" / "maps" / "sites" /
                "marsh-camp").exists()


def test_sites_post_422_bad_name_and_kind(client, spine_root):
    for name in ("", "  ", 'evil"quote', "sl/ash", "wild*card"):
        r = client.post("/api/campaign/testvale/maps/sites",
                        json={"kind": "camp", "name": name})
        assert r.status_code == 422, name
        assert "name" in r.json()["error"]
    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "volcano", "name": "Ok"})
    assert r.status_code == 422
    assert "dungeon|cave|camp" in r.json()["error"]
