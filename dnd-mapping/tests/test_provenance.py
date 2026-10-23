"""Ticket 60 — regeneration determinism's provenance stamps.

The repo claims "everything regenerated from a recorded seed" while the
version stamps were literals that survived any git pull under tools/, and
the vendor patch reached only fresh clones. These tests pin the fix's
Python half behaviorally (a faked `git describe --tags` must become the
stamp — argv and cwd intercepted where the resolver actually calls git)
and the JS half textually (the fast tier cannot execute the TS runner or
gen-city.mjs; the integration tier does that on tool-equipped machines).
Zero dependencies; run directly:  python tests/test_provenance.py
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root (or worktree)
MAP = ROOT / "dnd-mapping"

_spec = importlib.util.spec_from_file_location(
    "setup_tools", ROOT / "setup-tools.py")
setup_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_tools)

FAILED = []


def ok(name, cond, detail=""):
    print(f"  {name}: {'OK' if cond else 'FAIL ' + detail}")
    if not cond:
        FAILED.append(name)


class _FakeProc:
    """A subprocess.CompletedProcess stand-in for a faked git."""

    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _fake_tree(tmp, version="0.0.1-fallback", with_git=True):
    # idempotent: the direct-run __main__ loop shares one tmp tree
    tree = pathlib.Path(tmp) / "Fantasy-Map-Generator"
    (tree / "src" / "generators").mkdir(parents=True, exist_ok=True)
    if with_git:
        (tree / ".git").mkdir(exist_ok=True)
    elif (tree / ".git").exists():  # a previous test in the shared tmp
        import shutil
        shutil.rmtree(tree / ".git")
    (tree / "package.json").write_text(json.dumps({"version": version}),
                                       encoding="utf-8")
    return tree


def test_checkout_version_stamps_from_git_describe(tmp):
    """AC1: the stamp comes from the checkout, not a literal. A faked
    `git describe --tags` answering 9.9.9-faketag must land in the version
    verbatim (the package.json fallback must NOT win while git answers),
    and the resolver must ask git exactly that, in the tree itself."""
    tree = _fake_tree(tmp)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = list(cmd)
        seen["cwd"] = kw.get("cwd")
        if list(cmd)[:3] == ["git", "describe", "--tags"]:
            return _FakeProc(0, "9.9.9-faketag\n")
        return subprocess.run(cmd, **kw)  # not our call: the real one

    real_run = subprocess.run
    subprocess.run = fake_run
    try:
        v = setup_tools._checkout_version(tree)
    finally:
        subprocess.run = real_run
    ok("the resolver asks git describe --tags in the tree",
       seen.get("cmd") == ["git", "describe", "--tags"]
       and str(seen.get("cwd")) == str(tree), str(seen))
    ok("the checkout's describe output is the stamp",
       v == "9.9.9-faketag", repr(v))


def test_checkout_version_falls_back_to_package_json(tmp):
    """git failing (no repo here / git missing / rc != 0) falls back to the
    clone's recorded package.json version — the ticket's fallback path."""
    tree = _fake_tree(tmp, version="1.153.1")

    def fake_run(cmd, **kw):
        return _FakeProc(128, "")

    real_run = subprocess.run
    subprocess.run = fake_run
    try:
        v = setup_tools._checkout_version(tree)
    finally:
        subprocess.run = real_run
    ok("package.json version is the fallback", v == "1.153.1", repr(v))


def test_checkout_version_never_leaks_an_outer_repo(tmp):
    """A tree WITHOUT .git must not run git at all: git walks parent
    directories for a repository, and a tmp tree sitting inside some outer
    repo would get that repo's tags as a fake answer."""
    tree = _fake_tree(tmp, with_git=False)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = list(cmd)
        return _FakeProc(0, "8.8.8-outer\n")

    real_run = subprocess.run
    subprocess.run = fake_run
    try:
        v = setup_tools._checkout_version(tree)
    finally:
        subprocess.run = real_run
    ok("git is never asked outside a checkout", "cmd" not in seen, str(seen))
    ok("the package record answers instead", v == "0.0.1-fallback", repr(v))


def test_checkout_version_none_when_nothing_resolves(tmp):
    tree = pathlib.Path(tmp) / "empty"
    tree.mkdir()
    ok("no .git, no package.json -> None (unknown, never a fake match)",
       setup_tools._checkout_version(tree) is None)


def test_headless_runner_stamps_from_the_checkout():
    """AC1's runner half, pinned at the source: the version literal is
    gone and the stamp is computed from the checkout at run time (git
    describe first, package.json fallback)."""
    src = (MAP / "vendor-patches" / "fmg" / "headless-export.test.ts"
           ).read_text(encoding="utf-8")
    ok('the version literal "1.153.1-headless" is gone',
       'version: "1.153.1-headless"' not in src)
    ok("the stamp resolves from the checkout at run time",
       '["describe", "--tags"]' in src and "package.json" in src
       and "checkoutVersion()" in src)


def test_gencity_params_stamp_settlemaker_from_the_checkout():
    """Same discipline for the city half: gen-city.mjs's params.yaml must
    not carry the settlemaker_version literal — it reads the tool tree's
    package.json at run time."""
    src = (MAP / "scripts" / "gen-city.mjs").read_text(encoding="utf-8")
    ok("the settlemaker_version: 3.0.1 literal is gone",
       "settlemaker_version: 3.0.1" not in src)
    ok("the version is read from the checkout at run time",
       "readSettlemakerVersion" in src
       and 'join(ROOT, "tools", "settlemaker", "package.json")' in src)


def test_setup_tools_detects_a_stale_runner(tmp):
    """The ticket-60 detector behind `--check` (the studio-side end-to-end
    probe lives in studio/tests/test_setup_tools.py): a runner whose bytes
    differ from the tracked vendor copy is stale; a byte-identical copy
    is fresh."""
    tree = _fake_tree(tmp)
    tracked = MAP / "vendor-patches" / "fmg" / "headless-export.test.ts"
    installed = tree / "src" / "generators" / "headless-export.test.ts"
    installed.write_text("// drifted from the tracked copy\n",
                         encoding="utf-8")
    ok("a drifted runner is stale", setup_tools.fmg_runner_stale(tree))
    installed.write_bytes(tracked.read_bytes())
    ok("a byte-identical runner is fresh",
       not setup_tools.fmg_runner_stale(tree))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        for fn in [test_checkout_version_stamps_from_git_describe,
                   test_checkout_version_falls_back_to_package_json,
                   test_checkout_version_never_leaks_an_outer_repo,
                   test_checkout_version_none_when_nothing_resolves,
                   test_setup_tools_detects_a_stale_runner]:
            fn(d)
    for fn in [test_headless_runner_stamps_from_the_checkout,
               test_gencity_params_stamp_settlemaker_from_the_checkout]:
        fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} test(s) failed: {FAILED}")
        sys.exit(1)
    print("all provenance tests passed")
