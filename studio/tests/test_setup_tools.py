"""setup-tools.py --check: the pnpm-drift detector (P3-39).

The npm-only contract (movability: npm's flat node_modules has no
junctions) is checked, not assumed — a tree can carry every marker file
--check looks for and still be pnpm-shaped (everything symlinked into
node_modules/.pnpm). These tests build minimal fake tool trees in tmp and
drive the real check loop; the live trees under dnd-mapping/tools/ are
git-ignored, so a worktree starts without them and the fakes stand in.
"""

import importlib.util
from pathlib import Path

PARENT = Path(__file__).resolve().parents[2]  # repo root (or worktree)

_spec = importlib.util.spec_from_file_location(
    "setup_tools", PARENT / "setup-tools.py")
setup_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_tools)


def _fake_tools(root):
    """Both tool trees with every marker the done() checks look for,
    npm-shaped (no pnpm artifacts)."""
    tools = root / "tools"
    fmg = tools / "Fantasy-Map-Generator"
    (fmg / ".git").mkdir(parents=True)
    (fmg / "src" / "generators").mkdir(parents=True)
    (fmg / "src" / "generators" / "headless-export.test.ts").write_text(
        "", encoding="utf-8")
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
         setup_tools.settle_done, fake_apply)])
    assert setup_tools.main([]) == 0
    assert applied == ["settle"]
    assert setup_tools.pnpm_drift(settle) == []
    assert not (settle / "node_modules").exists()  # npm starts clean
    assert not (settle / "pnpm-lock.yaml").exists()
