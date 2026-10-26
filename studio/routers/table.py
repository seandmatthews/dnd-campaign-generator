"""Table-setup API: the avtt companion, spawned behind buttons (ticket 53).

The companion (dcg-avtt-integration) owns everything that writes
Atlas-owned file formats — the core never grows Atlas code (the seam
ruling, docs/design/atlas-seam.md). This router is a thin spawn seam: it
resolves the companion's home, spawns ONE publisher subcommand as a
subprocess (list-form args, explicit cwd, never shell — the engines.py
discipline), and returns the publisher's stdout verbatim. All companion
logic — validation, the system: gate, the pin-mismatch policy — stays
companion-side; this card shows its refusal text rather than
pre-validating a second time.

The only companion-adjacent thing done in-process is detection, and it is
existence-only and read-only: whether the campaign's vault carries the
Atlas plugin directory. The spine ignores .obsidian/ everywhere (canon
snapshots, doctor, the gitignore check); reading it back for a checkmark
is not reading table state. The authoritative version report always comes
from the companion itself (`publish.py <campaign> version`).
"""

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import canon
from routers.common import campaign_lock_nowait, require_campaign

router = APIRouter()

STUDIO_DIR = Path(__file__).resolve().parents[1]

PUBLISH_TIMEOUT_S = 120
PLUGIN_ID = "atlas-vtt"


def _looks_companion(p: Path) -> bool:
    return (p / "src" / "avtt" / "publish.py").is_file()


def companion_home() -> Path | None:
    """Where the avtt companion lives, or None.

    Resolution order: $AVTT_INTEGRATION_HOME (tests, exotic installs),
    then avtt-integration/ beside the studio (the bundle's layout —
    build_release.py stages it there), then the dev checkout's sibling
    clone (dcg-avtt-integration beside this repo). No companion code is
    ever imported into this process (D1: no imports across projects) —
    the companion is spawned, nothing more."""
    env = os.environ.get("AVTT_INTEGRATION_HOME")
    if env and _looks_companion(Path(env)):
        return Path(env)
    for candidate in (STUDIO_DIR.parent / "avtt-integration",
                      STUDIO_DIR.parent.parent / "dcg-avtt-integration"):
        if _looks_companion(candidate):
            return candidate
    return None


class PublishBody(BaseModel):
    pack: str = "icrpg"


class ScenesBody(BaseModel):
    site: str | None = None


def _spawn_publisher(name: str, home: Path, cmd: list[str], what: str,
                     snapshot_msg: str) -> dict:
    """The one spawn seam both buttons share: run the companion subcommand
    under the campaign lock, cap it, snapshot canon on success, and return
    the publisher's stdout verbatim. A nonzero exit is a refusal — a
    result, not an error; the publisher's own text is what the card
    shows."""
    with campaign_lock_nowait(name, f"the {what}"):
        try:
            proc = subprocess.run(
                cmd, cwd=str(home), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=PUBLISH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # subprocess.run killed the companion mid-publish: partial
            # atlas-vtt/ writes are real, and the skipped snapshot leaves
            # them uncommitted (they would ride into a later unrelated
            # commit). Say that — never "nothing was changed" (ticket 69).
            raise HTTPException(
                status_code=504,
                detail=f"the {what} did not finish within "
                       f"{PUBLISH_TIMEOUT_S}s — the publisher was killed at "
                       "the timeout, so any partial writes to atlas-vtt/ "
                       "are uncommitted; check atlas-vtt/ before "
                       "republishing")
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"could not start the avtt companion: {e}")
    if proc.returncode == 0:
        canon.snapshot(name, snapshot_msg)
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}


@router.get("/api/campaign/{name}/table-setup/status")
def get_table_status(name: str):
    """The card's status line: is the companion here, is Atlas in the
    vault. Existence checks only — versions are the companion's report."""
    d = require_campaign(name)
    return {
        "companion_found": companion_home() is not None,
        "atlas_plugin": (d / ".obsidian" / "plugins" / PLUGIN_ID).is_dir(),
    }


@router.post("/api/campaign/{name}/table-setup/publish-presets")
def post_publish_presets(name: str, body: PublishBody | None = None):
    """Install a system preset pack into the campaign's atlas-vtt/ by
    spawning the bundled companion's presets publisher.

    The request carries no path — the campaign directory is resolved
    server-side from the name, and spine refuses malformed names before
    anything spawns, so the subprocess can never be pointed outside the
    campaigns root. The publisher refuses campaigns whose `system:` does
    not match the pack; its refusal text comes back in stdout with
    ok=false and the card shows it verbatim. Success (exit 0) snapshots
    the campaign: atlas-vtt/ is committed canon policy (ticket 47)."""
    d = require_campaign(name)
    home = companion_home()
    if home is None:
        raise HTTPException(
            status_code=409,
            detail="the avtt companion is not bundled with this copy of "
                   "the studio — the manual steps in ATLAS.md are the "
                   "always-true path (drag the preset, or install it via "
                   "the companion repo on a dev machine)")
    pack = (body.pack if body else None) or "icrpg"
    cmd = [sys.executable, str(home / "src" / "avtt" / "publish.py"),
           str(d), "presets", "--pack", pack]
    return _spawn_publisher(
        name, home, cmd, "preset publish",
        f"table-setup: installed the {pack} preset (avtt companion)")


@router.post("/api/campaign/{name}/table-setup/publish-scenes")
def post_publish_scenes(name: str, body: ScenesBody | None = None):
    """Convert + publish site battlemap scene(s) into the campaign's
    atlas-vtt/ by spawning the bundled companion's scenes publisher.

    The same seam as publish-presets: no client-supplied path — the
    campaign dir is resolved server-side and the site slug is the
    publisher's own to validate (an unknown one is a verbatim refusal,
    not a pre-check here). The optional site travels as ONE joined
    ``--site=<slug>`` argument so a crafted value can never smuggle an
    option flag of its own. Omitting the site publishes every site.

    On this machine's expected state the vault does not run the pinned
    Atlas — the publisher takes its designed ``_inbox/`` degrade path
    (exit 0, the note in stdout): the .uvtt is staged for a hand
    drag-in, the card shows that note as the answer, not as an error."""
    d = require_campaign(name)
    home = companion_home()
    if home is None:
        raise HTTPException(
            status_code=409,
            detail="the avtt companion is not bundled with this copy of "
                   "the studio — the manual steps in ATLAS.md are the "
                   "always-true path (drag the .uvtt into Atlas; walls, "
                   "doors and lights come along)")
    cmd = [sys.executable, str(home / "src" / "avtt" / "publish.py"),
           str(d), "scenes"]
    site = ((body.site if body else None) or "").strip()
    if site:
        cmd.append(f"--site={site}")
    return _spawn_publisher(
        name, home, cmd, "scene publish",
        "table-setup: published site scene(s) to atlas-vtt/ "
        "(avtt companion)")
