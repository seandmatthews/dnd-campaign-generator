"""Dependency doctor (M5) — the honest answer to "why won't it boot".

Checks, in order: python version, the studio's imports, node, uv (optional),
the three engine roots with their key CLIs runnable, vale (optional),
the campaigns tree, the vault links, and the LLM key situation — no key vs
wrong endpoint vs endpoint fine, classified by ONE tiny live probe against
whatever llm.env / the legacy Z.ai discovery resolves.

Stdlib only at module level: the doctor must run when fastapi/uvicorn/yaml
are exactly the things missing. Output is {"ok": overall, "checks":[...]}
where every check is {name, ok, detail, fix, required}. `ok` covers the
required checks; the LLM key and vale are optional (the app works
read-only without a key — generation does not).

CLI:  python studio/doctor.py           human-readable
      python studio/doctor.py --json    the API shape
      python studio/doctor.py --fix     PERFORM the safe repairs (repair
                                        broken tool trees with npm install,
                                        run the pip line); everything else
                                        stays advice — assemble.py is never
                                        auto-run (it writes canon)
      python studio/doctor.py --migrate-data-root
                                        ONE explicit migration (ticket 35):
                                        move the legacy data dirs under the
                                        campaign-data root; refused while
                                        any lock is held, never auto-run
Importable: doctor.report() — wired to GET /api/doctor (routers/system.py).
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import spine

STUDIO_DIR = Path(__file__).resolve().parent
PARENT = STUDIO_DIR.parent

STUDIO_MODULES = ["fastapi", "uvicorn", "yaml", "httpx"]
API_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
PROBE_MODEL = "glm-5.3-flash"
PROBE_TIMEOUT_S = 15
ENGINE_PROBE_TIMEOUT_S = 30

# the tool trees with a documented repair (D7): present but with their
# critical module un-loadable means a broken/incomplete node_modules.
DND_MAPPING_TOOLS = PARENT / "dnd-mapping" / "tools"
SETTLEMAKER_DIR = DND_MAPPING_TOOLS / "settlemaker"
FMG_DIR = DND_MAPPING_TOOLS / "Fantasy-Map-Generator"
NPM_INSTALL_CMD = ["npm", "install", "--no-audit", "--no-fund"]
# FMG adds --ignore-scripts: its only install-time script is "prepare":
# simple-git-hooks (dev-only git hooks), and npm runs scripts through
# cmd.exe, which cannot handle the `&` this repo's path carried until
# the 2026-10-04 rename (the flag stays). Nothing the headless pipeline
# needs is scripted.
NPM_INSTALL_FMG = NPM_INSTALL_CMD + ["--ignore-scripts"]

# a live/interrupted week leaves a flag in the engine home (the engine
# home is scratch; the campaign is canon — copy-back only happens on
# success). The path itself is owned by engines.week_flag_path() — this
# module deliberately keeps no second copy of it.

ENGINES = [
    ("dnd-mapping", "src/import_fmg.py",
     "clone/zip the parent folder whole — studio sits beside dnd-mapping/"),
    ("agentic-simulator", "src/tick.py",
     "clone/zip the parent folder whole — studio sits beside agentic-simulator/"),
    ("verification", "check.py",
     "clone/zip the parent folder whole — studio sits beside verification/"),
]


def _mk(name, ok, detail, fix=None, required=True):
    return {"name": name, "ok": bool(ok), "detail": detail,
            "fix": fix, "required": bool(required)}


# ------------------------------------------------------------------- checks


def check_python():
    v = sys.version_info
    ok = v >= (3, 10)
    return _mk("python", ok, f"Python {v.major}.{v.minor}.{v.micro}"
               + ("" if ok else " (need >= 3.10)"),
               None if ok else "install Python 3.10 or newer (python.org)")


def check_imports(modules=None):
    """Each studio dependency actually imports — not just find_spec: a
    present-but-broken install must be reported, since run.py's friendly
    boot path quotes this check verbatim."""
    names = list(modules or STUDIO_MODULES)
    broken = []
    for m in names:
        try:
            importlib.import_module(m)
        except Exception as e:  # noqa: BLE001 — any import failure is the finding
            reason = f": {e}" if str(e) else ""
            broken.append(f"{m} ({type(e).__name__}{reason})")
    if not broken:
        return _mk("imports", True, "all importable: " + ", ".join(names))
    return _mk("imports", False, "not importable: " + "; ".join(broken),
               fix="pip install -r studio/requirements.txt")


def _version_of(cmd, cwd=None):
    """Run a --version-ish probe; (ran, version-or-error line).

    Windows .cmd/.bat shims cannot be exec'd directly by CreateProcess —
    they go through ["cmd", "/c", <full path>, ...]. Still list-form args
    against a resolved PATH entry, never a shell string."""
    attempts = [cmd]
    where = shutil.which(cmd[0])
    if where and where.lower().endswith((".cmd", ".bat")):
        attempts.insert(0, ["cmd", "/c", where, *cmd[1:]])
    last = "not found"
    for attempt in attempts:
        try:
            proc = subprocess.run(attempt, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=15, cwd=str(cwd) if cwd else None)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            last = str(e)
            continue
        line = (proc.stdout or "").strip().splitlines()
        if not line:
            line = (proc.stderr or "").strip().splitlines()
        if line:
            return True, line[0]
        last = f"(exit {proc.returncode})"
    return False, last


def check_node():
    import engines
    # resolve exactly what engines.run_node will launch: a version manager's
    # .cmd shim satisfies shutil.which but CreateProcess (shell=False) can
    # not execute it, so doctor used to green-light a node no job could run
    where = engines.node_executable()
    if where == "node":
        return _mk("node", False, "not on PATH",
                   fix="install Node 18+ (https://nodejs.org) — the map engines need it")
    ran, line = _version_of([where, "--version"])
    note = "" if where.lower().endswith(".exe") else         " (a non-.exe shim — jobs may not be able to launch it)"
    return _mk("node", ran, f"launching {where}" + (f" ({line})" if ran else f" ({line})") + note,
               fix=None if ran else "node exists but would not run")


def check_uv():
    """uv, the optional one-tool path (`uv run python studio/run.py` sets up
    Python + venv + packages by itself). Informational either way — the pip
    path needs nothing but Python."""
    where = shutil.which("uv")
    if not where:
        return _mk("uv (optional)", True,
                   "not installed — optional: `uv run` is the one-command boot "
                   "(pip path works without it)",
                   fix="pip install uv, or winget install astral-sh.uv",
                   required=False)
    ran, line = _version_of(["uv", "--version"])
    return _mk("uv (optional)", True, f"on PATH at {where}"
               + (f" ({line})" if ran else ""), required=False)


def check_engines():
    """The three sibling engine roots exist and their key CLIs run.

    Probes are usage probes (no args): import_fmg.py prints its docstring
    and exits 1, tick.py prints the faction views and exits 0, check.py
    prints argparse usage and exits 2 — any of those means "runnable".
    List-form args, cwd=engine root, never shell=True (the parent path
    contained `&` until 2026-10-04).

    While a week run is in flight the simulator probe is skipped: tick.py's
    parting save_state replaces state.yaml, and a concurrent reader can make
    that os.replace fail on Windows (sharing violation) — losing the whole
    week after the spend. In flight is detected by the run flag OR the
    engine lock — the actual mutual-exclusion invariant, held for the whole
    run. The flag is written only once the lock is held (ticket 26), so the
    lock alone covers a live run; the flag still names the other case, a
    crashed run whose lock died with the process but whose marker waits
    for boot restore. The other engines' homes are untouched by a week, so
    they still probe."""
    import engines
    out = []

    def week_in_flight() -> bool:
        if engines.week_flag_path().is_file():
            return True
        got = engines.ENGINE_LOCK.acquire(blocking=False)
        if got:
            engines.ENGINE_LOCK.release()
        return not got

    for dirname, script, fix in ENGINES:
        name = f"engine: {dirname}"
        root = PARENT / dirname
        if not root.is_dir():
            out.append(_mk(name, False, f"{root} not found", fix=fix))
            continue
        if root == engines.AGENTIC_SIMULATOR and week_in_flight():
            out.append(_mk(name, True,
                           "skipped — a week run is in progress; the probe "
                           "would race the engine's state save",
                           required=False))
            continue
        try:
            rc, lines = engines.run_py(root, script, timeout=ENGINE_PROBE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — a dead engine is a doctor finding
            out.append(_mk(name, False, f"{script} could not run: {e}", fix=fix))
            continue
        ran = rc in (0, 1, 2) and bool(lines)
        detail = f"{script} runs (exit {rc}: " + (lines[0] if lines else "no output")[:70] + ")"
        out.append(_mk(name, ran, detail,
                       fix=None if ran else f"{script} exited {rc} — see the engine's own docs"))
    return out


def check_tool_trees():
    """The two third-party tool trees (git-ignored on purpose — setup-tools.py
    clones, patches, pins and builds them; optional: read-only studio use and
    the fast test tier never need them)."""
    out = []
    for label, root, marker in (
        ("Fantasy-Map-Generator", FMG_DIR, Path("node_modules") / "vitest" / "vitest.mjs"),
        ("settlemaker", SETTLEMAKER_DIR, Path("dist") / "index.js"),
    ):
        present = (root / marker).is_file()
        out.append(_mk(f"tool: {label}", present,
                       "ready" if present else
                       f"missing — {label}/ is not under dnd-mapping/tools/",
                       fix=None if present else
                       "python setup-tools.py  (clones, patches, pins and "
                       "builds both trees; needs network once)",
                       required=False))
    return out


def check_week_run(path=None):
    """NEW: is an engine run in progress (or left over from a crash)?

    Reports whether agentic-simulator/world/.week-run-flag exists. Absence
    is fine. Presence is informational (required=False): the engine home is
    scratch and is re-copied from the campaign on the next week/boot — a
    crashed run never corrupts a campaign, because copy-back only happens
    on success. (The flag is shared by week runs AND forecasts, so the
    label stays engine-generic.)"""
    import engines
    flag = path or engines.week_flag_path()  # call-time seam: tests relocate it
    if flag.exists():
        return _mk("week run", False,
                   f"an engine run (week or forecast) is in progress or was "
                   f"interrupted ({flag})",
                  fix="let it finish if it is still running; otherwise just "
                      "boot — the engine home is restored from the campaign "
                      "(copy-back only happens on a successful week)",
                  required=False)
    return _mk("week run", True, "no engine run in progress", required=False)


def check_vale():
    where = shutil.which("vale")
    if where:
        ran, line = _version_of(["vale", "--version"])
        return _mk("vale", True, f"installed at {where}" + (f" ({line})" if ran else ""),
                   required=False)
    return _mk("vale", True, "not installed — check.py's taste layer is skipped, "
               "the structural layer still runs",
               fix="winget install Vale.Vale (optional)", required=False)


def check_campaigns():
    """campaigns/ exists with at least one campaign (a dir with campaign.yaml)."""
    root = spine.campaigns_root()
    if not root.is_dir():
        return _mk("campaigns", False, f"{root} does not exist",
                   fix="create campaigns/ (or run python studio/assemble.py "
                       "to build campaigns/ruravaqan from the engine artifacts)")
    found = sorted(d.name for d in root.iterdir()
                   if d.is_dir() and (d / "campaign.yaml").is_file())
    if not found:
        return _mk("campaigns", False, f"{root} has no campaign (a folder with "
                   "campaign.yaml inside)",
                   fix="run python studio/assemble.py, or create "
                       "campaigns/<name>/campaign.yaml")
    # Folders whose names cannot be addressed (bad slug) are skipped by the
    # app itself — name them so "where did my campaign go?" has an answer.
    skipped = spine.skipped_campaign_folders()
    detail = f"{len(found)} campaign(s): " + ", ".join(found)
    if skipped:
        detail += f" (skipped, unaddressable names: {', '.join(skipped)})"
    return _mk("campaigns", True, detail,
               fix=None if not skipped else
               "rename the skipped folder(s) to a lowercase slug "
               "(a-z, 0-9, hyphens) — the studio only lists addressable names")


def check_vault():
    """The campaign vault's links (obsidian-integration.md §2): dangling
    wiki-links and name/alias collisions across all campaigns — advisory
    (required=False), the same findings the review queue shows."""
    root = spine.campaigns_root()
    if not root.is_dir():
        return _mk("vault links", True, "no campaigns folder yet",
                   required=False)
    try:
        import links as links_mod
    except Exception as e:  # noqa: BLE001 — a broken import is the finding
        return _mk("vault links", False, f"link index unusable: {e}",
                   fix="pip install -r studio/requirements.txt", required=False)
    dangling = 0
    collisions = 0
    problems = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "campaign.yaml").is_file():
            continue
        if not spine.valid_name(d.name):
            continue
        try:
            idx = links_mod.build_index(d.name)
        except Exception as e:  # noqa: BLE001 — one bad page must not kill the check
            problems.append(f"{d.name}: {e}")
            continue
        dangling += len(idx["dangling"])
        collisions += len(idx["collisions"])
        for c in idx["collisions"]:
            problems.append(f"{d.name}: '{c['key']}' claimed by "
                            f"{', '.join(c['files'])}")
    detail = "vault links fine"
    bits = []
    if dangling:
        bits.append(f"{dangling} dangling link(s)")
    if collisions:
        bits.append(f"{collisions} name/alias collision(s)")
    if bits:
        detail = ", ".join(bits) + " — see the Review queue"
    if problems:
        detail = "; ".join(problems[:3])
    return _mk("vault links", not (dangling or collisions or problems), detail,
               fix="resolve dangling links by creating the page (or fix the "
                   "link); collisions: rename or de-alias one page",
               required=False)


def check_data_root():
    """Legacy (pre-ticket-35) data dirs still present? Advisory: the studio
    uses campaign-data/ regardless — until the legacy data is migrated, the
    campaigns it knew are invisible to it. The fix is the EXPLICIT doctor
    migration; nothing ever moves on boot (ticket 35 ruling)."""
    import engines
    legacy = engines.legacy_data_dirs()
    if not legacy:
        return _mk("data root", True,
                   f"campaign data lives under {spine.data_root()}",
                   required=False)
    names = ", ".join(str(old) for old, _ in legacy)
    return _mk("data root", False,
               f"legacy data dirs found: {names} — the studio is using "
               f"{spine.data_root()}, which does not see them yet",
               fix="python studio/doctor.py --migrate-data-root  (moves them "
                   "under the data root; refused while any campaign lock, "
                   "engine-home lock or week flag is held)",
               required=False)


def check_workspace_ignore():
    """campaigns/.gitignore carries the pinned lines (ticket 47): Obsidian's
    app-config dir and forecast bundles must not ride in canon snapshots.
    One AGGREGATED advisory check (the check_vault pattern — duplicate check
    names collapse in by_name). Purely textual: no git dependency. An
    ignore does not untrack, so the fix names the rm --cached command for
    anything committed before the line landed; the studio (re)writes the
    file itself on the next snapshot."""
    import canon  # lazy, like check_vault's links
    root = spine.campaigns_root()
    p = root / ".gitignore"
    if not p.is_file():
        return _mk("campaigns gitignore", True,
                   "no campaigns/.gitignore yet — the studio writes one "
                   "(.obsidian/, forecast bundles) on the next snapshot",
                   required=False)
    have = {ln.strip() for ln in p.read_text(encoding="utf-8",
                                            errors="replace").splitlines()}
    missing = [ln for ln in canon.WORKSPACE_IGNORE if ln not in have]
    if not missing:
        return _mk("campaigns gitignore", True,
                   "campaigns/.gitignore ignores .obsidian/ and forecast "
                   "bundles", required=False)
    return _mk("campaigns gitignore", False,
               f"campaigns/.gitignore is missing: {', '.join(missing)} — "
               "those files ride into every canon snapshot",
               fix="add the line(s) to campaigns/.gitignore (the studio "
                   "does it on the next snapshot). An ignore does not "
                   "untrack: for files committed earlier, run git -C "
                   "campaigns rm -r --cached -- <name>/.obsidian/",
               required=False)


def check_campaign_system():
    """The `system:` field across campaigns (ticket 48): freeform, no
    catalog — the core does not know systems and must not grow one. One
    AGGREGATED advisory check (the check_vault pattern): unset everywhere
    is normal and stays silent; the only wrongness it can see is a value
    that is not a plain string."""
    root = spine.campaigns_root()
    if not root.is_dir():
        return _mk("campaign system", True, "no campaigns folder yet",
                   required=False)
    named = []
    bad = []
    unread = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "campaign.yaml").is_file():
            continue
        if not spine.valid_name(d.name):
            continue
        try:
            doc = spine.load_campaign_yaml(d.name)
        except Exception as e:  # noqa: BLE001 — a corrupt file must not kill the check
            unread.append(f"{d.name}: {e}")
            continue
        raw = doc.get("system")
        if raw is None:
            continue
        norm = spine.campaign_system(doc)
        if norm is None:
            bad.append(f"{d.name}: system: {raw!r} is not a plain string")
        else:
            named.append(f"{d.name}: {norm}")
    if bad:
        return _mk("campaign system", False, "; ".join(bad),
                   fix="set system: to a plain string (e.g. system: icrpg) "
                       "in the campaign's campaign.yaml",
                   required=False)
    if named:
        detail = "system: " + ", ".join(named) \
            + " (freeform — packs match on the exact string)"
        if unread:
            detail += "; unreadable campaign.yaml: " + "; ".join(unread[:3])
        return _mk("campaign system", True, detail, required=False)
    if unread:
        return _mk("campaign system", True,
                   "no campaign declares a system (normal); unreadable "
                   "campaign.yaml: " + "; ".join(unread[:3]),
                   required=False)
    return _mk("campaign system", True,
               "no campaign declares a system (normal — the studio stays "
               "system-agnostic)", required=False)


# --------------------------------------------------------------- migration


def _git_tracked_candidates(dnd_mapping: Path) -> set:
    """The files git tracks under dnd-mapping/worlds/candidates (repo-root
    relative posix paths). List-form args, never a shell. Raises RuntimeError
    when git is missing or fails — the migration refuses rather than guess
    which candidates files are curated (tracked) and which are scratch."""
    cmd = ["git", "-C", str(dnd_mapping), "ls-files", "--",
           "worlds/candidates"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30,
                              check=False)
    except FileNotFoundError as e:
        raise RuntimeError(f"git not found — cannot tell curated candidates "
                           f"from scratch: {e}")
    if proc.returncode != 0:
        raise RuntimeError("git ls-files failed: "
                           + (proc.stderr or proc.stdout).strip()[:200])
    return {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}


def _move_dir(old: Path, new: Path, out) -> None:
    """One plain directory move (same volume -> a rename; nested campaign
    git repos and lock files ride along intact). A failure here is LOUD and
    names the state: on Windows a rename of a tree with any open handle
    inside fails, and shutil.move's copy+delete fallback can leave the
    source partially deleted while the target already holds the full copy
    (seen live 2026-10-04: an indexer held one .git object for an instant).
    The copy lands first, so nothing is lost — finish by deleting the
    leftover source and re-running this migration."""
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(old), str(new))
    except OSError as e:
        raise RuntimeError(
            f"moving {old} failed ({e}) — if {new} now exists it already "
            "holds the FULL copy (copy precedes delete); close whatever "
            "holds the files, delete the leftover source dir, and re-run "
            "this migration (it resumes with what is left)")
    out(f"  moved {old}")
    out(f"    -> {new}")


def migrate_data_root(parent=None, data_root=None, out=print) -> int:
    """The explicit one-shot migration (doctor --migrate-data-root): plain
    renames of the legacy data dirs into the campaign-data root, plus the
    untracked (scratch) half of dnd-mapping/worlds/candidates into
    scratch/map-candidates. Refused — nothing moved — while any campaign
    lock, engine-home lock or week flag is held, when a target already
    exists (never merges), or when git cannot say which candidates files
    are tracked. Idempotent: no legacy dirs means nothing to do.

    User-visible cost to mention: an Obsidian vault opened from
    campaigns/<name> must be re-opened once at the new path
    (<data root>/campaigns/<name>); the app's obsidian:// URIs follow the
    move on their own (they derive from campaigns_root())."""
    import engines
    parent = parent or engines.PARENT
    data_root = data_root or spine.data_root()
    pairs = engines.legacy_data_dirs(parent, data_root)
    cand_old = parent / "dnd-mapping" / "worlds" / "candidates"
    cand_new = data_root / "scratch" / "map-candidates"
    legacy_world = parent / "agentic-simulator" / "world"

    print(f"migrate-data-root: moving legacy campaign data under "
          f"{data_root}")
    if not pairs and not (cand_old.is_dir() and cand_old.exists()):
        print("nothing to migrate — no legacy data dirs found (already "
              "migrated, or a fresh checkout)")
        return 0

    # ---- refusal gates, ALL checked before ANY move (all-or-nothing) ----
    blockers = []
    if legacy_world.is_dir():
        week_flag = legacy_world / ".week-run-flag"
        if week_flag.is_file():
            blockers.append(f"a week run is in flight or was interrupted "
                            f"({week_flag}) — let it finish / boot the "
                            "studio once to restore, then migrate")
        home_lock = legacy_world / ".engine-home.lock"
        if spine.file_lock_held(home_lock):
            blockers.append(f"the engine home is held by a running process "
                            f"({home_lock})")
    old_campaigns = parent / "campaigns"
    if old_campaigns.is_dir():
        held = [lk.name for lk in
                (old_campaigns / ".locks").glob("*.lock")
                if spine.file_lock_held(lk)] \
            if (old_campaigns / ".locks").is_dir() else []
        if held:
            blockers.append("campaign lock(s) held by a running writer: "
                            + ", ".join(held))
    collisions = [str(new) for _, new in pairs if new.exists()]
    if collisions:
        blockers.append("target(s) already exist (the migration never "
                        "merges): " + ", ".join(collisions))
    tracked = None
    if cand_old.is_dir():
        try:
            tracked = _git_tracked_candidates(parent / "dnd-mapping")
        except RuntimeError as e:
            blockers.append(str(e))
    if blockers:
        print("REFUSED — nothing was moved:")
        for b in blockers:
            print(f"  - {b}")
        return 1

    # ---- the moves ----
    n = 0
    for old, new in pairs:
        if old.is_dir():
            _move_dir(old, new, out)
            n += 1
    if cand_old.is_dir() and tracked is not None:
        prefix = "worlds/candidates/"
        scratch_moved = 0
        for f in sorted(cand_old.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(cand_old).as_posix()
            if prefix + rel in tracked:
                continue  # curated and tracked — stays
            dest = cand_new / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest))
            scratch_moved += 1
        # prune dirs the scratch move emptied (tracked content remains)
        for d in sorted((p for p in cand_old.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
        if scratch_moved:
            out(f"  moved {scratch_moved} untracked file(s) {cand_old}")
            out(f"    -> {cand_new} (tracked curated candidates stayed put)")
            n += 1
    if n == 0:
        print("nothing to migrate — no legacy data dirs found (already "
              "migrated, or a fresh checkout)")
        return 0
    print()
    print(f"done: {n} location(s) migrated. Notes:")
    print("  - an Obsidian vault opened from the old campaigns/ path must be")
    print(f"    re-opened once at {data_root / 'campaigns'}; obsidian:// links")
    print("    the app builds follow the move on their own")
    print("  - re-run python studio/doctor.py to see all checks green")
    return 0


# ------------------------------------------------------------------- llm key


def find_key():
    """The resolved LLM key, or None — llm.env / $LLM_API_KEY first, then
    the legacy Z.ai discovery in llm_client ($ZAI_API_KEY, then the ZCode
    CLI's ~/.zcode/v2/provider_config.json). None when nothing fits."""
    import llm_client
    try:
        return llm_client.resolve_config()["key"] or None
    except Exception:  # noqa: BLE001 — a config error surfaces in check_llm
        return None


def _llm_cfg():
    """The effective llm_client config, or a findings string on failure —
    the single place check_llm/check_llm_key read provider config."""
    import llm_client
    try:
        return llm_client.resolve_config(), None
    except Exception as e:  # noqa: BLE001 — a bad llm.env is the finding
        return None, str(e)


def _key_source(cfg):
    """Where the key came from, for the detail line (resolve_config does
    not say; the source is checkable by construction)."""
    import llm_client
    if (os.environ.get("LLM_API_KEY") or "").strip():
        return "$LLM_API_KEY"
    if llm_client.read_env_file().get("LLM_API_KEY", "").strip():
        return "llm.env"
    if os.environ.get("ZAI_API_KEY"):
        return "$ZAI_API_KEY"
    return "the ZCode CLI config (~/.zcode/v2/provider_config.json)"


def probe_llm(key, url=None, model=None):
    """ONE tiny live probe to the resolved endpoint (max_tokens 16,
    'Reply OK'). Returns (ok, detail, fix) with the situation classified:
    200 fine / 1113 wrong-door / 401 invalid / anything else reported as-is.
    The 1113 door-hint is Z.ai-specific, so it fires only for the Z.ai URL."""
    url = url or API_URL
    model = model or PROBE_MODEL
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 16,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            resp.read()
        if url == API_URL:
            return True, "coding endpoint fine", None
        return True, f"endpoint fine ({model} at {url})", None
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — body is best-effort context
            pass
        code = None
        try:
            err = json.loads(raw)
            node = err.get("error") if isinstance(err, dict) else None
            code = (node.get("code") if isinstance(node, dict) else None) \
                or (err.get("code") if isinstance(err, dict) else None)
        except ValueError:
            pass
        if code is not None and str(code) == "1113":
            return False, "plan pool reachable but balance/subscription door " \
                          "wrong — use the coding endpoint", \
                   f"requests must hit {API_URL} (the studio already does); " \
                   "the configured key is probably not a coding-plan key"
        if e.code == 401:
            return False, "key invalid (HTTP 401)", \
                   "check the key in llm.env / $LLM_API_KEY, or the legacy " \
                   "ZAI_API_KEY / ~/.zcode/v2/provider_config.json"
        return False, f"endpoint returned HTTP {e.code}: {raw[:120] or e.reason}", \
               f"check LLM_BASE_URL and LLM_MODEL in llm.env for {url}"
    except Exception as e:  # noqa: BLE001 — network errors are findings, not crashes
        return False, f"endpoint unreachable: {e}", "check the network, then retry"


def check_llm(probe=None):
    cfg, err = _llm_cfg()
    if err:
        return _mk("llm key", False, err,
                   fix="fix llm.env (see llm.env.example) — LLM_MODEL is "
                       "required when LLM_BASE_URL is not the default",
                   required=False)
    if not cfg["key"]:
        return _mk("llm key", False, "no key found",
                   fix="copy llm.env.example to llm.env and set LLM_API_KEY "
                       "(any OpenAI-compatible provider; a local Ollama works "
                       "too), or the legacy $ZAI_API_KEY / "
                       "~/.zcode/v2/provider_config.json — generation and "
                       "live weeks need a key; everything else works without",
                   required=False)
    try:
        ok, detail, fix = (probe or probe_llm)(cfg["key"], cfg["url"], cfg["model"])
    except Exception as e:  # noqa: BLE001 — a throwing probe is a finding
        ok, detail, fix = False, f"probe failed: {e}", None
    return _mk("llm key", ok, f"{detail} [key: {_key_source(cfg)}]",
               fix=fix, required=False)


def check_llm_key():
    """Presence-only llm check (no network) — the health strip's quick mode:
    a page load must not spend tokens or wait on an API round-trip."""
    cfg, err = _llm_cfg()
    if err:
        return _mk("llm key", False, err,
                   fix="fix llm.env (see llm.env.example) — LLM_MODEL is "
                       "required when LLM_BASE_URL is not the default",
                   required=False)
    if not cfg["key"]:
        return _mk("llm key", False, "no key found",
                   fix="copy llm.env.example to llm.env and set LLM_API_KEY "
                       "(any OpenAI-compatible provider; a local Ollama works "
                       "too), or the legacy $ZAI_API_KEY / "
                       "~/.zcode/v2/provider_config.json — generation and "
                       "live weeks need a key; everything else works without",
                   required=False)
    return _mk("llm key", True,
               f"key found in {_key_source(cfg)} (presence check — not probed)",
               required=False)


# ------------------------------------------------------------------- report


def run_all_checks(probe=None, modules=None, live_llm=True):
    checks = [check_python(), check_imports(modules), check_node(), check_uv()]
    checks.extend(check_engines())
    checks.extend(check_tool_trees())
    checks.append(check_week_run())
    checks.append(check_data_root())
    checks.append(check_vale())
    checks.append(check_campaigns())
    checks.append(check_vault())
    checks.append(check_workspace_ignore())
    checks.append(check_campaign_system())
    checks.append(check_llm(probe) if live_llm else check_llm_key())
    return checks


def report(probe=None, modules=None, live_llm=True):
    checks = run_all_checks(probe=probe, modules=modules, live_llm=live_llm)
    overall = all(c["ok"] for c in checks if c["required"])
    return {"ok": overall, "checks": checks}


# --------------------------------------------------------------------- --fix


def _node_import_ok(entry: Path, timeout: int = ENGINE_PROBE_TIMEOUT_S):
    """(loads, why) — can node import this module at all? Broken or
    half-installed node_modules shows up as a resolution error here.
    Dynamic import works for ESM and CJS alike. List-form args (the parent
    path contained `&` until 2026-10-04)."""
    probe = ("import(require('url').pathToFileURL(process.argv[1]).href)"
             ".then(function () {}, function (e) {"
             "console.error(String(e)); process.exit(3); })")
    cmd = ["node", "-e", probe, str(entry)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except FileNotFoundError:
        return True, "node not on PATH — cannot probe (skipped)"
    except subprocess.TimeoutExpired:
        return True, "import did not return in time (assumed loadable)"
    if proc.returncode == 0:
        return True, f"{entry.name} loads"
    lines = [ln for ln in (proc.stderr or "").strip().splitlines() if ln]
    why = lines[-1] if lines else f"exit {proc.returncode}"
    return False, f"{entry.name} will not load: {why[:120]}"


def settlemaker_ok(root=None):
    """Probe (a): the tree exists but dist/index.js won't import → the
    documented npm repair applies. A missing tree is NOT a breakage
    (the engine check already reports that)."""
    root = root or SETTLEMAKER_DIR
    if not root.is_dir():
        return True, "not installed"
    entry = root / "dist" / "index.js"
    if not entry.is_file():
        return False, "dist/index.js missing — the tool tree is incomplete"
    return _node_import_ok(entry)


def fmg_ok(root=None):
    """Probe (b): the tree exists but node_modules/vitest/vitest.mjs is
    missing → the headless FMG pipeline cannot run → repair. Ticket 60
    adds the half marker files never covered: the installed headless
    runner must match the tracked vendor-patches copy byte-for-byte (a
    patch committed after the tree existed used to reach no machine that
    had already run setup), and the live checkout version is named in the
    detail — the same resolution the world-regen gate compares against the
    campaign's recorded version."""
    root = root or FMG_DIR
    if not root.is_dir():
        return True, "not installed"
    if not (root / "node_modules" / "vitest" / "vitest.mjs").is_file():
        return False, "node_modules/vitest/vitest.mjs missing — install broken or incomplete"
    tracked = (PARENT / "dnd-mapping" / "vendor-patches" / "fmg"
               / "headless-export.test.ts")
    installed = root / "src" / "generators" / "headless-export.test.ts"
    if tracked.is_file() and installed.is_file() \
            and tracked.read_bytes() != installed.read_bytes():
        return False, ("the installed headless runner does not match the "
                       "tracked vendor-patches copy — run python "
                       "setup-tools.py to re-apply it")
    import engines
    live = engines.fmg_checkout_version(root)
    return True, ("node_modules/vitest/vitest.mjs present"
                  + (f", checkout at {live}" if live else ""))


def _npm_cmd(args):
    """[npm, *args] — or through its .cmd/.bat shim, which CreateProcess
    cannot exec directly (same story as _version_of above). None when npm
    is not on PATH at all."""
    where = shutil.which("npm")
    if not where:
        return None
    if where.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", where, *args]
    return ["npm", *args]


MISSING_NPM_ADVICE = (
    "install Node (https://nodejs.org — npm comes with it), then re-run "
    "python studio/doctor.py --fix")


def plan_fixes(rep=None):
    """What --fix would do: a list of plans
    {name, why, cmd, cwd, env, do, advice} — do=True plans are safe to run
    (the npm repair, the pip line); do=False plans are advice only, because
    they write canon (assemble.py) or need something the doctor cannot
    install (a missing python/node itself, an LLM key)."""
    rep = rep or report()
    plans = []
    by_name = {c["name"]: c for c in rep["checks"]}

    imports = by_name.get("imports")
    if imports and not imports["ok"]:
        plans.append({
            "name": "python packages",
            "why": imports["detail"],
            "cmd": [sys.executable, "-m", "pip", "install", "-r",
                    str(STUDIO_DIR / "requirements.txt")],
            "cwd": None, "env": None, "do": True, "advice": None,
        })

    for label, root, probe in (("settlemaker", SETTLEMAKER_DIR, settlemaker_ok),
                               ("Fantasy-Map-Generator", FMG_DIR, fmg_ok)):
        ok, why = probe(root)
        if not ok:
            base = NPM_INSTALL_FMG if label == "Fantasy-Map-Generator" \
                else NPM_INSTALL_CMD
            cmd = _npm_cmd(base[1:])
            plans.append({
                "name": f"npm install: {label}",
                "why": why,
                "cmd": cmd,
                "cwd": root,
                "env": None,
                "do": cmd is not None,
                "advice": None if cmd is not None else MISSING_NPM_ADVICE,
            })

    campaigns = by_name.get("campaigns")
    if campaigns and not campaigns["ok"]:
        plans.append({
            "name": "campaigns",
            "why": campaigns["detail"],
            "cmd": None, "cwd": None, "env": None, "do": False,
            "advice": "run  python studio/assemble.py  to build "
                      "campaigns/ruravaqan from the engine artifacts "
                      "(the doctor never auto-runs it — it writes canon)",
        })
    return plans


def _quote(cmd):
    return " ".join(f'"{c}"' if (" " in c or "&" in c) else c for c in cmd)


def apply_fixes(plans, dry=False, out=print):
    """Run the do=True plans (or narrate them when dry); print the
    advice-only ones. Returns the number of repair commands attempted."""
    ran = 0
    for p in plans:
        if not p.get("do"):
            out(f"  [advice] {p['name']}: {p['why']}")
            out(f"           {p.get('advice') or p.get('fix') or ''}")
            continue
        if dry:
            out(f"  [would run] {p['name']}: {_quote(p['cmd'])}"
                + (f"  (cwd={p['cwd']})" if p.get("cwd") else ""))
            continue
        out(f"  [fixing] {p['name']}: {p['why']}")
        out(f"           running: {_quote(p['cmd'])}"
            + (f"  (cwd={p['cwd']})" if p.get("cwd") else ""))
        ran += 1
        try:
            proc = _run_repair(p["cmd"], cwd=p.get("cwd"), env=p.get("env"))
        except Exception as e:  # noqa: BLE001 — a failed repair is a finding
            out(f"           FAILED: {e}")
            continue
        tail = (proc.stdout or "").strip().splitlines()[-5:]
        for line in tail:
            out(f"           | {line[:110]}")
        if proc.returncode == 0:
            out("           done (exit 0)")
        else:
            out(f"           FAILED (exit {proc.returncode}) — see the "
                "output above; the fix lines in the report still apply")
    return ran


def _run_repair(cmd, cwd=None, env=None, timeout=600):
    """List-form subprocess for repairs — never shell=True (the parent path
    contained `&` until 2026-10-04)."""
    if env:
        env = {**os.environ, **{k: str(v) for k, v in env.items()}}
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _print_report(rep):
    print("Campaign Studio doctor")
    print()
    for c in rep["checks"]:
        tag = "ok  " if c["ok"] else ("FAIL" if c["required"] else "info")
        print(f"  [{tag}] {c['name']:<22} {c['detail']}")
        if not c["ok"] and c.get("fix"):
            print(f"           fix: {c['fix']}")
        elif c["ok"] and c.get("fix"):
            print(f"           note: {c['fix']}")
    print()
    blocked = [c for c in rep["checks"] if not c["ok"] and c["required"]]
    optional = [c for c in rep["checks"] if not c["ok"] and not c["required"]]
    if not blocked and not optional:
        print("All clear. Boot with:  python studio/run.py  (or double-click studio/start.bat)")
    elif not blocked:
        print("The studio will boot (read-only without an LLM key). "
              "See the info lines above for generation.")
        print("Boot with:  python studio/run.py  (or double-click studio/start.bat)")
    else:
        print("Not ready — fix the FAIL lines above, then re-run "
              "python studio/doctor.py.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Check every prerequisite of Campaign Studio and say "
                    "precisely which one is missing.")
    ap.add_argument("--json", action="store_true",
                    help="print the API shape instead of the human report")
    ap.add_argument("--fix", action="store_true",
                    help="perform the safe repairs (npm install in broken "
                         "tool trees, pip install -r studio/requirements.txt); "
                         "everything else is printed as advice — assemble.py "
                         "is never auto-run")
    ap.add_argument("--migrate-data-root", action="store_true",
                    help="move the legacy data dirs (campaigns/, the engine "
                         "home, chronicles, studio logs, map scratch) under "
                         "the campaign-data root, then exit; refused while "
                         "any lock is held, never merges, idempotent "
                         "(ticket 35)")
    args = ap.parse_args(argv)
    if args.migrate_data_root:
        return migrate_data_root()
    rep = report()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        _print_report(rep)
    if args.fix:
        plans = plan_fixes(rep)
        if not plans:
            print("Nothing to fix automatically — nothing above has a "
                  "doctor-safe repair.")
        else:
            print(f"{len(plans)} repair(s) found:")
            apply_fixes(plans)
            print()
            print("Re-checking…")
            rep = report()
            _print_report(rep)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
