"""Tests for the FMG importer — the riskiest parser in dnd-mapping.

Zero dependencies; a miniature .map file is synthesized in tmp, so no
multi-megabyte real dumps are needed. import_fmg.OUT_DIR is redirected into
the tmp tree for every test: nothing here touches worlds/candidates/.
  run directly:  python tests/test_import_fmg.py
"""

import json
import pathlib
import subprocess
import sys
import tempfile

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
import import_fmg

FAILED = []


def ok(name, cond, detail=""):
    print(f"  {name}: {'OK' if cond else 'FAIL ' + detail}")
    if not cond:
        FAILED.append(name)


def make_map(tmp, name="tiny.map", *, include=(), map_name="Tinia", seed="12345",
             mtime=None):
    """A miniature .map: header | settings | then one JSON line per section.
    `include` selects which data sections are present."""
    sections = {
        "features": [{"i": 1, "type": "ocean", "cells": 900},
                     {"i": 2, "type": "island", "cells": 700}],
        "cultures": [{"i": 1, "name": "Highlanders", "base": 3, "type": "Nomadic",
                      "expansionism": 0.5}],
        "states": [{"i": 1, "name": "Bonia", "salesTax": 0.2, "type": "Kingdom",
                    "formName": "Feudal Monarchy", "capital": 2, "cells": 400,
                    "color": "#a37d68", "neighbors": []}],
        "burgs": [{"i": 0}, {"i": 2, "name": "Ruravaqan", "port": True,
                             "capital": True, "state": 1, "population": 29.6,
                             "type": "City", "x": 276.84, "y": 442.91}],
        "provinces": [{"i": 1, "name": "The Shore", "burg": 2, "state": 1}],
        "religions": [{"i": 0, "name": "No religion"},
                      {"i": 1, "name": "The Cult", "type": "Cult"}],
        "rivers": [{"i": 1, "name": "The Brume", "discharge": 42.5,
                    "length": 12.3, "cells": [7, 8]}],
    }
    settings = {"seed": seed, "lore": {"name": map_name},
                "graph": {"width": 1280, "height": 720}}
    compact = {"separators": (",", ":"), "ensure_ascii": False}  # FMG emits compact JSON
    lines = [
        "1.153.1|2024-01-01|||||map-0000",
        json.dumps(settings),
        '{"nameBases": 1}',
    ]
    for key in include:
        lines.append(json.dumps(sections[key], **compact))
    text = "\n".join(lines) + "\n"
    p = pathlib.Path(tmp) / name
    p.write_text(text, encoding="utf-8")
    if mtime:
        import os
        os.utime(p, (mtime, mtime))
    return p


def import_to(tmpdir, path):
    """import_file with OUT_DIR redirected into the test's tmp tree."""
    saved = import_fmg.OUT_DIR
    import_fmg.OUT_DIR = pathlib.Path(tmpdir) / "candidates"
    try:
        return import_fmg.import_file(path)
    finally:
        import_fmg.OUT_DIR = saved


def test_full_import(tmp):
    p = make_map(tmp, include=("features", "cultures", "states", "burgs",
                               "provinces", "religions", "rivers"))
    out, doc, missing = import_to(tmp, p)
    ok("no sections missing on a well-formed dump", missing == [], str(missing))
    s = doc["summary"]
    ok("one nation", s["nations"] == 1, str(s))
    ok("one settlement", s["settlements"] == 1, str(s))
    ok("capital name resolved", doc["nations"][0]["capital"] == "Ruravaqan",
       str(doc["nations"][0]))
    ok("settlement fields carried",
       doc["major_settlements"][0]["port"] is True
       and doc["major_settlements"][0]["population_thousands"] == 29.6,
       str(doc["major_settlements"]))
    ok("slug from map name + seed suffix", out.name == "tinia-2345.yaml", out.name)
    ok("output went to the redirected dir", out.parent.name == "candidates")
    ok("river imports from a .map (no graph arrays, no settlements)",
       doc["rivers"] == [{"name": "The Brume", "discharge": 42.5,
                          "length": 12.3, "settlements": []}],
       str(doc["rivers"]))


def test_missing_sections_are_reported_not_silent(tmp):
    """The silent-failure path: a drifted file must come back with a `missing`
    report naming what imported empty — not as a world with zero warnings."""
    p = make_map(tmp, include=("features", "burgs"))  # no states/cultures/...
    out, doc, missing = import_to(tmp, p)
    ok("missing sections named", set(missing) == {"cultures", "states",
                                                  "provinces", "religions",
                                                  "rivers"},
       str(missing))
    ok("burg section still parsed", doc["summary"]["settlements"] == 1)


def test_seedless_dump_gets_a_noseed_suffix(tmp):
    p = make_map(tmp, seed=None, map_name="NoSeed")
    out, doc, missing = import_to(tmp, p)
    ok("noseed suffix instead of 'None'", out.name == "noseed-noseed.yaml",
       out.name)
    ok("meta seed is 'None'-free", "None" not in str(doc["meta"]["seed"]),
       str(doc["meta"]["seed"]))


def test_output_is_mtime_and_clock_free(tmp):
    """The regression: meta stamped `imported_at` from the source's mtime (and
    once from the wall clock) — not reproducible content, so every
    regeneration permanently dirtied the tracked derivative. Provenance is
    seed + fmg_version only; re-importing an unchanged dump is byte-identical
    no matter when it runs."""
    p = make_map(tmp, include=("features", "burgs"), mtime=1000000000)
    out, doc, _ = import_to(tmp, p)
    ok("no timestamp key in meta",
       not any("at" in k for k in doc["meta"] if k.endswith("_at")),
       str(sorted(doc["meta"])))
    ok("provenance is seed + version",
       doc["meta"]["seed"] == "12345" and doc["meta"]["fmg_version"],
       str(doc["meta"]))


def test_cli_batch_survives_one_bad_file(tmp):
    good = make_map(tmp, name="good.map", include=("features", "burgs"))
    bad = pathlib.Path(tmp) / "bad.map"
    bad.write_text("1.0|not json\n", encoding="utf-8")
    # drive the real main() with OUT_DIR pointed into tmp: one malformed file
    # must not abort the batch, and the run must exit nonzero
    driver = (
        "import sys, pathlib\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "import import_fmg\n"
        f"import_fmg.OUT_DIR = pathlib.Path({str(tmp)!r}) / 'candidates'\n"
        f"sys.argv = ['import_fmg.py', {str(good)!r}, {str(bad)!r}]\n"
        "sys.exit(import_fmg.main())\n"
    )
    proc = subprocess.run([sys.executable, "-c", driver],
                          capture_output=True, text=True)
    ok("batch runner exits nonzero on a bad file", proc.returncode != 0,
       f"rc={proc.returncode}")
    ok("the failure is named on stderr", "IMPORT FAILED" in proc.stderr,
       proc.stderr[:200])
    ok("the good file still imported",
       "good.map ->" in proc.stdout, proc.stdout)


def test_json_export_reports_no_missing(tmp):
    data = {
        "info": {"mapName": "Fullia", "seed": "777"},
        "settings": {"seed": "777"},
        "pack": {
            "features": [0, {"i": 1, "type": "ocean", "cells": 900}],
            "cultures": [0, {"i": 1, "name": "C", "base": 1, "expansionism": 1}],
            "states": [0, {"i": 1, "name": "S", "salesTax": 0, "cells": 10,
                           "color": "#fff", "neighbors": [], "culture": 1}],
            "burgs": [0, {"i": 1, "name": "B", "port": False, "state": 1,
                          "population": 1.0, "x": 1, "y": 2}],
            "provinces": [0],
            "religions": [0],
            "cells": {"v": [[0, 1, 2]]},
            "vertices": {"p": [[0, 0], [2, 0], [1, 4]]},
            "rivers": [{"i": 1, "name": "The Brume", "discharge": 10,
                        "length": 5, "cells": [0]}],
        },
    }
    p = pathlib.Path(tmp) / "full.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    out, doc, missing = import_to(tmp, p)
    ok("json export: nothing missing", missing == [], str(missing))
    ok("json export: placeholders dropped", doc["summary"]["settlements"] == 1)
    ok("json export: nation culture resolved",
       doc["nations"][0]["culture"] == "C", str(doc["nations"][0]))
    ok("json export: river grounded to the burg it passes",
       doc["rivers"] == [{"name": "The Brume", "discharge": 10.0,
                          "length": 5.0, "settlements": ["B"]}],
       str(doc["rivers"]))
    ok("json export: river counted in summary",
       doc["summary"]["rivers"] == 1, str(doc["summary"]))


# --------------------------------------------------------------- climate


STEERING_SETTINGS = {
    "seed": "777",
    "graph": {"width": 1280, "height": 720, "points": 10000},
    "geography": {"coordinates": {"latT": 180, "latN": 90, "latS": -90}},
    "climate": {"temperature": {"equator": 30, "northPole": -40,
                                "southPole": -15}, "precipitation": 120},
    "cultures": {"set": "highFantasy"},
}
# y -> latitude on a 720-high map spanning latT=180 from latN=90:
#   y=180 -> 90 - 45.0 = 45.0;  y=540 -> 90 - 135.0 = -45.0
STEERING_PACK = {
    "features": [0, {"i": 1, "type": "ocean", "cells": 900}],
    "biomes": [0,
               {"i": 1, "name": "Taiga"},
               {"i": 2, "name": "Glacier"},
               {"i": 3, "name": "Marine"}],
    "cultures": [0, {"i": 1, "name": "Dunirr (Dwarven)", "base": 1,
                     "expansionism": 1}],
    "states": [0, {"i": 1, "name": "S", "salesTax": 0, "cells": 4,
                   "color": "#fff", "neighbors": [], "culture": 1,
                   "capital": 1}],
    "burgs": [0,
              {"i": 1, "name": "B", "port": False, "state": 1, "cell": 0,
               "capital": True, "population": 9.0, "x": 10, "y": 540},
              {"i": 2, "name": "C", "port": False, "state": 1, "cell": 3,
               "capital": False, "population": 1.0, "x": 20, "y": 180}],
    "provinces": [0],
    "religions": [0],
    "rivers": [],
    # cell biomes: state 1 owns cells 0-3; taiga dominates 2:1 over glacier
    # (cell 3 is marine-owned-by-nobody: state 0 -> excluded)
    "cells": {"biome": [1, 1, 2, 3], "state": [1, 1, 1, 0],
              "v": [[0, 1, 2], [1, 2, 3], [2, 3, 4], [3, 4, 5]]},
    "vertices": {"p": [[0, 0], [2, 0], [1, 4], [3, 1], [4, 3], [5, 5]]},
}


def test_climate_fields_headless_shape(tmp):
    """Headless dumps: map options at the settings top level, cells as
    columnar arrays. geography.yaml gains culture_set + climate in meta,
    dominant biome + capital latitude per nation, biome + latitude per
    settlement — all pure functions of the dump (byte-stable)."""
    data = {"info": {"mapName": "Fullia", "seed": "777", "height": 720},
            "settings": dict(STEERING_SETTINGS),
            "pack": json.loads(json.dumps(STEERING_PACK))}
    p = pathlib.Path(tmp) / "headless-777.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    _, doc, _ = import_to(tmp, p)
    nation = doc["nations"][0]
    ok("nation dominant biome (marine excluded, taiga 2:1)",
       nation.get("biome") == "Taiga", str(nation))
    ok("nation latitude from the capital's y", nation.get("latitude") == -45.0,
       str(nation))
    ok("biome/latitude sit right after culture",
       list(nation)[:6] == ["id", "name", "kind", "capital", "culture",
                            "biome"], str(list(nation)))
    by_name = {s["name"]: s for s in doc["major_settlements"]}
    ok("settlement biome from its own cell",
       by_name["B"].get("biome") == "Taiga" and
       by_name["C"].get("biome") == "Marine", str(by_name))
    ok("settlement latitudes", by_name["B"].get("latitude") == -45.0
       and by_name["C"].get("latitude") == 45.0, str(by_name))
    ok("meta records the steering",
       doc["meta"].get("culture_set") == "highFantasy"
       and doc["meta"].get("climate") == {"equator_c": 30, "north_pole_c": -40,
                                          "south_pole_c": -15},
       str(doc["meta"]))
    again = import_to(tmp, p)[1]
    ok("re-import is byte-stable", again == doc)


def test_climate_fields_browser_shape(tmp):
    """Browser full JSON exports: options nested under settings.options.map,
    cells expanded into per-cell dicts — same fields, same values."""
    pack = json.loads(json.dumps(STEERING_PACK))
    cells = pack["cells"]
    pack["cells"] = [
        {"i": i, "biome": cells["biome"][i], "state": cells["state"][i],
         "v": cells["v"][i]}
        for i in range(len(cells["biome"]))
    ]
    data = {"info": {"mapName": "Fullia", "seed": "777", "height": 720},
            "settings": {"options": {"map": dict(STEERING_SETTINGS)}},
            "pack": pack}
    p = pathlib.Path(tmp) / "full.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    _, doc, _ = import_to(tmp, p)
    nation = doc["nations"][0]
    ok("browser shape: nation biome + latitude",
       nation.get("biome") == "Taiga" and nation.get("latitude") == -45.0,
       str(nation))
    ok("browser shape: meta steering",
       doc["meta"].get("culture_set") == "highFantasy", str(doc["meta"]))


def test_climate_fields_degrade_to_absent(tmp):
    """A dump with no options and no per-cell data (.map-section imports,
    pre-enrichment headless dumps) keeps the old shape: no biome/latitude
    keys anywhere, no meta.climate/culture_set — absent, never wrong."""
    data = {
        "info": {"mapName": "Bare", "seed": "5"},
        "settings": {"seed": "5"},
        "pack": {
            "features": [0, {"i": 1, "type": "ocean", "cells": 900}],
            "cultures": [0, {"i": 1, "name": "C", "base": 1, "expansionism": 1}],
            "states": [0, {"i": 1, "name": "S", "salesTax": 0, "cells": 10,
                           "color": "#fff", "neighbors": [], "culture": 1,
                           "capital": 1}],
            "burgs": [0, {"i": 1, "name": "B", "port": False, "state": 1,
                          "population": 1.0, "x": 1, "y": 2}],
            "provinces": [0],
            "religions": [0],
            "rivers": [],
        },
    }
    p = pathlib.Path(tmp) / "bare.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    _, doc, _ = import_to(tmp, p)
    nation = doc["nations"][0]
    ok("no biome/latitude on the nation",
       "biome" not in nation and "latitude" not in nation, str(nation))
    ok("no biome/latitude on settlements",
       all("biome" not in s and "latitude" not in s
           for s in doc["major_settlements"]), str(doc["major_settlements"]))
    ok("no climate/culture_set in meta",
       "climate" not in doc["meta"] and "culture_set" not in doc["meta"],
       str(doc["meta"]))


if __name__ == "__main__":
    for fn in [test_full_import, test_missing_sections_are_reported_not_silent,
               test_seedless_dump_gets_a_noseed_suffix,
               test_output_is_mtime_and_clock_free,
               test_cli_batch_survives_one_bad_file,
               test_json_export_reports_no_missing,
               test_climate_fields_headless_shape,
               test_climate_fields_browser_shape,
               test_climate_fields_degrade_to_absent]:
        with tempfile.TemporaryDirectory() as d:
            fn(d)
    print()
    if FAILED:
        print(f"{len(FAILED)} test(s) failed: {FAILED}")
        sys.exit(1)
    print("all import_fmg tests passed")
