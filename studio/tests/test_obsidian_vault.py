"""obsidian_vault: the one-click vault registration (ticket 45).

The service merges ONE entry into Obsidian's own config (OBSIDIAN_CONFIG
overrides the real path in tests): idempotent, backup first, never a
clobber — a corrupt config is refused loudly, and the read side answers
False rather than blowing up the authoring card."""

import json
import os

import pytest

import obsidian_vault


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """Point OBSIDIAN_CONFIG at a throwaway file, return its path."""
    p = tmp_path / "obsidian.json"
    monkeypatch.setenv("OBSIDIAN_CONFIG", str(p))
    return p


def test_unregistered_when_config_missing(cfg):
    assert not cfg.exists()
    assert obsidian_vault.is_registered(str(cfg.parent / "lore")) is False


def test_register_creates_entry_and_reads_back(cfg):
    lore = str(cfg.parent / "lore")
    out = obsidian_vault.register(lore)
    assert out["registered"] is True and out["already"] is False
    assert cfg.exists()
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    (entry,) = doc["vaults"].values()
    assert entry["path"] == lore
    assert isinstance(entry["ts"], int)
    assert entry["open"] is False          # never steals focus
    assert obsidian_vault.is_registered(lore) is True


def test_register_is_idempotent(cfg):
    lore = str(cfg.parent / "lore")
    obsidian_vault.register(lore)
    again = obsidian_vault.register(lore)
    assert again["already"] is True
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert len(doc["vaults"]) == 1


def test_register_merges_and_backs_up_existing_vaults(cfg):
    other = {"vaults": {"cafebabe01020304": {
        "path": str(cfg.parent / "other-vault"), "ts": 1, "open": True}}}
    cfg.write_text(json.dumps(other), encoding="utf-8")
    obsidian_vault.register(str(cfg.parent / "lore"))
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert {v["path"] for v in doc["vaults"].values()} == {
        str(cfg.parent / "other-vault"), str(cfg.parent / "lore")}
    # the pre-write config survives as a backup generation
    (backup,) = obsidian_vault.backups(cfg)
    assert set(json.loads(
        backup.read_text(encoding="utf-8"))["vaults"]) == {"cafebabe01020304"}


def test_register_normalizes_the_path(cfg):
    lore = str(cfg.parent / "lore")
    obsidian_vault.register(lore)
    # doubled separators and a trailing one are the same folder to normpath
    assert obsidian_vault.is_registered(
        str(cfg.parent) + os.sep + os.sep + "lore" + os.sep) is True


# ---------------- ticket 64: the backups are generations, not one slot


def test_two_registrations_leave_both_generations_recoverable(cfg):
    """(ticket 64) the backup used to be ONE fixed name: the second
    registration overwrote the only copy of the ORIGINAL vault list, and
    the original was unrecoverable. Every registration now stamps its own
    generation — both stay recoverable."""
    original = {"vaults": {"aaaa000000000001": {
        "path": "V:/Original", "ts": 1, "open": False}}}
    cfg.write_text(json.dumps(original), encoding="utf-8")
    obsidian_vault.register(str(cfg.parent / "one"))
    obsidian_vault.register(str(cfg.parent / "two"))

    gens = [json.loads(p.read_text(encoding="utf-8"))
            for p in obsidian_vault.backups(cfg)]  # oldest first
    assert len(gens) == 2
    # generation 1 is the ORIGINAL list, untouched by the second register
    assert {v["path"] for v in gens[0]["vaults"].values()} == {"V:/Original"}
    # generation 2 is the one-registration list
    assert {v["path"] for v in gens[1]["vaults"].values()} == {
        "V:/Original", str(cfg.parent / "one")}
    # and the live config carries all three vaults
    live = json.loads(cfg.read_text(encoding="utf-8"))
    assert {v["path"] for v in live["vaults"].values()} == {
        "V:/Original", str(cfg.parent / "one"), str(cfg.parent / "two")}


def test_backup_generations_prune_to_three(cfg):
    """(ticket 64) the generations roll: the newest three backups stay,
    older ones prune (the pre-ticket-64 fixed-name backup sorts oldest
    and prunes first)."""
    cfg.write_text(json.dumps({"vaults": {}}), encoding="utf-8")
    for i in range(5):
        obsidian_vault.register(str(cfg.parent / f"vault-{i}"))
    gens = obsidian_vault.backups(cfg)
    assert len(gens) == 3
    sizes = [len(json.loads(p.read_text(encoding="utf-8"))["vaults"])
             for p in gens]
    # oldest surviving -> newest: the configs as they stood before the
    # third, fourth and fifth registrations
    assert sizes == [2, 3, 4]
    live = json.loads(cfg.read_text(encoding="utf-8"))
    assert len(live["vaults"]) == 5


def test_corrupt_config_refused_not_clobbered(cfg):
    cfg.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(obsidian_vault.ObsidianConfigError):
        obsidian_vault.register(str(cfg.parent / "lore"))
    assert cfg.read_text(encoding="utf-8") == "{not json at all"
    # and the read side degrades to "not registered" instead of raising
    assert obsidian_vault.is_registered(str(cfg.parent / "lore")) is False


def test_non_object_config_refused(cfg):
    cfg.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(obsidian_vault.ObsidianConfigError):
        obsidian_vault.register(str(cfg.parent / "lore"))
