"""Ticket 35 — the campaign-data root: the resolution rule, the subprocess
env propagation, legacy-dir detection, and the doctor migration.

The rule: $CAMPAIGN_DATA_ROOT if set, else <repo-root>/campaign-data —
resolved by spine.data_root() on the studio side and by the same rule in
the engine/battlemap CLIs (their side is pinned by the real-subprocess
tests in test_integration.py)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import doctor
import engines
import spine


# ----------------------------------------------------------------- the rule


def test_data_root_default_is_gitignored_repo_dir():
    assert spine.data_root() == spine.DATA_ROOT


def test_data_root_env_overrides_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMPAIGN_DATA_ROOT", str(tmp_path))
    assert spine.data_root() == tmp_path


def test_campaigns_root_derives_from_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(spine, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", tmp_path / "data" / "campaigns")
    assert spine.campaigns_root() == tmp_path / "data" / "campaigns"


def test_default_layout_names_the_ticket_dirs():
    """The module globals were derived from the default root at import: the
    ticket's layout, pinned so a typo cannot scatter the data again."""
    assert spine.CAMPAIGNS_ROOT == spine.DATA_ROOT / "campaigns"
    assert engines.ENGINE_HOME == spine.DATA_ROOT / "engine-home"
    assert engines.CHRONICLE_HOME == spine.DATA_ROOT / "chronicle"
    assert engines.week_flag_path() == engines.ENGINE_HOME / ".week-run-flag"
    assert engines.engine_home_lock_path() == \
        engines.ENGINE_HOME / ".engine-home.lock"
    assert engines.engine_state_path() == \
        engines.ENGINE_HOME / "state.yaml"
    assert engines.engine_state_backup_path() == \
        engines.ENGINE_HOME / "state.yaml.bak"
    assert engines.engine_replay_path(4) == \
        engines.ENGINE_HOME / "replay" / "week-4.yaml"
    assert engines.engine_chronicle_dir() == engines.CHRONICLE_HOME
    assert engines.map_sites_dir() == spine.DATA_ROOT / "maps" / "sites"
    assert engines.candidate_drop_dir() == \
        spine.DATA_ROOT / "scratch" / "map-candidates"


def test_engine_home_globals_relocate_every_helper(tmp_path, monkeypatch):
    home = tmp_path / "moved"
    chron = tmp_path / "moved-chronicle"
    monkeypatch.setattr(engines, "ENGINE_HOME", home)
    monkeypatch.setattr(engines, "CHRONICLE_HOME", chron)
    assert engines.engine_state_path() == home / "state.yaml"
    assert engines.week_flag_path() == home / ".week-run-flag"
    assert engines.engine_replay_path(2) == home / "replay" / "week-2.yaml"
    assert engines.engine_chronicle_dir() == chron


# --------------------------------------------------------- subprocess env


def test_run_py_hands_every_subprocess_the_data_root(tmp_path, monkeypatch):
    """The engine/battlemap CLIs do not import spine — the env set by
    engines._run is what keeps them on the studio's root (ticket 35)."""
    monkeypatch.delenv("CAMPAIGN_DATA_ROOT", raising=False)
    rc, lines = engines.run_py(
        tmp_path, "-c", "import os; print(os.environ['CAMPAIGN_DATA_ROOT'])")
    assert rc == 0
    assert lines[-1] == str(spine.data_root())


def test_run_py_caller_env_wins_over_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMPAIGN_DATA_ROOT", raising=False)
    rc, lines = engines.run_py(
        tmp_path, "-c", "import os; print(os.environ['CAMPAIGN_DATA_ROOT'])",
        env={"CAMPAIGN_DATA_ROOT": str(tmp_path / "explicit")})
    assert rc == 0
    assert lines[-1] == str(tmp_path / "explicit")


# ------------------------------------------------------ legacy dir detection


def _fabricate_legacy_tree(parent: Path) -> None:
    (parent / "campaigns" / "testvale").mkdir(parents=True)
    (parent / "agentic-simulator" / "world").mkdir(parents=True)
    (parent / "agentic-simulator" / "chronicle").mkdir(parents=True)
    (parent / "studio" / "logs").mkdir(parents=True)
    (parent / "dnd-mapping" / "worlds" / "sites").mkdir(parents=True)
    (parent / "dnd-mapping" / "out-gen-city-smoke").mkdir(parents=True)


def test_legacy_data_dirs_lists_only_existing(tmp_path):
    _fabricate_legacy_tree(tmp_path)
    data = tmp_path / "campaign-data"
    legacy = engines.legacy_data_dirs(tmp_path, data)
    olds = [old.name for old, _ in legacy]
    assert olds == ["campaigns", "world", "chronicle", "logs", "sites",
                    "out-gen-city-smoke"]
    assert legacy[0] == (tmp_path / "campaigns", data / "campaigns")
    assert legacy[1] == (tmp_path / "agentic-simulator" / "world",
                         data / "engine-home")


def test_legacy_data_dirs_empty_after_migration(tmp_path):
    assert engines.legacy_data_dirs(tmp_path, tmp_path / "campaign-data") == []


def test_legacy_data_dirs_skips_candidates_by_design(tmp_path):
    """worlds/candidates survives the migration with its tracked half — it
    must never show up as a to-move legacy dir."""
    (tmp_path / "dnd-mapping" / "worlds" / "candidates").mkdir(parents=True)
    assert engines.legacy_data_dirs(tmp_path, tmp_path / "campaign-data") == []


# --------------------------------------------------------------- migration


def _git(repo: Path, *args) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


def _fabricate_migratable_repo(tmp_path: Path) -> Path:
    """A legacy checkout with one campaign (nested git repo), one tracked
    curated candidate, one untracked scratch candidate, and the rest of
    the legacy dirs."""
    _fabricate_legacy_tree(tmp_path)
    camp = tmp_path / "campaigns" / "testvale"
    _git(camp, "init", "-q")
    (camp / "campaign.yaml").write_text("name: testvale\n", encoding="utf-8")
    _git(camp, "add", "-A")
    _git(camp, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "canon")

    dnd = tmp_path / "dnd-mapping"
    _git(dnd, "init", "-q")
    cand = dnd / "worlds" / "candidates"
    cand.mkdir(parents=True)
    (cand / "curated.yaml").write_text("meta: curated\n", encoding="utf-8")
    (cand / "index.md").write_text("# candidates\n", encoding="utf-8")
    (cand / "dropped.yaml").write_text("meta: scratch\n", encoding="utf-8")
    _git(dnd, "add", "worlds/candidates/curated.yaml",
         "worlds/candidates/index.md")
    _git(dnd, "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "-qm", "curated")
    return tmp_path


def test_migrate_moves_legacy_dirs_and_keeps_git_history(tmp_path, capsys):
    _fabricate_migratable_repo(tmp_path)
    data = tmp_path / "campaign-data"
    rc = doctor.migrate_data_root(parent=tmp_path, data_root=data)
    assert rc == 0
    out = capsys.readouterr().out

    assert (data / "campaigns" / "testvale" / "campaign.yaml").is_file()
    assert (data / "engine-home").is_dir()
    assert (data / "chronicle").is_dir()
    assert (data / "logs").is_dir()
    assert (data / "maps" / "sites").is_dir()
    assert (data / "scratch" / "gen-city-smoke").is_dir()
    # the old locations are gone
    assert not (tmp_path / "campaigns").exists()
    assert not (tmp_path / "agentic-simulator" / "world").exists()
    # the nested campaign git history survived the move
    log = subprocess.run(
        ["git", "-C", str(data / "campaigns" / "testvale"), "log",
         "--oneline"], capture_output=True, text=True, check=True)
    assert "canon" in log.stdout
    assert "moved" in out

    # candidates: tracked curated files stayed, scratch moved
    cand = tmp_path / "dnd-mapping" / "worlds" / "candidates"
    assert (cand / "curated.yaml").is_file()
    assert (cand / "index.md").is_file()
    assert not (cand / "dropped.yaml").exists()
    assert (data / "scratch" / "map-candidates" / "dropped.yaml").is_file()


def test_migrate_is_idempotent(tmp_path, capsys):
    _fabricate_migratable_repo(tmp_path)
    data = tmp_path / "campaign-data"
    assert doctor.migrate_data_root(parent=tmp_path, data_root=data) == 0
    capsys.readouterr()
    assert doctor.migrate_data_root(parent=tmp_path, data_root=data) == 0
    assert "nothing to migrate" in capsys.readouterr().out


def test_migrate_refused_while_campaign_lock_held(tmp_path, capsys):
    _fabricate_migratable_repo(tmp_path)
    data = tmp_path / "campaign-data"
    locks = tmp_path / "campaigns" / ".locks"
    locks.mkdir()
    fh, held = spine.try_file_lock(locks / "testvale.lock")
    try:
        assert held is False  # we hold it now
        assert doctor.migrate_data_root(parent=tmp_path, data_root=data) == 1
        assert "campaign lock" in capsys.readouterr().out
        # nothing moved
        assert (tmp_path / "campaigns" / "testvale").is_dir()
        assert not data.exists()
    finally:
        spine.release_file_lock(fh)


def test_migrate_refused_while_week_flag_left_behind(tmp_path, capsys):
    _fabricate_migratable_repo(tmp_path)
    flag = tmp_path / "agentic-simulator" / "world" / ".week-run-flag"
    flag.write_text("{}\n", encoding="utf-8")
    assert doctor.migrate_data_root(parent=tmp_path,
                                    data_root=tmp_path / "campaign-data") == 1
    assert "week run" in capsys.readouterr().out
    assert (tmp_path / "campaigns").is_dir()


def test_migrate_refused_when_target_exists(tmp_path, capsys):
    """Never merges: a pre-existing target dir is a hard refusal."""
    _fabricate_migratable_repo(tmp_path)
    data = tmp_path / "campaign-data"
    (data / "campaigns").mkdir(parents=True)
    assert doctor.migrate_data_root(parent=tmp_path, data_root=data) == 1
    assert "already exist" in capsys.readouterr().out
    assert (tmp_path / "campaigns" / "testvale").is_dir()


def test_migrate_fresh_checkout_is_a_noop(tmp_path, capsys):
    assert doctor.migrate_data_root(parent=tmp_path,
                                    data_root=tmp_path / "campaign-data") == 0
    assert "nothing to migrate" in capsys.readouterr().out


# ---------------------------------------------------------------- the check


def test_check_data_root_names_legacy_dirs(tmp_path, monkeypatch):
    _fabricate_legacy_tree(tmp_path)
    monkeypatch.setattr(engines, "PARENT", tmp_path)
    monkeypatch.setattr(spine, "DATA_ROOT", tmp_path / "campaign-data")
    check = doctor.check_data_root()
    assert check["ok"] is False
    assert "legacy data dirs found" in check["detail"]
    assert "--migrate-data-root" in check["fix"]


def test_check_data_root_clean_after_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "PARENT", tmp_path)
    monkeypatch.setattr(spine, "DATA_ROOT", tmp_path / "campaign-data")
    check = doctor.check_data_root()
    assert check["ok"] is True


def test_boot_banner_fires_on_legacy_dirs(tmp_path, monkeypatch, caplog):
    """The lifespan logs the migration pointer when legacy dirs exist —
    loudly, but nothing moves on boot (ticket 35 ruling)."""
    import logging

    import server

    _fabricate_legacy_tree(tmp_path)
    monkeypatch.setattr(engines, "PARENT", tmp_path)
    monkeypatch.setattr(spine, "DATA_ROOT", tmp_path / "campaign-data")
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT",
                        tmp_path / "campaign-data" / "campaigns")
    with caplog.at_level(logging.WARNING, logger="studio.server"):
        from fastapi.testclient import TestClient
        with TestClient(server.app):
            pass
    assert any("--migrate-data-root" in rec.message and
               "LEGACY DATA LOCATIONS" in rec.message
               for rec in caplog.records), caplog.text
    # nothing migrated itself
    assert (tmp_path / "campaigns").is_dir()
    assert not (tmp_path / "campaign-data" / "campaigns").exists()
