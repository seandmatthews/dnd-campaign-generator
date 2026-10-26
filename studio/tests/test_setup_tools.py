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
import subprocess
from pathlib import Path

import pytest

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


# ----------------------------------- ticket 67: the pin is the commit SHA
#
# A tag is a movable pointer server-side: a checkout cloned after a tag
# move sits at a different commit that `git describe --tags` still
# reports by the tag's own name. The pin is therefore FMG_REV — the
# tag's commit — and a tree counts as fresh only when its HEAD matches
# by SHA, not when the version string agrees.

def _git_init_at_commit(repo):
    """A real one-commit git repo at `repo` (git is a setup-tools
    prerequisite); returns its HEAD SHA."""
    repo.mkdir(parents=True, exist_ok=True)

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    git("init", "-q")
    (repo / "file.txt").write_text("x", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example",
        "commit", "-q", "-m", "a commit")
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          check=True, capture_output=True,
                          text=True).stdout.strip()


def test_fmg_pin_is_a_commit_sha_with_the_tag_recorded_beside_it():
    """The pin must be a full commit SHA (a tag can move server-side);
    the human-readable tag stays recorded beside it."""
    assert len(setup_tools.FMG_REV) == 40
    assert all(c in "0123456789abcdef" for c in setup_tools.FMG_REV)
    assert setup_tools.FMG_TAG == "1.153.1"


def test_checkout_commit_resolves_a_real_clone(tmp_path):
    d = tmp_path / "clone"
    head = _git_init_at_commit(d)
    assert setup_tools._checkout_commit(d) == head


def test_checkout_commit_reads_the_head_file_when_git_cannot(tmp_path):
    """The offline fallback: a --branch tag clone sits on a detached HEAD
    whose .git/HEAD IS the raw SHA, so the check works even with git
    missing or hung. (An empty .git dir — the fake trees above — resolves
    to None and never lets git answer from some OUTER repository.)"""
    d = tmp_path / "clone"
    (d / ".git").mkdir(parents=True)
    (d / ".git" / "HEAD").write_text(
        setup_tools.FMG_REV + "\n", encoding="ascii")
    assert setup_tools._checkout_commit(d) == setup_tools.FMG_REV
    (d / ".git" / "HEAD").write_text("ref: refs/heads/main\n",
                                     encoding="ascii")
    assert setup_tools._checkout_commit(d) is None  # a ref needs git
    assert setup_tools._checkout_commit(tmp_path / "empty-git-dir") is None


def test_check_catches_a_moved_tag_even_when_describe_agrees(
        tmp_path, monkeypatch, capsys):
    """The ticket-67 finding: every marker green, `git describe --tags`
    reporting exactly the pinned TAG name — and HEAD still not the pinned
    commit (the checkout was cloned after the tag moved server-side).
    The version-string check is green here by construction; the SHA
    check must refuse it."""
    tools = _fake_tools(tmp_path)
    fmg = tools / "Fantasy-Map-Generator"
    head = _git_init_at_commit(fmg)
    subprocess.run(["git", "-C", str(fmg), "tag", setup_tools.FMG_TAG],
                   check=True, capture_output=True)
    # the old check's blind spot, exactly: describe answers the tag name
    assert setup_tools._checkout_version(fmg) == setup_tools.FMG_TAG
    assert head != setup_tools.FMG_REV
    monkeypatch.setattr(setup_tools, "TOOLS", tools)
    assert setup_tools.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "[stale]" in out
    assert head[:12] in out and setup_tools.FMG_REV[:12] in out


def test_check_passes_when_head_is_the_pinned_commit(
        tmp_path, monkeypatch, capsys):
    """The SHA decides: a tree whose version fallback would scream
    (package.json says 9.9.9) is fresh when HEAD equals FMG_REV."""
    tools = _fake_tools(tmp_path)
    (tools / "Fantasy-Map-Generator" / "package.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8")
    monkeypatch.setattr(setup_tools, "TOOLS", tools)
    monkeypatch.setattr(setup_tools, "_checkout_commit",
                        lambda d: setup_tools.FMG_REV)
    assert setup_tools.main(["--check"]) == 0
    assert "[done]" in capsys.readouterr().out


def test_fresh_clone_refuses_a_moved_tag(tmp_path, monkeypatch, capsys):
    """The clone leg verifies HEAD against the SHA pin before anything
    else runs: a tag moved server-side between the pin and the clone
    must stop setup, not seed a new machine with a different world."""
    monkeypatch.setattr(setup_tools, "TOOLS", tmp_path)
    target = setup_tools.TOOLS / "Fantasy-Map-Generator"

    def fake_run(cmd, *a, **k):
        if cmd[0] == "git" and cmd[1] == "clone":
            # what upstream serves after a tag move: a DIFFERENT commit,
            # tagged with the pin's own name
            head = _git_init_at_commit(target)
            subprocess.run(["git", "-C", str(target), "tag",
                            setup_tools.FMG_TAG], check=True,
                           capture_output=True)
            assert head != setup_tools.FMG_REV
        return 0, ""

    monkeypatch.setattr(setup_tools, "_run", fake_run)
    with pytest.raises(SystemExit):
        setup_tools.fmg_apply()
    out = capsys.readouterr().out
    assert setup_tools.FMG_REV in out and "moved server-side" in out
