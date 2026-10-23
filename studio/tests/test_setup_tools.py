"""setup-tools.py --check: the pnpm-drift detector (P3-39) and the
ticket-60 pin verification (tag + vendor-patch freshness).

The npm-only contract (movability: npm's flat node_modules has no
junctions) is checked, not assumed — a tree can carry every marker file
--check looks for and still be pnpm-shaped (everything symlinked into
node_modules/.pnpm). Ticket 60 added the other half markers never proved:
the FMG checkout must be at the pinned tag and the installed headless
runner byte-identical to the tracked vendor-patches copy. These tests
build minimal fake tool trees in tmp and drive the real check loop; the
live trees under dnd-mapping/tools/ are git-ignored, so a worktree starts
without them and the fakes stand in.
"""

import importlib.util
import json
from pathlib import Path

PARENT = Path(__file__).resolve().parents[2]  # repo root (or worktree)

_spec = importlib.util.spec_from_file_location(
    "setup_tools", PARENT / "setup-tools.py")
setup_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_tools)


def _fake_tools(root):
    """Both tool trees with every marker the done() checks look for,
    npm-shaped (no pnpm artifacts), the FMG tree at the pin: package.json
    carries FMG_TAG (no real git repo to describe) and the installed
    runner is byte-identical to the tracked vendor-patches copy."""
    tools = root / "tools"
    fmg = tools / "Fantasy-Map-Generator"
    (fmg / ".git").mkdir(parents=True)
    (fmg / "src" / "generators").mkdir(parents=True)
    (fmg / "src" / "generators" / "headless-export.test.ts").write_bytes(
        (PARENT / "dnd-mapping" / "vendor-patches" / "fmg"
         / "headless-export.test.ts").read_bytes())
    (fmg / "package.json").write_text(
        json.dumps({"version": setup_tools.FMG_TAG}), encoding="utf-8")
    (fmg / "node_modules" / "vitest").mkdir(parents=True)
    (fmg / "node_modules" / "vitest" / "vitest.mjs").write_text(
        "", encoding="utf-8")
    settle = tools / "settlemaker"
    (settle / ".git").mkdir(parents=True)
    (settle / "dist").mkdir()
    (settle / "dist" / "index.js").write_text("", encoding="utf-8")
    (settle / "node_modules" / "sharp").mkdir(parents=True)
    (settle / "node_modules" / "sharp" / "package.json").write_text(
        "{}", encoding="utf-8")
    return tools


def _make_pnpm_shaped(tree):
    """The drift P3-39 found in the wild: real marker files present AND a
    symlinked .pnpm store behind them."""
    (tree / "node_modules" / ".pnpm" / "sharp@0.33.5").mkdir(parents=True)
    (tree / "node_modules" / ".modules.yaml").write_text("", encoding="utf-8")
    (tree / "pnpm-lock.yaml").write_text("", encoding="utf-8")


def test_pnpm_drift_lists_the_artifacts(tmp_path):
    tree = tmp_path / "settlemaker"
    tree.mkdir()
    assert setup_tools.pnpm_drift(tree) == []  # nothing installed: no drift
    (tree / "node_modules").mkdir()
    (tree / "node_modules" / "sharp").mkdir()  # npm-shaped: markers only
    assert setup_tools.pnpm_drift(tree) == []
    _make_pnpm_shaped(tree)
    assert sorted(setup_tools.pnpm_drift(tree)) == sorted(
        setup_tools.PNPM_ARTIFACTS)


def test_check_passes_on_npm_shaped_trees(tmp_path, monkeypatch, capsys):
    tools = _fake_tools(tmp_path)
    monkeypatch.setattr(setup_tools, "TOOLS", tools)
    assert setup_tools.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "[done]" in out and "pnpm" not in out


def test_check_fails_on_a_pnpm_shaped_tree(tmp_path, monkeypatch, capsys):
    tools = _fake_tools(tmp_path)
    _make_pnpm_shaped(tools / "settlemaker")  # markers all present, still bad
    monkeypatch.setattr(setup_tools, "TOOLS", tools)
    assert setup_tools.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "[pnpm]" in out and "settlemaker" in out
    assert ".pnpm" in out  # the evidence is named
    assert "Fantasy-Map-Generator" not in out.split("[pnpm]")[1]


def test_setup_mode_removes_a_pnpm_install(tmp_path, monkeypatch):
    """Non-check mode repairs instead of refusing: the pnpm install is
    deleted first, then the normal setup path reinstalls with npm. The
    reinstall itself is monkeypatched away here — the removal is the
    unit under test."""
    tools = _fake_tools(tmp_path)
    settle = tools / "settlemaker"
    _make_pnpm_shaped(settle)
    applied = []

    def fake_apply():
        applied.append("settle")

    monkeypatch.setattr(setup_tools, "TOOLS", tools)
    monkeypatch.setattr(setup_tools, "STEPS", [
        ("settlemaker (cities + rasterizer)", "settlemaker",
         setup_tools.settle_done, fake_apply, None)])
    assert setup_tools.main([]) == 0
    assert applied == ["settle"]
    assert setup_tools.pnpm_drift(settle) == []
    assert not (settle / "node_modules").exists()  # npm starts clean
    assert not (settle / "pnpm-lock.yaml").exists()


# --------------------------------------------- ticket 60: pin verification

def test_check_catches_a_stale_patch(tmp_path, monkeypatch, capsys):
    """Ticket 60 AC: --check must fail when the installed headless runner
    no longer matches the tracked vendor-patches copy (hash mismatch).
    Before ticket 60 the runner was copied only on a fresh clone — once
    the file existed, content was never compared, so a fix committed to
    vendor-patches/ changed regeneration only on fresh clones."""
    tools = _fake_tools(tmp_path)
    installed = (tools / "Fantasy-Map-Generator" / "src" / "generators"
                 / "headless-export.test.ts")
    installed.write_text("// drifted from the tracked vendor copy\n",
                         encoding="utf-8")
    monkeypatch.setattr(setup_tools, "TOOLS", tools)
    assert setup_tools.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "[stale]" in out and "vendor-patches" in out
    assert "Fantasy-Map-Generator" in out
    # the finding is FMG's alone — settlemaker still reports done
    assert "[done]     settlemaker" in out


def test_check_catches_tag_drift(tmp_path, monkeypatch, capsys):
    """The other ticket-60 finding: every marker green and the checkout
    still not at the pinned tag (a git pull under tools/). The fake tree
    has no real git repo, so the resolver falls back to package.json —
    exactly the drift shape --check must name."""
    tools = _fake_tools(tmp_path)
    (tools / "Fantasy-Map-Generator" / "package.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8")
    monkeypatch.setattr(setup_tools, "TOOLS", tools)
    assert setup_tools.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "[stale]" in out and "9.9.9" in out and "1.153.1" in out


def test_setup_mode_re_copies_a_stale_runner(tmp_path, monkeypatch, capsys):
    """The repair half: plain setup mode re-copies the tracked runner into
    an existing tree (offline-safe — it is our own file), so a vendor
    patch fix reaches machines that already ran setup once, not only
    fresh clones."""
    tools = _fake_tools(tmp_path)
    fmg = tools / "Fantasy-Map-Generator"
    installed = fmg / "src" / "generators" / "headless-export.test.ts"
    installed.write_text("// drifted\n", encoding="utf-8")
    monkeypatch.setattr(setup_tools, "TOOLS", tools)
    assert setup_tools.main([]) == 0
    out = capsys.readouterr().out
    assert "[refresh]" in out
    tracked = (PARENT / "dnd-mapping" / "vendor-patches" / "fmg"
               / "headless-export.test.ts")
    assert installed.read_bytes() == tracked.read_bytes()
    assert not setup_tools.fmg_runner_stale(fmg)
