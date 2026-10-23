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
- cwd is the campaigns root, args are list-form (a rule kept from when
  the parent path contained `&`), and the commit is `--only` the
  campaign's directory: nothing else
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

# Lines campaigns/.gitignore must carry (ticket 47): Obsidian's app-config
# dir is chalk and plugin state, not canon — and it appears at whatever
# depth a vault was opened (the campaign root, or lore/ via the one-click
# registration), so the pattern is depth-agnostic. Forecast bundles are
# projections, not canon — forecasts.py's docstring has promised their
# git-ignoring since they shipped; this is where it becomes true.
WORKSPACE_IGNORE = (".obsidian/", "**/simulation/forecasts/")

# One snapshot at a time, process-wide: concurrent snapshots race on git's
# index.lock and the loser silently loses its undo point. Campaign locks are
# always held OUTSIDE this one, so no lock cycle.
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


def ensure_workspace_ignore(root) -> None:
    """Idempotently give the campaigns workspace a .gitignore carrying
    WORKSPACE_IGNORE. Runs on the snapshot path (NOT inside _ensure_repo —
    that early-returns on an existing repo, and pre-existing workspaces
    must gain the lines too), appending only what is missing so a file the
    user maintains is never rewritten. Failure logs and proceeds: this is
    VCS furniture, and a furniture problem never stands between the DM and
    their files."""
    try:
        p = root / ".gitignore"
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
        have = {ln.strip() for ln in text.splitlines()}
        missing = [ln for ln in WORKSPACE_IGNORE if ln not in have]
        if not missing:
            return
        if text and not text.endswith("\n"):
            text += "\n"
        text += "".join(ln + "\n" for ln in missing)
        spine.write_text_atomic(p, text)
        log.info("campaigns workspace .gitignore: added %s",
                 ", ".join(missing))
    except OSError as e:
        log.warning("could not update campaigns/.gitignore (snapshots "
                    "continue): %s", e)


def snapshot(campaign_name: str, message: str):
    """Commit the campaign's current state with `message`. Returns the short
    commit hash, or None when skipped (git absent / init failed / nothing to
    commit / any VCS failure — all logged, never raised). Compatibility
    wrapper around snapshot_result(); new callers should use that and
    surface the state."""
    return snapshot_result(campaign_name, message).get("commit")


def snapshot_result(campaign_name: str, message: str) -> dict:
    """Commit with a reportable outcome:
      {"state": "committed"|"no_change"|"unavailable"|"failed",
       "commit": short sha or None, "detail": str}
    committed = history recorded; no_change = nothing differed (a no-op is
    success, not failure); unavailable = git missing so no undo trail exists
    (distinct from a command failing); failed = git ran and failed. The
    mutation itself is never blocked either way (a VCS failure never stands
    between the DM and their files)."""
    if not (GIT and AVAILABLE):
        log.info("canon snapshot skipped (git unavailable): %s", message)
        return {"state": "unavailable", "commit": None,
                "detail": "git is not installed or disabled — no history "
                          "snapshot was made (the files themselves are fine)"}
    try:
        # canonicalize BEFORE anything flows to git (ticket 56): a call
        # under a case alias or trailing dot must not `git add -A` an
        # empty pathspec while the real directory got the write — the
        # directory's name is the one git knows. Idempotent for an
        # already-canonical name (the exact match returns itself).
        campaign_name = spine.canonical_name(campaign_name)
        d = spine.campaign_dir(campaign_name)
    except spine.CampaignNotFound:
        log.warning("canon snapshot skipped (unknown campaign %s)",
                    campaign_name)
        return {"state": "failed", "commit": None,
                "detail": f"unknown campaign: {campaign_name}"}
    root = d.parent
    if not _ensure_repo(root):
        return {"state": "unavailable", "commit": None,
                "detail": "the campaigns workspace could not be initialized "
                          "as a git repository"}

    with _SNAPSHOT_LOCK:
        ensure_workspace_ignore(root)
        return _snapshot_locked(root, campaign_name, message)


def _snapshot_locked(root, campaign_name: str, message: str) -> dict:
    message = " ".join(str(message).split())[:MESSAGE_MAX]
    rc, out, err = _git(root, "add", "-A", "--", campaign_name)
    if rc != 0:
        log.warning("canon snapshot failed at git add (%s): %s",
                    campaign_name, (err or out).strip()[-300:])
        return {"state": "failed", "commit": None,
                "detail": "git add failed: " + (err or out).strip()[-200:]}
    rc, out, err = _git(root, "commit", "-m", message,
                        "--only", "--", campaign_name)
    if rc != 0:
        # git prints the full "nothing to commit" status text when the tree
        # is clean, but with untracked files SIBLING to the pathspec (a
        # second campaign never snapshotted yet) the wording becomes
        # "nothing added to commit but untracked files present" — same
        # no-change verdict, and it must not read as a failure
        if ("nothing to commit" in (out + err)
                or "nothing added to commit" in (out + err)):
            log.info("canon snapshot: nothing to commit for %s", campaign_name)
            return {"state": "no_change", "commit": None,
                    "detail": "nothing to commit — the campaign files were "
                              "already identical to the last snapshot"}
        log.warning("canon snapshot failed at git commit (%s): %s",
                    campaign_name, (err or out).strip()[-300:])
        return {"state": "failed", "commit": None,
                "detail": "git commit failed: " + (err or out).strip()[-200:]}
    m = _COMMIT_RE.search(out)
    sha = m.group(1) if m else "?"
    log.info("canon snapshot %s %s", sha, message)
    return {"state": "committed", "commit": sha, "detail": message}
