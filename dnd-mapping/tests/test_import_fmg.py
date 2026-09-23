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
                               "provinces", "religions"))
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


def test_missing_sections_are_reported_not_silent(tmp):
    """The silent-failure path: a drifted file must come back with a `missing`
    report naming what imported empty — not as a world with zero warnings."""
    p = make_map(tmp, include=("features", "burgs"))  # no states/cultures/...
    out, doc, missing = import_to(tmp, p)
    ok("missing sections named", set(missing) == {"cultures", "states",
                                                  "provinces", "religions"},
       str(missing))
    ok("burg section still parsed", doc["summary"]["settlements"] == 1)


def test_seedless_dump_gets_a_noseed_suffix(tmp):
    p = make_map(tmp, seed=None, map_name="NoSeed")
    out, doc, missing = import_to(tmp, p)
    ok("noseed suffix instead of 'None'", out.name == "noseed-noseed.yaml",
       out.name)
    ok("meta seed is 'None'-free", "None" not in str(doc["meta"]["seed"]),
       str(doc["meta"]["seed"]))


def test_imported_at_is_the_source_mtime_not_now(tmp):
    p = make_map(tmp, include=("features", "burgs"), mtime=1000000000)
    out, doc, _ = import_to(tmp, p)
    ok("imported_at stamps the source file",
       doc["meta"]["imported_at"] == "2001-09-09",
       str(doc["meta"]["imported_at"]))


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
                           "color": "#fff", "neighbors": []}],
            "burgs": [0, {"i": 1, "name": "B", "port": False, "state": 1,
                          "population": 1.0, "x": 1, "y": 2}],
            "provinces": [0],
            "religions": [0],
        },
    }
    p = pathlib.Path(tmp) / "full.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    out, doc, missing = import_to(tmp, p)
    ok("json export: nothing missing", missing == [], str(missing))
    ok("json export: placeholders dropped", doc["summary"]["settlements"] == 1)


if __name__ == "__main__":
    for fn in [test_full_import, test_missing_sections_are_reported_not_silent,
               test_seedless_dump_gets_a_noseed_suffix,
               test_imported_at_is_the_source_mtime_not_now,
               test_cli_batch_survives_one_bad_file,
               test_json_export_reports_no_missing]:
        with tempfile.TemporaryDirectory() as d:
            fn(d)
    print()
    if FAILED:
        print(f"{len(FAILED)} test(s) failed: {FAILED}")
        sys.exit(1)
    print("all import_fmg tests passed")
