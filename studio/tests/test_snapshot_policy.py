"""Snapshot policy (ticket 47): what rides in canon snapshots and what
never does. `atlas-vtt/` is committed (tuned scenes are earned artifacts);
`.obsidian/` (at any vault depth) and forecast bundles are ignored — the
studio ensures the workspace .gitignore on the snapshot path, so
pre-existing workspaces gain it too. Real git in a tmp campaigns root, the
test_hardening pattern."""

import subprocess

import canon
import spine


def _git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60,
                          check=False)
    return proc.returncode, proc.stdout, proc.stderr


def _campaigns_root(tmp_path):
    root = tmp_path / "campaigns"
    camp = root / "testvale"
    (camp / "lore").mkdir(parents=True)
    (camp / "lore" / "timeline.yaml").write_text("entries: []\n",
                                                 encoding="utf-8")
    return root


def _tracked(root):
    rc, out, _ = _git(root, "ls-files")
    assert rc == 0
    return out.splitlines()


def test_atlas_vtt_files_are_canon_and_snapshot(tmp_path, monkeypatch):
    """A file hand-placed under the campaign's atlas-vtt/ rides in the next
    snapshot commit — tuned table artifacts are earned history."""
    root = _campaigns_root(tmp_path)
    monkeypatch.setattr(canon, "AVAILABLE", True)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    (root / "testvale" / "atlas-vtt" / "scenes").mkdir(parents=True)
    (root / "testvale" / "atlas-vtt" / "scenes" / "quay.atlasmap").write_text(
        "{}\n", encoding="utf-8")
    assert canon.snapshot("testvale", "campaign/testvale: scene tuned")
    assert "testvale/atlas-vtt/scenes/quay.atlasmap" in _tracked(root)


def test_obsidian_dirs_never_ride_in_snapshots(tmp_path, monkeypatch):
    """.obsidian/ is ignored at ANY depth: a campaign-root vault's config
    and a lore-folder vault's config alike (ticket 47's depth-agnostic
    line). Ignored files stay on disk — they just never commit."""
    root = _campaigns_root(tmp_path)
    monkeypatch.setattr(canon, "AVAILABLE", True)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    (root / "testvale" / ".obsidian").mkdir()
    (root / "testvale" / ".obsidian" / "workspace.json").write_text(
        "{}\n", encoding="utf-8")
    (root / "testvale" / "lore" / ".obsidian").mkdir()
    (root / "testvale" / "lore" / ".obsidian" / "app.json").write_text(
        "{}\n", encoding="utf-8")
    assert canon.snapshot("testvale", "campaign/testvale: first")
    tracked = _tracked(root)
    assert not any(".obsidian/" in t for t in tracked), tracked
    # on disk, untouched — ignored, not deleted
    assert (root / "testvale" / ".obsidian" / "workspace.json").is_file()
    assert (root / "testvale" / "lore" / ".obsidian" / "app.json").is_file()


def test_forecast_bundles_never_ride_in_snapshots(tmp_path, monkeypatch):
    """Forecasts are projections, not canon: forecasts.py has promised their
    git-ignoring since they shipped — pinned here so the promise stays
    true."""
    root = _campaigns_root(tmp_path)
    monkeypatch.setattr(canon, "AVAILABLE", True)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    (root / "testvale" / "simulation" / "forecasts" / "fc1").mkdir(
        parents=True)
    (root / "testvale" / "simulation" / "forecasts" / "fc1" / "bundle.yaml"
     ).write_text("weeks: []\n", encoding="utf-8")
    assert canon.snapshot("testvale", "campaign/testvale: week 1")
    assert not any("forecasts/" in t for t in _tracked(root))


def test_preexisting_workspace_gains_gitignore_on_next_snapshot(
        tmp_path, monkeypatch):
    """The ensure runs on the SNAPSHOT path, not at repo init: a workspace
    whose repo predates the policy still gets the lines (and an
    already-correct file is left byte-identical)."""
    root = _campaigns_root(tmp_path)
    monkeypatch.setattr(canon, "AVAILABLE", True)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    rc, _, err = _git(root, "init")
    assert rc == 0, err
    # a hand-inited repo has whatever identity its owner gave it; give it
    # one the way a real workspace would have (canon sets identity only on
    # its own init path — pre-existing behavior, not this ticket's)
    _git(root, "config", "user.name", "Snapshot Policy Test")
    _git(root, "config", "user.email", "policy@test")
    assert not (root / ".gitignore").exists()
    assert canon.snapshot("testvale", "campaign/testvale: first")
    text = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".obsidian/" in text.splitlines()
    assert "**/simulation/forecasts/" in text.splitlines()

    # idempotent: a second snapshot does not duplicate the lines
    before = text
    (root / "testvale" / "lore" / "timeline.yaml").write_text(
        "entries:\n  - id: t0001\n", encoding="utf-8")
    canon.snapshot("testvale", "campaign/testvale: second")
    assert (root / ".gitignore").read_text(encoding="utf-8") == before


def test_workspace_ignore_preserves_user_lines(tmp_path, monkeypatch):
    """The ensure APPENDS only what is missing — a .gitignore the user
    maintains keeps its own lines and its own text verbatim."""
    root = _campaigns_root(tmp_path)
    monkeypatch.setattr(canon, "AVAILABLE", True)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    (root / ".gitignore").write_text("*.log\n.obsidian/\n",
                                     encoding="utf-8")
    canon.snapshot("testvale", "campaign/testvale: first")
    text = (root / ".gitignore").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "*.log", "user lines keep their position"
    assert ".obsidian/" in lines
    assert "**/simulation/forecasts/" in lines
    assert lines.count(".obsidian/") == 1, "no duplicate of a present line"


def test_ignore_ensure_failure_never_blocks_the_snapshot(
        tmp_path, monkeypatch):
    """A .gitignore write problem is furniture, not canon: the snapshot
    proceeds (the VCS rule — a furniture failure never stands between the
    DM and their files)."""
    root = _campaigns_root(tmp_path)
    monkeypatch.setattr(canon, "AVAILABLE", True)
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)

    def explode(path, text):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(canon.spine, "write_text_atomic", explode)
    (root / "testvale" / "lore" / "timeline.yaml").write_text(
        "entries:\n  - id: t0001\n", encoding="utf-8")
    assert canon.snapshot("testvale", "campaign/testvale: despite furniture")
