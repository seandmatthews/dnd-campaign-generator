"""Integration tier: the real engine CLIs, invoked with the routers' argv.

The fast suite monkeypatches studio/engines.py, so it proves route
semantics but never notices an engine's CLI or output format drifting.
These tests run the actual subprocesses behind the wrappers and pin the
seams the studio depends on — one test per engine entry point:

  routers/simulation.py -> agentic-simulator  src/why.py, src/views.py,
                              src/tick.py
  routers/maps.py       -> dnd-mapping       scripts/gen-city.mjs,
                              src/import_fmg.py, src/battlemap.py
                              gen/render + scripts/render-scene-png.mjs
                              + wrap, FMG headless-export.test.ts
  studio/loregen.py     -> verification      check.py

Ground rules (D3): subprocess list args only (the parent path contains
`&`), `-X utf8` for python, explicit timeouts, assertions on exit codes and
bytes — never an LLM, never the network. Ruravaqan is read-only here; the
engine homes are documented scratch (D4): state is copied in exactly as the
routers do, the generated battlemap site is mirrored into a tmp dir and its
scratch copy removed, and import_fmg.py's candidate yaml is left where the
engine writes it (that is its documented behavior).
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PARENT = Path(__file__).resolve().parents[2]  # .../Documents/D&D
DND_MAPPING = PARENT / "dnd-mapping"
AGENTIC_SIMULATOR = PARENT / "agentic-simulator"
VERIFICATION = PARENT / "verification"
FMG = DND_MAPPING / "tools" / "Fantasy-Map-Generator"
CAMPAIGNS = PARENT / "campaigns"
SAMPLE = CAMPAIGNS / "sample"
RURAVAQAN = CAMPAIGNS / "ruravaqan"

PY = [sys.executable, "-X", "utf8"]  # mirrors engines.run_py

# What this tier needs that git does not carry — the same three paths
# run-tests.py's INTEGRATION_NEEDS checks (campaigns/ and tools/ are
# gitignored). pytest.ini documents `pytest -m integration` as a direct
# invocation; this guard makes a fresh clone skip cleanly the way the
# orchestrator's note does, instead of erroring through the fixtures.
NEEDS = [
    RURAVAQAN / "simulation" / "state.yaml",
    DND_MAPPING / "tools" / "Fantasy-Map-Generator",
    DND_MAPPING / "tools" / "settlemaker" / "dist" / "index.js",
]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(p.exists() for p in NEEDS),
        reason="missing (gitignored) prerequisites: campaigns/ruravaqan, "
               "dnd-mapping/tools — the tier runs on the machines that "
               "have them; see run-tests.py"),
]


def run(cmd, cwd, timeout, env=None):
    """List-form args (the parent path contains `&`), captured, hard cap."""
    if env is not None:
        env = {**os.environ, **env}
    return subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd), env=env, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def engine_state():
    """The routers' copy-in (D4, simulation.py _copy_state_in): campaign
    state -> engine home, previous engine state kept as state.yaml.bak.
    The engine reads a fixed world/state.yaml; the campaign is canon."""
    dst = AGENTIC_SIMULATOR / "world" / "state.yaml"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file():
        shutil.copy2(dst, dst.parent / "state.yaml.bak")
    shutil.copy2(RURAVAQAN / "simulation" / "state.yaml", dst)
    return dst


# ------------------------------------------- routers/simulation.py seams ----

def test_why_json_seam(engine_state):
    """POST /simulation/why runs, verbatim:
    python -X utf8 src/why.py <week> <faction> --json   (cwd=agentic-simulator)"""
    r = run([*PY, "src/why.py", "4", "mint", "--json"],
            cwd=AGENTIC_SIMULATOR, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)  # the router 501s unless this parses
    assert data["week"] == 4
    assert data["faction"] == "mint"
    assert isinstance(data["lines"], list) and data["lines"]
    assert all(isinstance(line, str) for line in data["lines"])


def test_why_human_mode_seam(engine_state):
    """The same command WITHOUT --json must stay human format: rc 0 and
    output that is not JSON (the router never consumes this mode, but the
    flag must be an addition, not a replacement)."""
    r = run([*PY, "src/why.py", "4", "mint"],
            cwd=AGENTIC_SIMULATOR, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip()
    with pytest.raises(ValueError):
        json.loads(r.stdout)


def test_views_seam(engine_state):
    """GET /simulation/views/{id} runs, verbatim:
    python -X utf8 src/views.py <faction_id>   (cwd=agentic-simulator)"""
    r = run([*PY, "src/views.py", "mint"],
            cwd=AGENTIC_SIMULATOR, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    view = yaml.safe_load(r.stdout)  # the router yaml.safe_loads stdout
    assert isinstance(view, dict)
    assert view["you"]["id"] == "mint"
    assert "week" in view


def test_tick_inspect_is_read_only_seam(engine_state):
    """tick.py with NO args: rc 0, prints the city (the inspect mode the
    scheduler builds on), and world/state.yaml is byte-for-byte untouched —
    the read-only proof. (The canon advance is tick.py --live, which spends
    LLM credits and is NOT exercised here.)"""
    before = engine_state.stat().st_mtime_ns
    r = run([*PY, "src/tick.py"], cwd=AGENTIC_SIMULATOR, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Ruravaqan" in r.stdout
    assert engine_state.stat().st_mtime_ns == before


# ------------------------------------------- studio/loregen.py seam ---------

def test_check_structural_seam(tmp_path):
    """loregen._structural_findings runs, verbatim:
    python -X utf8 check.py <file> --out <dir>   (cwd=verification)
    Deliberately awful prose must come back as a findings.json queue."""
    prose = tmp_path / "awful.md"
    prose.write_text(
        "The customs men dragged the harbor dispute through every quarter of "
        "the city for what felt like a season and a half while the tanneries "
        "fumed and the bells rang and nobody with coin to lose could sleep "
        "for the sound of the picketers outside their shuttered warehouses.\n"
        "\n"
        "It was an ancient evil, as everyone eventually agreed. House Ferrand "
        "laid the blame on the Ruravaquan estate and its many debts.\n",
        encoding="utf-8")
    out = tmp_path / "findings"
    r = run([*PY, "check.py", prose, "--out", out],
            cwd=VERIFICATION, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    findings_file = out / "findings.json"
    assert findings_file.is_file(), r.stdout
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert isinstance(findings, list) and findings
    assert all("rule" in f for f in findings)
    rules = {f["rule"] for f in findings}
    assert "structural/long-sentence" in rules   # the 45-word opener
    assert "structural/canon-variant" in rules   # Ruravaquan ~ canon Ruravaqan


# ------------------------------------------- routers/maps.py seams ----------

def _port_burg() -> str:
    """One burg by name from sample geography.yaml with port: true."""
    geo = yaml.safe_load(
        (SAMPLE / "world" / "geography.yaml").read_text(encoding="utf-8")) or {}
    for s in geo.get("major_settlements") or []:
        if s.get("port"):
            return str(s["name"])
    pytest.skip("sample geography.yaml lists no port burg")


def test_gen_city_seam(tmp_path):
    """POST /maps/cities runs, verbatim:
    node scripts/gen-city.mjs --world <campaign source.json> --burg <name>
        --out <tmp>   (cwd=dnd-mapping)"""
    out = tmp_path / "city"
    r = run(["node", "scripts/gen-city.mjs",
             "--world", SAMPLE / "world" / "source.json",
             "--burg", _port_burg(), "--out", out],
            cwd=DND_MAPPING, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    svgs = list(out.glob("*.svg"))
    assert svgs and svgs[0].stat().st_size > 100_000  # a real city, not a stub
    assert list(out.glob("*.geojson")), sorted(p.name for p in out.iterdir())
    params = (out / "params.yaml").read_text(encoding="utf-8")
    assert "harbour" in params  # harbourSize recorded — port burgs need piers


def test_import_fmg_seam():
    """POST /world step 2 runs, verbatim:
    python -X utf8 src/import_fmg.py <dump.json>   (cwd=dnd-mapping)
    The summary line must carry '<dump> -> <name>.yaml:' — that regex is how
    the router finds the candidate it stages as world/geography.yaml. The
    candidate yaml is left in worlds/candidates (documented scratch)."""
    r = run([*PY, "src/import_fmg.py", SAMPLE / "world" / "source.json"],
            cwd=DND_MAPPING, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"-> (\S+\.yaml):", r.stdout)  # the router's own regex
    assert m, r.stdout
    assert (DND_MAPPING / "worlds" / "candidates" / m.group(1)).is_file()


def test_battlemap_chain_seam(tmp_path):
    """POST /maps/sites: the full documented chain with exactly the argv
    maps.py passes (battlemap.py's --dir paths are root-relative, and the
    rasterizer resolves its arg against the dnd-mapping root too — so the
    chain runs in the engine home, per maps.py's own docstring, and the
    artifact set is mirrored into tmp). If sharp is broken this fails
    loudly — that is the point; the documented repair is
    plain npm install in tools/settlemaker."""
    site = "smoke-cave"
    rel = f"worlds/sites/{site}"  # root-relative, exactly as the router passes
    engine_dir = DND_MAPPING / "worlds" / "sites" / site

    def battlemap(*args):
        return run([*PY, "src/battlemap.py", *args], cwd=DND_MAPPING,
                   timeout=180)

    try:
        r = battlemap("gen-cave", "--seed", "7", "--name", site)
        assert r.returncode == 0, r.stdout + r.stderr
        r = battlemap("render", "--dir", rel)
        assert r.returncode == 0, r.stdout + r.stderr
        r = run(["node", "scripts/render-scene-png.mjs", rel],
                cwd=DND_MAPPING, timeout=180)
        assert r.returncode == 0, (
            "scene rasterization failed (sharp) — documented repair: "
            "npm install in dnd-mapping/tools/settlemaker: "
            + r.stdout + r.stderr)
        r = battlemap("wrap", "--dir", rel)
        assert r.returncode == 0, r.stdout + r.stderr

        png = engine_dir / "scene.png"
        for p in (engine_dir / "scene.json", engine_dir / "scene.svg", png):
            assert p.is_file(), f"missing {p.name}"
        assert png.stat().st_size > 50_000
        uvtt = list(engine_dir.glob("*.uvtt"))
        assert uvtt, sorted(p.name for p in engine_dir.iterdir())

        artifacts = tmp_path / site
        shutil.copytree(engine_dir, artifacts)  # the copy-out half of D4
        assert (artifacts / "scene.svg").is_file()
        assert (artifacts / "scene.png").is_file()
    finally:
        shutil.rmtree(engine_dir, ignore_errors=True)  # scratch, not canon


def test_fmg_headless_seam(tmp_path):
    """POST /world step 1 runs, verbatim (docs/map-pipeline.md Tier 1):
    FMG_SEED=99 FMG_OUT=<tmp> node node_modules/vitest/vitest.mjs run
        src/generators/headless-export.test.ts   (cwd=Fantasy-Map-Generator)"""
    out = tmp_path / "fmg"
    r = run(["node", "node_modules/vitest/vitest.mjs", "run",
             "src/generators/headless-export.test.ts"],
            cwd=FMG, timeout=180, env={"FMG_SEED": "99", "FMG_OUT": str(out)})
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    dump = out / "headless-99.json"
    assert dump.is_file(), sorted(p.name for p in out.iterdir())
    assert dump.stat().st_size > 1_000_000
    data = json.loads(dump.read_text(encoding="utf-8"))
    assert "pack" in data  # the shape src/import_fmg.py parses
