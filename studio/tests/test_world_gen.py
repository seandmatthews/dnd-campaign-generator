"""POST /world — the 409 force gate and the job with a FAKE FMG step.

No real FMG/vitest/import_fmg call ever runs here: engines.run_node (the
headless-FMG incantation) and engines.run_py (import_fmg) are monkeypatched
to fake engines that write structurally real artifacts into the places the
documented CLIs would (FMG_OUT dump, worlds/candidates/*.yaml).
"""

import json

import engines
import spine
from conftest import tiny_fmg_dump, wait_for_job


def test_world_post_409_without_force(client, spine_root):
    # testvale's fixture already carries world/geography.yaml
    r = client.post("/api/campaign/testvale/world", json={"seed": "12345"})
    assert r.status_code == 409
    assert "geography.yaml already exists" in r.json()["error"]
    assert "force" in r.json()["error"]


def test_world_post_422_bad_seed(client, spine_root):
    for seed in ("", "  ", "../evil", "seed with spaces", "x" * 65):
        r = client.post("/api/campaign/testvale/world",
                        json={"seed": seed, "force": True})
        assert r.status_code == 422, seed
        assert "seed" in r.json()["error"]


def test_world_post_404_unknown_campaign(client, spine_root):
    assert client.post("/api/campaign/nowhere/world",
                       json={"seed": "1"}).status_code == 404


def test_world_job_fake_fmg_copies_dump_and_geography(client, tmp_spine_root,
                                                      monkeypatch):
    calls = {}

    def fake_run_node(engine_root, *args, timeout=600, on_line=None, env=None):
        calls["fmg_root"] = engine_root
        calls["fmg_args"] = args
        calls["fmg_env"] = env
        assert env and env["FMG_SEED"] == "42" and env["FMG_OUT"]
        with open(f"{env['FMG_OUT']}/headless-42.json", "w",
                  encoding="utf-8") as f:
            json.dump(tiny_fmg_dump("42"), f)
        if on_line:
            on_line("headless export: headless-42.json (2 burgs, 1 states)")
        return 0, ["headless export: headless-42.json"]

    def fake_run_py(engine_root, script, *args, timeout=600, on_line=None,
                    env=None):
        calls["import_root"] = engine_root
        calls["import_args"] = (script,) + args
        assert script == "src/import_fmg.py"
        dump_path = args[0]
        dump = json.loads(open(dump_path, encoding="utf-8").read())
        candidates = engine_root / "worlds" / "candidates"
        candidates.mkdir(parents=True, exist_ok=True)
        out = candidates / "testland-0042.yaml"
        out.write_text(
            "meta:\n  seed: '42'\n"
            "summary:\n  nations: 1\n  settlements: 1\n"
            f"  map_name: {dump['info']['mapName']}\n",
            encoding="utf-8")
        if on_line:
            on_line(f"headless-42.json -> testland-0042.yaml: 'Testland' "
                    "(seed 42): 1 nations, 1 settlements, 1 cultures")
        return 0, ["headless-42.json -> testland-0042.yaml: 'Testland' "
                   "(seed 42): 1 nations, 1 settlements, 1 cultures"]

    monkeypatch.setattr(engines, "run_node", fake_run_node)
    monkeypatch.setattr(engines, "run_py", fake_run_py)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")

    r = client.post("/api/campaign/testvale/world",
                    json={"seed": "42", "force": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["kind"] == "world-gen"

    # the exact documented headless incantation, cwd = the FMG clone
    assert calls["fmg_root"] == (tmp_spine_root / "dnd-mapping" / "tools" /
                                 "Fantasy-Map-Generator")
    assert calls["fmg_args"] == ("node_modules/vitest/vitest.mjs", "run",
                                 "src/generators/headless-export.test.ts")
    assert calls["import_root"] == tmp_spine_root / "dnd-mapping"
    assert calls["import_args"][0] == "src/import_fmg.py"

    # copy-in of the genesis record + copy-out of the geography
    world = tmp_spine_root / "testvale" / "world"
    dump_on_disk = json.loads(
        (world / "source.json").read_text(encoding="utf-8"))
    assert dump_on_disk["info"]["seed"] == "42"
    assert (world / "geography.yaml").is_file()

    assert job["result"]["nations"] == 1
    assert job["result"]["settlements"] == 1
    # the hero rendered from the dump (read-only path)
    assert job["result"]["map"] == "/campaign-file/testvale/world/map.svg"
    svg = (world / "map.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert "#aa4040" in svg  # Testonia's FMG color
    # (serving via /campaign-file is covered by test_files.py and the live
    # sample-campaign flow — the mount binds the real campaigns root at boot)


def test_world_job_survives_unrenderable_dump(client, tmp_spine_root,
                                              monkeypatch):
    """The map.svg step is best-effort: a world without drawable cells still
    generates (map: null in the result), because the world exists regardless."""

    def fake_run_node(engine_root, *args, timeout=600, on_line=None, env=None):
        with open(f"{env['FMG_OUT']}/headless-7.json", "w",
                  encoding="utf-8") as f:
            json.dump({"info": {"seed": "7", "mapName": "Bare",
                                "width": 10, "height": 10},
                       "settings": {}, "pack": {}}, f)
        return 0, ["ok"]

    def fake_run_py(engine_root, script, *args, timeout=600, on_line=None,
                    env=None):
        candidates = engine_root / "worlds" / "candidates"
        candidates.mkdir(parents=True, exist_ok=True)
        (candidates / "bare-0007.yaml").write_text(
            "summary:\n  nations: 0\n  settlements: 0\n", encoding="utf-8")
        return 0, ["bare.json -> bare-0007.yaml: 'Bare' (seed 7): "
                   "0 nations, 0 settlements, 0 cultures"]

    monkeypatch.setattr(engines, "run_node", fake_run_node)
    monkeypatch.setattr(engines, "run_py", fake_run_py)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")

    r = client.post("/api/campaign/testvale/world",
                    json={"seed": "7", "force": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["result"]["map"] is None
    assert not (tmp_spine_root / "testvale" / "world" / "map.svg").exists()
    assert "map.svg skipped" in "\n".join(job["log_tail"])


def test_world_job_engine_failure_is_a_job_error(client, tmp_spine_root,
                                                 monkeypatch):
    def failing_fmg(*args, on_line=None, **kwargs):
        if on_line:
            on_line("vitest: nope")
        return 3, ["vitest: nope"]

    monkeypatch.setattr(engines, "run_node", failing_fmg)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")

    r = client.post("/api/campaign/testvale/world",
                    json={"seed": "42", "force": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "headless FMG failed" in job["error"]
    # nothing was staged into the campaign
    assert not (tmp_spine_root / "testvale" / "world" / "source.json").exists()
    assert (tmp_spine_root / "testvale" / "world" /
            "geography.yaml").is_file()  # the fixture's original, untouched
