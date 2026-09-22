"""Git-backed canon snapshots (hardening wave): every successful canon
mutation commits the campaign directory to the ENCLOSING git repo — the
studio's parent (Documents/D&D) is the repository root and `campaigns/` is
user data living inside it.

Rules of the road:
- cwd is the repo root, args are list-form (the parent path contains `&`),
  and the commit is `--only` the campaign's path: nothing else the user (or
  an integrator) has staged is ever swept into a snapshot.
- Availability is probed ONCE at import (`git` on PATH + inside a work
  tree); when absent, snapshot() is a logged no-op. A VCS failure NEVER
  blocks the DM — snapshot returns None and the mutation stands.
- Messages are truncated to 72 chars at the call sites' convention:
  "campaign/<name>: <what happened>", lowercase.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

import spine

log = logging.getLogger("studio.canon")

GIT = shutil.which("git")
REPO_ROOT = spine.STUDIO_DIR.parent  # read at call time via module global
MESSAGE_MAX = 72


def _inside_work_tree() -> bool:
    if not GIT:
        return False
    try:
        proc = subprocess.run(
            [GIT, "rev-parse", "--is-inside-work-tree"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("git probe failed (%s) — canon snapshots disabled", e)
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


AVAILABLE = _inside_work_tree()  # module-level probe; snapshot no-ops if False

_COMMIT_RE = re.compile(r"^\[[^\]]+ ([0-9a-f]+)\]", re.MULTILINE)


def _git(*args: str) -> tuple:
    try:
        proc = subprocess.run([GIT, *args], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)
    return proc.returncode, proc.stdout, proc.stderr


def snapshot(campaign_name: str, message: str):
    """Commit the campaign's current state with `message`. Returns the short
    commit hash, or None when skipped (git absent / campaign outside the
    repo / nothing to commit / any VCS failure — all logged, never raised)."""
    if not (GIT and AVAILABLE):
        log.info("canon snapshot skipped (git repo unavailable): %s", message)
        return None
    try:
        d = spine.campaign_dir(campaign_name)
    except spine.CampaignNotFound:
        log.warning("canon snapshot skipped (unknown campaign %s)",
                    campaign_name)
        return None
    try:
        rel = d.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # campaigns root relocated away from the repo (tests, portable set-
        # ups): the snapshot would commit the wrong tree — skip loudly
        log.info("canon snapshot skipped (%s lives outside %s)",
                 campaign_name, REPO_ROOT)
        return None

    message = " ".join(str(message).split())[:MESSAGE_MAX]
    rc, out, err = _git("add", "-A", "--", rel)
    if rc != 0:
        log.warning("canon snapshot failed at git add (%s): %s",
                    rel, (err or out).strip()[-300:])
        return None
    rc, out, err = _git("commit", "-m", message, "--only", "--", rel)
    if rc != 0:
        if "nothing to commit" in (out + err):
            log.info("canon snapshot: nothing to commit for %s", rel)
        else:
            log.warning("canon snapshot failed at git commit (%s): %s",
                        rel, (err or out).strip()[-300:])
        return None
    m = _COMMIT_RE.search(out)
    sha = m.group(1) if m else "?"
    log.info("canon snapshot %s %s", sha, message)
    return sha
