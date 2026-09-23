"""Git-backed canon snapshots: every successful canon mutation commits the
campaign directory — inside the CAMPAIGNS WORKSPACE'S OWN git repository.

The campaigns/ workspace is a nested repo, deliberately separate from the
parent: the parent holds the engines and the studio and can be pushed to a
public remote, while campaign data (premises, factions, chronicles — the
DM's creative work) never leaves this machine unless the campaigns repo
itself is pushed somewhere private. The undo trail the README promises
("history is your undo") lives in campaigns/.git: `git -C campaigns log --
<name>` is the audit trail.

Rules of the road:
- cwd is the campaigns root, args are list-form (the parent path contains
  `&`), and the commit is `--only` the campaign's directory: nothing else
  the user has staged there is ever swept into a snapshot.
- The repo is created on demand: the first snapshot `git init`s the
  campaigns root (idempotent) and, if git has no configured identity,
  sets a local one — a newcomer's snapshots must never fail on a missing
  user.email. When git is absent, snapshot() is a logged no-op. A VCS
  failure NEVER blocks the DM — snapshot returns None and the mutation
  stands.
- Messages are truncated to 72 chars at the call sites' convention:
  "campaign/<name>: <what happened>", lowercase.
"""

import logging
import re
import shutil
import subprocess
import threading

import spine

log = logging.getLogger("studio.canon")

GIT = shutil.which("git")
AVAILABLE = bool(GIT)  # tests flip this to exercise the no-op path
MESSAGE_MAX = 72

_COMMIT_RE = re.compile(r"^\[[^\]]+ ([0-9a-f]+)\]", re.MULTILINE)

# One snapshot at a time, process-wide. Several callers write from request
# threads with only their campaign lock held (or none): two concurrent
# snapshots race on git's index.lock, the loser's add fails, and its undo
# point is silently lost — precisely in the concurrent cases where the user
# most needs it. Campaign locks are always held OUTSIDE this one (a week
# job holds the campaign lock, then snapshots), so no lock cycle.
_SNAPSHOT_LOCK = threading.Lock()


def _git(root, *args: str) -> tuple:
    try:
        proc = subprocess.run([GIT, *args], cwd=str(root),
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)
    return proc.returncode, proc.stdout, proc.stderr


def _ensure_repo(root) -> bool:
    """The campaigns root must be a git repository: init it on demand and
    give it a usable commit identity if none is configured."""
    if (root / ".git").exists():
        return True
    rc, out, err = _git(root, "init")
    if rc != 0:
        log.warning("canon snapshot skipped (git init failed in %s): %s",
                    root, (err or out).strip()[-300:])
        return False
    log.info("campaigns workspace initialized as its own git repo (%s)", root)
    rc, out, _ = _git(root, "config", "--get", "user.email")
    if rc != 0 or not out.strip():
        _git(root, "config", "user.name", "Campaign Studio")
        _git(root, "config", "user.email", "studio@localhost")
        log.info("campaigns repo: local commit identity set "
                 "(Campaign Studio <studio@localhost>) — git had none")
    return True


def snapshot(campaign_name: str, message: str):
    """Commit the campaign's current state with `message`. Returns the short
    commit hash, or None when skipped (git absent / init failed / nothing to
    commit / any VCS failure — all logged, never raised)."""
    if not (GIT and AVAILABLE):
        log.info("canon snapshot skipped (git unavailable): %s", message)
        return None
    try:
        d = spine.campaign_dir(campaign_name)
    except spine.CampaignNotFound:
        log.warning("canon snapshot skipped (unknown campaign %s)",
                    campaign_name)
        return None
    root = d.parent
    if not _ensure_repo(root):
        return None

    with _SNAPSHOT_LOCK:
        return _snapshot_locked(root, campaign_name, message)


def _snapshot_locked(root, campaign_name: str, message: str):
    message = " ".join(str(message).split())[:MESSAGE_MAX]
    rc, out, err = _git(root, "add", "-A", "--", campaign_name)
    if rc != 0:
        log.warning("canon snapshot failed at git add (%s): %s",
                    campaign_name, (err or out).strip()[-300:])
        return None
    rc, out, err = _git(root, "commit", "-m", message,
                        "--only", "--", campaign_name)
    if rc != 0:
        if "nothing to commit" in (out + err):
            log.info("canon snapshot: nothing to commit for %s", campaign_name)
        else:
            log.warning("canon snapshot failed at git commit (%s): %s",
                        campaign_name, (err or out).strip()[-300:])
        return None
    m = _COMMIT_RE.search(out)
    sha = m.group(1) if m else "?"
    log.info("canon snapshot %s %s", sha, message)
    return sha
