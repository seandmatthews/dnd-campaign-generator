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
    with campaign_lock_nowait(name, "the preset publish"):
        try:
            proc = subprocess.run(
                cmd, cwd=str(home), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=PUBLISH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=504,
                detail=f"the preset publisher did not finish within "
                       f"{PUBLISH_TIMEOUT_S}s — nothing was changed")
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"could not start the avtt companion: {e}")
    if proc.returncode == 0:
        canon.snapshot(
            name, f"table-setup: installed the {pack} preset "
                  "(avtt companion)")
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}
