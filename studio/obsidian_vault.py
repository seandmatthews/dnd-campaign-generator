"""Register campaign lore folders in the user's Obsidian vault list.

The obsidian://open?path=... deep link only focuses a folder Obsidian
already knows. A folder it has never seen has no vault to focus: the
first click shows Obsidian's own "Vault not found" error instead (the
authoring card has warned about this since ticket 43). There is no
obsidian:// action that pre-registers a folder, so the studio does the
registration itself: one JSON entry in Obsidian's own config —
%APPDATA%/obsidian/obsidian.json on Windows, ~/Library/Application
Support/obsidian/obsidian.json on macOS, ~/.config/obsidian/obsidian.json
elsewhere — merged in place with a timestamped backup taken first (the
newest three generations kept — one fixed backup name meant a SECOND
registration overwrote the only copy of the ORIGINAL vault list,
ticket 64).

The caveat the UI must always carry: a RUNNING Obsidian keeps its vault
list in memory and writes the file back on vault changes, silently
dropping a concurrent edit — after registering, quit and reopen Obsidian
once.

This module touches nothing inside the vault itself. Pages stay the DM's
(the no-in-app-editor ruling governs authoring, not environment setup);
the only file ever written is Obsidian's own config, and the campaign
path it names is always the studio-resolved lore folder — the endpoint
takes no path argument.
"""

import datetime
import json
import os
import secrets
import shutil
import sys
import threading
import time
from pathlib import Path

import spine

_BACKUP_SUFFIX = ".studio-backup"
_BACKUPS_KEPT = 3
_WRITE_LOCK = threading.Lock()


class ObsidianConfigError(RuntimeError):
    """Obsidian's config exists but is not usable — never clobber it."""


def config_path() -> Path:
    """Where Obsidian keeps its vault list. OBSIDIAN_CONFIG overrides
    (tests; a portable install pointing at a chosen file)."""
    override = os.environ.get("OBSIDIAN_CONFIG")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "obsidian" / "obsidian.json"
    if sys.platform == "darwin":
        return Path("~/Library/Application Support/obsidian/obsidian.json"
                    ).expanduser()
    return Path("~/.config/obsidian/obsidian.json").expanduser()


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _load(cfg: Path) -> dict:
    """The parsed config, or {} when absent. A config that exists but is
    not parseable JSON — or not a JSON object — is corrupt: refuse loudly
    rather than clobber the user's vault list with a fresh one."""
    try:
        with open(cfg, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise ObsidianConfigError(
            f"{cfg} is not valid JSON ({e.msg} at line {e.lineno}) — "
            "refusing to edit it; register the folder by hand in Obsidian "
            "(Open folder as vault).") from e
    if not isinstance(doc, dict):
        raise ObsidianConfigError(
            f"{cfg} is not a JSON object — refusing to edit it; register "
            "the folder by hand in Obsidian (Open folder as vault).")
    return doc


def is_registered(path: str) -> bool:
    """Best-effort read for the authoring card: anything unusable (no
    config, corrupt file, unreadable entry) reads as not registered —
    the card's advice stays correct either way."""
    try:
        doc = _load(config_path())
        vaults = doc.get("vaults")
        if not isinstance(vaults, dict):
            return False
        norm = _norm(path)
        return any(isinstance(v, dict) and _norm(str(v.get("path", ""))) == norm
                   for v in vaults.values())
    except (OSError, ValueError, ObsidianConfigError):
        return False


def backups(cfg: Path) -> list[Path]:
    """The config's backup generations, oldest first (ticket 64): one
    timestamped copy per registration, the newest _BACKUPS_KEPT kept."""
    prefix = cfg.name + _BACKUP_SUFFIX
    try:
        return sorted(p for p in cfg.parent.iterdir()
                      if p.name.startswith(prefix))
    except OSError:
        return []


def _backup(cfg: Path) -> None:
    """Copy the current config aside as a NEW generation — the old single
    fixed backup name meant the second registration destroyed the only
    copy of the ORIGINAL vault list. Same-microsecond collisions bump a
    counter instead of overwriting; the legacy fixed name (pre-ticket-64)
    sorts before every timestamped one and prunes first."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S%f")
    backup = cfg.with_name(f"{cfg.name}{_BACKUP_SUFFIX}-{stamp}")
    n = 1
    while backup.exists():
        backup = cfg.with_name(f"{cfg.name}{_BACKUP_SUFFIX}-{stamp}-{n}")
        n += 1
    shutil.copy2(cfg, backup)
    for old in backups(cfg)[:-_BACKUPS_KEPT]:
        try:
            old.unlink()
        except OSError:
            pass  # a backup that will not delete is never worth a crash


def register(path: str) -> dict:
    """Merge `path` into Obsidian's vault list as an unfocused vault.

    Idempotent (an already-registered path returns already=True with no
    write), backs the config up first, and writes atomically. Returns
    {"registered": True, "already": bool, "path": ..., "config": ...}.
    """
    cfg = config_path()
    with _WRITE_LOCK:
        doc = _load(cfg)
        vaults = doc.get("vaults")
        if not isinstance(vaults, dict):
            vaults = {}
        norm = _norm(path)
        hit = next((vid for vid, v in vaults.items()
                    if isinstance(v, dict) and _norm(str(v.get("path", ""))) == norm),
                   None)
        if hit is not None:
            return {"registered": True, "already": True, "path": path,
                    "config": cfg}
        if os.path.exists(cfg):
            _backup(cfg)
        vaults[secrets.token_hex(8)] = {
            "path": path,
            "ts": int(time.time() * 1000),
            "open": False,
        }
        doc["vaults"] = vaults
        spine.write_text_atomic(cfg, json.dumps(doc, indent=2))
        return {"registered": True, "already": False, "path": path,
                "config": cfg}
