"""Ticket 51 pins: the payload split's pure seam.

The builder packs one stage three ways (full exe payload, runtime pack,
tools pack, app payload). Ownership lives in ONE pure mapping —
release/build_release.py's payload_plan — which the pack step and the
post-pack self-check both read, so the packed sets cannot disagree with
the plan. These tests pin the mapping itself (disjoint, total, prefix
traps) and the stage walker's agreement with the pack's -xr! strip
rules. The 7z-level behavior (exclusion semantics, -slt parse) is
verified at build time by the builder's own self-check against the real
archives — the fast tier must not depend on release/cache.
"""

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "build_release_module", REPO / "release" / "build_release.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)


def test_plan_is_disjoint_and_total():
    files = ["VERSION", "llm.env", "studio/server.py",
             "runtime/python/python.exe", "runtime/node/node.exe",
             "dnd-mapping/tools/fmg/index.html",
             "dnd-mapping/tools/settlemaker/node_modules/x/y.js",
             "dnd-mapping/src/battlemap.py",
             "campaign-data/campaigns/.locks/keep"]
    plan = br.payload_plan(files)
    assert set(plan) == {"runtime", "tools", "app"}
    union = set().union(*plan.values())
    assert union == set(files), "every file must land in exactly one payload"
    assert len(union) == len(files), "no file may land in two payloads"
    assert plan["runtime"] == {"runtime/python/python.exe",
                               "runtime/node/node.exe"}
    assert plan["tools"] == {"dnd-mapping/tools/fmg/index.html",
                             "dnd-mapping/tools/settlemaker/node_modules/x/y.js"}
    assert plan["app"] == {"VERSION", "llm.env", "studio/server.py",
                           "dnd-mapping/src/battlemap.py",
                           "campaign-data/campaigns/.locks/keep"}


def test_plan_prefix_traps():
    # only the exact subtrees 'runtime/' and 'dnd-mapping/tools/' are
    # theirs — lookalike names stay app
    plan = br.payload_plan([
        "dnd-mapping/toolbox/b.js",     # not the tools subtree
        "dnd-mapping/tools",            # the dir entry itself (a file so
                                        # named would still be app — only
                                        # dnd-mapping/tools/ CHILDREN are)
        "runtimeless/x",                # 'runtime' prefix, not the subtree
        "studio/runtime/x.txt",         # a DEEP dir named runtime
        "studio/tools/y.txt",           # a deep dir named tools
    ])
    assert plan["runtime"] == set() and plan["tools"] == set()
    assert plan["app"] == {"dnd-mapping/toolbox/b.js", "dnd-mapping/tools",
                           "runtimeless/x", "studio/runtime/x.txt",
                           "studio/tools/y.txt"}


def test_plan_normalizes_windows_separators():
    plan = br.payload_plan(["runtime\\python\\python.exe",
                            "dnd-mapping\\tools\\fmg\\x.js",
                            "studio\\server.py"])
    assert plan["runtime"] == {"runtime/python/python.exe"}
    assert plan["tools"] == {"dnd-mapping/tools/fmg/x.js"}
    assert plan["app"] == {"studio/server.py"}


def test_stage_files_walks_and_strips_like_the_pack(tmp_path):
    # the walker's output feeds the plan that the packed archives are
    # checked against — it must carry every real file and strip exactly
    # what 7z's -xr! names strip (__pycache__, *.pyc), nothing else
    (tmp_path / "runtime" / "python").mkdir(parents=True)
    (tmp_path / "runtime" / "python" / "python.exe").write_text("p")
    (tmp_path / "studio" / "__pycache__").mkdir(parents=True)
    (tmp_path / "studio" / "__pycache__" / "server.cpython-311.pyc").write_text("c")
    (tmp_path / "studio" / "server.py").write_text("s")
    (tmp_path / "notes.pyc").write_text("n")          # a bare .pyc file
    (tmp_path / "not-pycache").mkdir(parents=True)
    (tmp_path / "not-pycache" / "keep.py").write_text("k")
    files = br.stage_files(tmp_path)
    assert files == {"runtime/python/python.exe", "studio/server.py",
                     "not-pycache/keep.py"}
    plan = br.payload_plan(files)
    assert plan["runtime"] == {"runtime/python/python.exe"}
    assert plan["app"] == {"studio/server.py", "not-pycache/keep.py"}


def test_tools_rev_is_derived_from_the_pin_source():
    # the tools-pack name must be derived from setup-tools.py's pins
    # (the single source of truth), never restated — this only pins the
    # SHAPE so a pin move cannot silently produce an unparseable name
    assert re.fullmatch(r"fmg-.+-[0-9a-f]{7}-sharp-.+", br.TOOLS_REV), br.TOOLS_REV
    assert br.TOOLS_REV.startswith("fmg-")


def test_empty_stage_plans_to_empty():
    plan = br.payload_plan([])
    assert plan == {"runtime": set(), "tools": set(), "app": set()}
