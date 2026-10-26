"""POST /maps/sites — the full battlemap chain with FAKE engines.

The fakes reproduce the documented CLI contract exactly (argv shapes and
the campaign-data/maps/sites/<name> output location): gen-<kind> writes
scene.json, render writes scene.svg, render-scene-png.mjs writes
scene.png (sharp), wrap writes <slug>.uvtt. No real battlemap/sharp call
ever runs.
"""

import json
import os
import zlib
from pathlib import Path

import yaml

import engines
import spine
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
        site_dir = engines.map_sites_dir() / name
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "scene.json").write_text(json.dumps(
            {"name": name, "grids": {"x": 46, "y": 30},
             "pixels_per_grid": 70, "meters_per_grid": 1.524,
             "provenance": {"generator": f"battlemap.py {cmd} seed={seed}"},
             "features": [], "doors": []}), encoding="utf-8")
        return 0, [f"camp '{name}': 7 tents -> {site_dir}"]
    if cmd in ("render", "wrap"):
        # --dir arrives dnd-mapping-relative (the CLI resolves it against
        # its cwd); the fake mirrors that resolution
        site_dir = engine_root / Path(arg("--dir"))
        if cmd == "render":
            (site_dir / "scene.svg").write_text("<svg>scene</svg>",
                                                encoding="utf-8")
            return 0, ["rendered scene.svg"]
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
    fake_mapping = tmp_spine_root / "dnd-mapping"
    fake_mapping.mkdir()
    monkeypatch.setattr(engines, "DND_MAPPING", fake_mapping)
    # the battlemap home lives under the data root — keep it tmp-owned
    monkeypatch.setattr(spine, "DATA_ROOT", tmp_spine_root / "campaign-data")


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

    # the documented chain, in order, with the human name (list-form argv);
    # the --dir paths resolve (dnd-mapping-relative) into the data root's
    # maps/sites home
    rel = os.path.relpath(engines.map_sites_dir() / SITE,
                          engines.DND_MAPPING)
    assert calls[0] == ("src/battlemap.py", "gen-camp", "--seed",
                        calls[0][3], "--name", SITE)
    assert calls[1] == ("src/battlemap.py", "render", "--dir", rel)
    assert calls[2] == ("src/battlemap.py", "wrap", "--dir", rel)

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
    assert "npm install" in job["error"]
    assert "settlemaker" in job["error"]
    # nothing copied into the campaign on failure
    assert not (tmp_spine_root / "testvale" / "maps" / "sites" /
                "marsh-camp").exists()


def test_sites_job_missing_artifacts_error_names_real_filenames(
        client, tmp_spine_root, monkeypatch):
    """P3-38: when wrap exits 0 without writing the UVTT, the error must
    name the concrete expected filename (marsh-camp.uvtt), not the
    '<slug>.uvtt' template — scene/svg/png were already concrete."""
    def fake_wrap_silent(engine_root, script, *args, timeout=600,
                          on_line=None, env=None):
        if args and args[0] == "wrap":
            return 0, ["wrapped (but wrote nothing)"]  # lying engine
        return fake_battlemap(engine_root, script, *args, **{
            "timeout": timeout, "on_line": on_line, "env": env})

    install(tmp_spine_root, monkeypatch)
    monkeypatch.setattr(engines, "run_py", fake_wrap_silent)
    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "camp", "name": SITE})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "scene pipeline did not produce" in job["error"]
    assert "marsh-camp.uvtt" in job["error"], job["error"]
    assert "<slug>" not in job["error"], job["error"]
    # nothing copied into the campaign on failure
    assert not (tmp_spine_root / "testvale" / "maps" / "sites" /
                "marsh-camp").exists()


def test_sites_post_422_bad_name_and_kind(client, spine_root):
    for name in ("", "  ", 'evil"quote', "sl/ash", "wild*card", "ctrl\x1fchar"):
        r = client.post("/api/campaign/testvale/maps/sites",
                        json={"kind": "camp", "name": name})
        assert r.status_code == 422, name
        assert "name" in r.json()["error"]
    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "volcano", "name": "Ok"})
    assert r.status_code == 422
    assert "dungeon|cave|camp" in r.json()["error"]


# ---------------- ticket 64: atomic canon writes + slug-collision refusals


def test_site_params_write_is_atomic(client, tmp_spine_root, monkeypatch):
    """(ticket 64) params.yaml is canon: a crash mid-plain-write must not
    truncate it — the write goes through write_text_atomic, so the
    poisoned plain-write path is never taken and the job completes whole."""
    from conftest import poison_plain_write

    install(tmp_spine_root, monkeypatch)
    poison_plain_write(monkeypatch, name_part="params.yaml")
    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": "camp", "name": SITE})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    params = yaml.safe_load(
        (tmp_spine_root / "testvale" / "maps" / "sites" / "marsh-camp" /
         "params.yaml").read_text(encoding="utf-8"))
    assert params["name"] == SITE and params["kind"] == "camp"


def test_site_slug_collision_refuses_409(client, tmp_spine_root, monkeypatch):
    """(ticket 64) 'The Drowned Crypt' and 'the drowned crypt' slugify to
    one maps/sites/the-drowned-crypt/ — the second job used to silently
    replace the first's artifacts. A DIFFERING request (name or kind)
    refuses with 409 naming the existing artifact; the same request
    verbatim stays the documented regenerate-overwrite case."""
    install(tmp_spine_root, monkeypatch)

    def run(name, kind="camp"):
        r = client.post("/api/campaign/testvale/maps/sites",
                        json={"kind": kind, "name": name})
        if r.status_code == 202:
            return r, wait_for_job(client, r.json()["job_id"])
        return r, None

    r, job = run("The Drowned Crypt")
    assert r.status_code == 202 and job["status"] == "done", job and job["error"]
    dest = tmp_spine_root / "testvale" / "maps" / "sites" / "the-drowned-crypt"

    # the case-collision refuses, naming the existing artifacts
    r, _ = run("the drowned crypt")
    assert r.status_code == 409
    detail = r.json()["error"]
    assert "maps/sites/the-drowned-crypt" in detail
    assert "The Drowned Crypt" in detail
    params = yaml.safe_load((dest / "params.yaml").read_text(encoding="utf-8"))
    assert params["name"] == "The Drowned Crypt"  # nothing was replaced

    # the same name regenerated as a different kind is also a differing
    # request into the same artifact set
    r, _ = run("The Drowned Crypt", kind="dungeon")
    assert r.status_code == 409
    assert "dungeon" in r.json()["error"]

    # the same request verbatim is regeneration — allowed to replace
    r, job = run("The Drowned Crypt")
    assert r.status_code == 202 and job["status"] == "done", job and job["error"]
    params = yaml.safe_load((dest / "params.yaml").read_text(encoding="utf-8"))
    assert params["kind"] == "camp"
