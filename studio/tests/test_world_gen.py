"""POST /world — the 409 force gate and the job with a FAKE FMG step, plus
the steering surface (options validation, FMG_OPTIONS pass-through) and the
world-options LLM suggestion endpoint (faked completion).

No real FMG/vitest/import_fmg call ever runs here: engines.run_node (the
headless-FMG incantation) and engines.run_py (import_fmg) are monkeypatched
to fake engines that write structurally real artifacts into the places the
documented CLIs would (FMG_OUT dump, worlds/candidates/*.yaml).
"""

import json

import engines
import llm_client
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


# ------------------------------------------------------------- steering


def test_world_post_422_bad_options(client, spine_root):
    for options, why in (
        ({"species": "dwarven"}, "unknown key"),
        ({"culture_set": "klingon"}, "bad enum"),
        ({"cultures": "twelve"}, "non-integer count"),
        ({"burgs": 10 ** 9}, "out-of-range count"),
        ({"temperature_c": {"midway": 5}}, "unknown temperature key"),
        ({"temperature_c": {"north_pole": 999}}, "out-of-range temperature"),
    ):
        r = client.post("/api/campaign/testvale/world",
                        json={"seed": "42", "force": True, "options": options})
        assert r.status_code == 422, (options, why, r.json())
        assert "error" in r.json()


def test_world_job_carries_fmg_options(client, tmp_spine_root, monkeypatch):
    """Steering rides to the runner as FMG_OPTIONS, translated onto FMG's
    real option paths; with nothing set the env key is absent entirely."""
    calls = {}

    def fake_run_node(engine_root, *args, timeout=600, on_line=None, env=None):
        calls["env"] = env
        with open(f"{env['FMG_OUT']}/headless-{env['FMG_SEED']}.json", "w",
                  encoding="utf-8") as f:
            json.dump(tiny_fmg_dump(str(env["FMG_SEED"])), f)
        return 0, ["headless export"]

    def fake_run_py(engine_root, script, *args, timeout=600, on_line=None,
                    env=None):
        candidates = engine_root / "worlds" / "candidates"
        candidates.mkdir(parents=True, exist_ok=True)
        (candidates / "testland-0042.yaml").write_text(
            "meta:\n  seed: '42'\nsummary:\n  nations: 1\n  settlements: 1\n",
            encoding="utf-8")
        return 0, [("headless-42.json -> testland-0042.yaml: 'Testland' "
                    "(seed 42): 1 nations, 1 settlements, 1 cultures")]

    monkeypatch.setattr(engines, "run_node", fake_run_node)
    monkeypatch.setattr(engines, "run_py", fake_run_py)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")

    r = client.post("/api/campaign/testvale/world", json={
        "seed": "42", "force": True,
        "options": {"culture_set": "highFantasy", "template": "continents",
                    "burgs": 800, "precipitation": 60, "latitude": 15,
                    "temperature_c": {"north_pole": -45}}})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert json.loads(calls["env"]["FMG_OPTIONS"]) == {
        "generation": {"cultures": {"set": "highFantasy"},
                       "template": "continents",
                       "burgs": {"limit": 800},
                       "geography": {"latitude": 15}},
        "map": {"climate": {"precipitation": 60,
                            "temperature": {"northPole": -45}}},
    }
    assert any("steering" in ln for ln in job["log_tail"])

    calls.clear()
    r = client.post("/api/campaign/testvale/world",
                    json={"seed": "43", "force": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert "FMG_OPTIONS" not in (calls.get("env") or {})


# ------------------------------------------------- world-options (LLM)


def test_world_options_422_empty_premise(client, spine_root):
    r = client.post("/api/campaign/testvale/world-options", json={"premise": "  "})
    assert r.status_code == 422
    assert "premise" in r.json()["error"]


def test_world_options_503_without_key(client, spine_root, monkeypatch):
    def no_key(*args, **kwargs):
        raise RuntimeError("no LLM API key found — set LLM_API_KEY in llm.env")

    monkeypatch.setattr(llm_client, "complete", no_key)
    r = client.post("/api/campaign/testvale/world-options",
                    json={"premise": "an icy north held by dwarves"})
    assert r.status_code == 503
    assert "LLM_API_KEY" in r.json()["error"]


def test_world_options_suggests_validated_values(client, spine_root, monkeypatch):
    """The happy path: the model's JSON (fences tolerated) comes back in
    client-format form values, already passed through the same validator
    the generate call uses."""

    def fake_complete(prompt, **kwargs):
        assert "icy north" in prompt
        return ('Sure! Here are the options:\n```json\n'
                '{"culture_set": "highFantasy", "template": "continents", '
                '"temperature_c": {"north_pole": -45, "equator": 27}}\n```')

    monkeypatch.setattr(llm_client, "complete", fake_complete)
    r = client.post("/api/campaign/testvale/world-options",
                    json={"premise": "an icy north held by dwarves and trolls"})
    assert r.status_code == 200, r.json()
    assert r.json()["options"] == {
        "culture_set": "highFantasy",
        "template": "continents",
        "temperature_c": {"north_pole": -45, "equator": 27},
    }


def test_world_options_502_on_unusable_suggestions(client, spine_root,
                                                   monkeypatch):
    # prose with no JSON object at all
    monkeypatch.setattr(llm_client, "complete",
                        lambda *a, **k: "I would suggest a cold world!")
    r = client.post("/api/campaign/testvale/world-options",
                    json={"premise": "x"})
    assert r.status_code == 502
    assert "unusable suggestion" in r.json()["error"]
    # a JSON object with an invalid value: refused, with the manual fallback
    monkeypatch.setattr(llm_client, "complete",
                        lambda *a, **k: '{"culture_set": "klingon"}')
    r = client.post("/api/campaign/testvale/world-options",
                    json={"premise": "x"})
    assert r.status_code == 502
    assert "invalid option" in r.json()["error"]
    assert "fill the form manually" in r.json()["error"]
