"""Subprocess wrappers for the three engines (D3, D4) + the shared engine
home's orchestration.

The parent path contained `&` (Documents/D&D) until the 2026-10-04
rename; every invocation stays list-form args with cwd=<engine root> —
never shell=True. Both wrappers
capture stdout+stderr line by line (stderr is merged into stdout so engine
tracebacks land in the job log) and stream each line to an optional
on_line callback, which is what keeps a live week's log tail moving.

Engine roots resolve relative to the studio parent — studio is a fourth
sibling of dnd-mapping / agentic-simulator / verification (D1). The engine
HOME (state, flags, locks, replay) and the chronicle output are DATA, not
code: they live under the campaign-data root (spine.data_root(), ticket
35), and every subprocess this module launches carries CAMPAIGN_DATA_ROOT
so the engine/battlemap CLIs — which do not import spine — land on the
same root.

The engine-home half (below the subprocess wrappers) owns everything that
touches the SHARED scratch: copy-in, the interrupted-week flag and its
boot restore, chronicle meta parsing over the home's files, and the two
run flows (week proposal, forecast) that routers drive as jobs. Routes
belong in the router; everything that would run identically from a CLI
lives here.
"""

import collections
import contextlib
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import forecasts
import llm_client
import proposals
import publication
import spine
import timeline as timeline_store
import yaml

STUDIO_DIR = Path(__file__).resolve().parent
PARENT = STUDIO_DIR.parent

DND_MAPPING = PARENT / "dnd-mapping"
AGENTIC_SIMULATOR = PARENT / "agentic-simulator"
VERIFICATION = PARENT / "verification"

# The shared engine home and chronicle output: DATA under the campaign-data
# root, not inside the engine's code tree (ticket 35). Module globals so
# tests can relocate them (patch engines.ENGINE_HOME / engines.CHRONICLE_HOME
# the way spine.CAMPAIGNS_ROOT is patched); every helper below reads them at
# call time.
ENGINE_HOME = spine.data_root() / "engine-home"
CHRONICLE_HOME = spine.data_root() / "chronicle"

_log = logging.getLogger("studio.engines")

# The simulator engine home (agentic-simulator/world/state.yaml) is ONE
# scratch file shared by every campaign (D4): copy-in before an engine run,
# copy-out after. Every copy-in + engine-run + copy-out sequence therefore
# holds this process-wide lock — the week job for its whole run, the
# views/why routes around their copy-in + subprocess. Per-campaign locks
# (spine.campaign_lock) cannot protect it: two campaigns share the home.
# Ordering: campaign lock (outer, held by week jobs) then engine lock —
# never the reverse, or a views call could deadlock a running week.
#
# The lock records WHO holds it (ticket 24 R2): the submit-time probes in
# post_week/post_forecast 409 on CROSS-campaign contention (this lock is
# global, the queue is per-campaign) but must let a same-campaign request
# join its own campaign's queue behind the running week/forecast. A holder
# of None means "not a week/forecast that named itself" (a direct
# `with ENGINE_LOCK:`) — the probes treat that as cross-campaign, which is
# today's behavior for any unknown holder.
class _HolderTrackingLock:
    """threading.Lock plus the holding campaign's name. acquire() takes the
    same (blocking, timeout) shape as threading.Lock plus an optional holder
    recorded for probes to read; release() clears it. The context-manager
    form acquires without a holder (unknown), exactly like a bare lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._mu = threading.Lock()
        self._holder = None

    def acquire(self, blocking=True, timeout=-1, holder=None) -> bool:
        ok = self._lock.acquire(blocking, timeout)
        if ok:
            with self._mu:
                self._holder = holder
        return ok

    def release(self) -> None:
        with self._mu:
            self._holder = None
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    @property
    def holder(self):
        """The campaign name the current holder passed, or None (unknown)."""
        with self._mu:
            return self._holder

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *exc):
        self._lock.release()


ENGINE_LOCK = _HolderTrackingLock()


def run_py(engine_root: Path, script: str, *args, timeout: int = 600,
           on_line=None, env: dict = None):
    # -u: tick.py's per-faction progress lines are the week UI's log tail —
    # block-buffered through a pipe they arrive only when the process exits
    cmd = [sys.executable, "-X", "utf8", "-u", script, *args]
    return _run(cmd, engine_root, timeout, on_line, env)


def node_executable() -> str:
    """The node binary Popen can actually launch. CreateProcess (Popen with
    shell=False) resolves only .exe, while shutil.which honors PATHEXT and
    can hand back a version manager's .cmd shim — doctor would green-light
    it and every map job would die with 'engine not found for node'."""
    return shutil.which("node.exe") or shutil.which("node") or "node"


def run_node(engine_root: Path, *args, timeout: int = 600, on_line=None,
             env: dict = None):
    """[node, *args] with cwd=engine_root. Returns (rc, lines)."""
    return _run([node_executable(), *args], engine_root, timeout, on_line,
                env)


def fmg_dir() -> Path:
    """The FMG clone inside dnd-mapping — read at call time so tests can
    relocate engines.DND_MAPPING."""
    return DND_MAPPING / "tools" / "Fantasy-Map-Generator"


WEEK_FLAG_NAME = ".week-run-flag"
ENGINE_HOME_LOCK_NAME = ".engine-home.lock"

# Set on every engine process the studio launches under engine_lock_logged:
# the parent server already holds the engine-home lock file, and a child
# that re-took it would deadlock on its own parent. The engine's standalone
# mains (tick.py --live, effects.py --apply, ingest.py --apply) skip their
# own lock claim when they see it (store.py standalone_home_lock).
STUDIO_ENGINE_HOME_HELD = "STUDIO_ENGINE_HOME_HELD"


def week_flag_path() -> Path:
    """Marker for a week run in flight (the engine job writes it once it
    HOLDS the engine home and removes it on every exit path; server.py
    restores the engine home on boot when it is left behind). Read at call
    time so tests can relocate engines.ENGINE_HOME."""
    return ENGINE_HOME / WEEK_FLAG_NAME


def engine_home_lock_path() -> Path:
    """The cross-process lock over the shared engine home:
    ENGINE_LOCK excludes this process's threads only, and the home is also
    touched by the engine's standalone CLIs (tick.py --live, effects.py
    --apply) as separate processes. Read at call time so tests can
    relocate engines.ENGINE_HOME."""
    return ENGINE_HOME / ENGINE_HOME_LOCK_NAME


def engine_home_lock_held() -> bool:
    """Whether any process holds the engine-home lock file (fresh-handle
    probe, spine.file_lock_held) — tests and diagnostics assert on it."""
    return spine.file_lock_held(engine_home_lock_path())


# The rest of the engine home's layout is owned HERE the same way: routers
# and the doctor must rebuild none of it. All read at call time so tests can
# relocate ENGINE_HOME / CHRONICLE_HOME.

def engine_state_path() -> Path:
    """The shared engine home's scratch state.yaml (campaigns own canon)."""
    return ENGINE_HOME / "state.yaml"


def engine_state_backup_path() -> Path:
    """The pre-copy-in engine state, kept so a boot after a crashed week can
    restore the home (restore_interrupted_week)."""
    return ENGINE_HOME / "state.yaml.bak"


def engine_replay_path(week) -> Path:
    """The resolver's newest replay trail for one week (scratch; rides with
    the proposal bundle). Reruns version like chronicles — after a rewind
    and re-run the record is week-N-2.yaml — so return the highest suffix
    present; a fresh week still resolves to plain week-N.yaml."""
    d = ENGINE_HOME / "replay"
    best = d / f"week-{week}.yaml"
    n = 2
    while (d / f"week-{week}-{n}.yaml").is_file():
        best = d / f"week-{week}-{n}.yaml"
        n += 1
    return best


def engine_chronicle_dir() -> Path:
    return CHRONICLE_HOME


def map_sites_dir() -> Path:
    """battlemap.py's scratch home for generated sites (ex
    dnd-mapping/worlds/sites): campaign-data/maps/sites. Canon copies live
    in the campaign repos; this home is regenerated on demand. The
    battlemap CLI derives the same default from CAMPAIGN_DATA_ROOT (set on
    every subprocess _run launches) and accepts an explicit --dir."""
    return spine.data_root() / "maps" / "sites"


def candidate_drop_dir() -> Path:
    """import_fmg.py's candidate-yaml drop dir (ex
    dnd-mapping/worlds/candidates): campaign-data/scratch/map-candidates.
    The tracked half of the old dual-status dir — curated geography.yamls,
    index.md, screenshots — stays put under worlds/candidates/; curation is
    now an ordinary copy + git add (ticket 35)."""
    return spine.data_root() / "scratch" / "map-candidates"


def legacy_data_dirs(parent: Path | None = None,
                     data_root: Path | None = None) -> list:
    """Pre-ticket-35 data locations that still exist, as [(legacy, new)]
    pairs: what the boot banner names and doctor --migrate-data-root moves.
    Empty when everything already lives under the data root. Both roots
    parameterizable so tests can fabricate a legacy tree in tmp; the
    default derives today's layout. dnd-mapping/worlds/candidates is
    deliberately NOT here: the dir legitimately survives the migration
    (its tracked half stays); only its untracked contents move."""
    parent = parent or PARENT
    data_root = data_root or spine.data_root()
    pairs = [
        (parent / "campaigns", data_root / "campaigns"),
        (parent / "agentic-simulator" / "world", data_root / "engine-home"),
        (parent / "agentic-simulator" / "chronicle", data_root / "chronicle"),
        (parent / "studio" / "logs", data_root / "logs"),
        (parent / "dnd-mapping" / "worlds" / "sites",
         data_root / "maps" / "sites"),
        (parent / "dnd-mapping" / "out-gen-city-smoke",
         data_root / "scratch" / "gen-city-smoke"),
    ]
    return [(old, new) for old, new in pairs if old.exists()]


# Transcript cap: week staging re-parses for HOLDS lines, errors read the
# tail; a chatty node worldgen used to pin the whole transcript in RAM.
# Past the cap, head lines drop with a marker.
RUN_LINE_CAP = 4000


def tail_detail(lines: list, n: int = 8) -> str:
    """The standard engine-failure detail: the last n transcript lines
    joined with ' | ' (one place, so the tail convention stays uniform)."""
    return " | ".join(lines[-n:])


def _run(cmd: list, cwd: Path, timeout: int, on_line, env: dict = None) -> tuple:
    # CAMPAIGN_DATA_ROOT is set on EVERY engine subprocess (ticket 35): the
    # engine and battlemap CLIs resolve their homes from the env — they do
    # not import spine — so this, not cwd, is what keeps them on the
    # studio's root. The caller's env (FMG_SEED, STUDIO_ENGINE_HOME_HELD, a
    # test override) merges over it.
    merged = {"CAMPAIGN_DATA_ROOT": str(spine.data_root())}
    if env:
        merged.update({k: str(v) for k, v in env.items()})
    env = {**os.environ, **merged}
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            # POSIX: the child gets its own process group so the timeout
            # kill can take the whole tree (see _kill); Windows uses
            # taskkill /T and must not pass this flag
            start_new_session=(os.name != "nt"),
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"engine not found for {' '.join(cmd[:2])}: {e}")

    lines = collections.deque(maxlen=RUN_LINE_CAP)
    dropped = 0
    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        # kill the whole tree: vitest/node children inherit the stdout pipe,
        # and an orphaned worker holding it never lets the reader loop reach
        # EOF — the thread (and with it ENGINE_LOCK) hangs until restart
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        else:
            # the child leads its own process group (start_new_session), so
            # killpg reaches grandchildren; the old bare proc.kill() left
            # them orphaned on the pipe, wedging ENGINE_LOCK until restart.
            # Already-dead children race here — a lookup miss means nothing
            # is left to kill.
            try:
                os.killpg(os.getpgid(proc.pid), 9)  # SIGKILL
            except (ProcessLookupError, PermissionError):
                proc.kill()

    watchdog = threading.Timer(timeout, _kill)
    watchdog.start()
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if len(lines) == RUN_LINE_CAP:
                dropped += 1
            lines.append(line)
            if on_line is not None:
                on_line(line)
        rc = proc.wait()
    except BaseException:
        _kill()
        proc.wait()
        raise
    finally:
        watchdog.cancel()
    if timed_out.is_set():
        lines.append(f"[studio] engine killed after {timeout}s timeout")
        if rc == 0:  # a killed child usually exits nonzero; 0 still means timeout
            rc = 124
    out = list(lines)
    if dropped:
        out.insert(0, f"[studio] {dropped} earlier engine lines dropped "
                      f"(kept the last {RUN_LINE_CAP})")
    return rc, out


def copy_file(src: Path, dst: Path) -> Path:
    """Copy-in/copy-out primitive (D4): place one artifact, mkdir as needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def copy_file_atomic(src: Path, dst: Path) -> Path:
    """copy_file for files a reader may open mid-copy (campaign canon:
    simulation/state.yaml). tmp + os.replace in the destination's own
    directory, so a concurrent GET never sees a half-written state and a
    crash mid-copy never truncates it. The replace retries briefly: a
    reader holding the destination open makes Windows raise
    PermissionError for an instant, and the reader is the innocent party."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    for attempt in range(3):
        try:
            os.replace(tmp, dst)
            return dst
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
    return dst


def run_check(path, timeout: int = 180) -> list:
    """verification/check.py's ladder over one file (structural always; vale
    when installed). Returns the findings list — [] when clean. The ONE
    invocation of the checker (list-form args, cwd=verification, tmp out
    dir); review.py and loregen.py both used to hand-roll it.

    Raises RuntimeError when check.py produces no findings.json — callers
    decide whether that is advisory (a note) or fatal."""
    out_dir = Path(tempfile.mkdtemp(prefix="studio-check-"))
    try:
        rc, _lines = run_py(VERIFICATION, "check.py", str(path),
                            "--out", str(out_dir), timeout=timeout)
        findings_file = out_dir / "findings.json"
        if findings_file.is_file():
            return json.loads(findings_file.read_text(encoding="utf-8"))
        raise RuntimeError(f"check.py produced no findings.json (rc={rc})")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# ------------------------------------------------ engine-home orchestration
#
# Everything below touches the SHARED engine home (agentic-simulator/world):
# copy-in, the interrupted-week flag and its boot restore, chronicle meta
# over the home's scratch files, and the two run flows the simulation
# router drives as jobs. The week flow's contract (output becomes a pending
# proposal bundle, never a direct canon write) lives here with it.

WEEK_TIMEOUT = 1800  # a live week is minutes of LLM calls

_HOLDS_RE = re.compile(r"^\s*([a-z_]+)\s+HOLDS\b")


def run_record_identity(engine: str) -> dict:
    """The run-identity half of a run record: engine name + provider
    identity, best-effort (no secrets, never fatal)."""
    rec = {"engine": engine, "model": None, "base_url": None}
    try:
        cfg = llm_client.resolve_config()
        rec["model"] = cfg.get("model")
        rec["base_url"] = cfg.get("url")
    except Exception:  # noqa: BLE001 — identity is best-effort, no secrets
        pass
    return rec


# The first acquire window before the loud "waiting" line, split out so a
# test can shrink it (most waits are a queued same-campaign run finishing
# within seconds; a real 5s + 93min pair cannot be waited out in a test).
ENGINE_LOCK_FIRST_WAIT_S = 5.0


def engine_lock_deadline_s() -> float:
    """The dead-man cap on a queued engine job's wait for the home: 3x the
    longest legal run (one live week) plus its watchdog margin. A function
    (not a constant) so tests can shrink it too."""
    return 3 * (WEEK_TIMEOUT + 60)


@contextlib.contextmanager
def engine_lock_logged(log, why: str, holder=None):
    """The engine home is one shared scratch across campaigns: jobs that
    passed the submit-time courtesy check can still queue here behind
    another campaign's run. Log the wait so a slow run is never silent,
    and bound it (a run aborted by its own watchdog eventually releases).
    holder names the campaign taking the home, so the submit-time probes
    can tell same-campaign contention (queue behind it) from cross (409).

    The section also takes the home's CROSS-PROCESS lock file (ticket 26):
    ENGINE_LOCK excludes studio threads only, and the engine's standalone
    CLIs (tick.py --live, effects.py --apply) touch the same world/ as
    separate processes — a standalone run interleaving with a studio week
    races state.yaml last-writer-wins and can leak a foreign chronicle
    into the proposal's new_files diff. The engine processes launched
    inside this section carry STUDIO_ENGINE_HOME_HELD so they do not
    re-take the lock their parent holds (store.standalone_home_lock)."""
    if not ENGINE_LOCK.acquire(timeout=ENGINE_LOCK_FIRST_WAIT_S, holder=holder):
        deadline = engine_lock_deadline_s()
        log(f"waiting for the engine home — {why} is in flight; this run "
            f"starts when it finishes (the job log goes quiet meanwhile)")
        if not ENGINE_LOCK.acquire(timeout=deadline, holder=holder):
            raise RuntimeError(
                f"the engine home stayed busy for over {int(deadline // 60)} "
                f"minutes — giving up without running anything")
    home = None
    try:
        home, held = spine.try_file_lock(engine_home_lock_path())
        if held:
            raise RuntimeError(
                "the engine home is held by a process outside the studio (a "
                "standalone tick.py --live or effects.py --apply run) — let "
                "it finish, then submit again. Nothing was run or spent.")
        yield
    finally:
        spine.release_file_lock(home)
        ENGINE_LOCK.release()


def copy_state_in(name: str) -> None:
    """D4: campaign state.yaml -> engine home, backing up the engine's
    previous state first, and clearing the home's run OUTPUTS (chronicles,
    replay trails). The home is shared scratch: leftover projection files
    from a discarded forecast would collide with a real week's chronicle
    names (-2 suffixes) and a stale replay/week-N.yaml would ride into an
    unrelated campaign's proposal as its replay trail."""
    src = spine.campaign_dir(name) / "simulation" / "state.yaml"
    engine_state = engine_state_path()
    engine_state.parent.mkdir(parents=True, exist_ok=True)
    if engine_state.is_file():
        copy_file(engine_state, engine_state_backup_path())
    chronicle_dir = engine_chronicle_dir()
    if chronicle_dir.is_dir():
        for p in chronicle_dir.glob("*.md"):
            p.unlink(missing_ok=True)
    replay_dir = engine_replay_path(0).parent
    if replay_dir.is_dir():
        for p in replay_dir.glob("week-*.yaml"):
            p.unlink(missing_ok=True)
    copy_file(src, engine_state)


# ------------------------------------------------- interrupted-week flag

# One module lock over the flag file's write and its compare-and-unlink
# clear (ticket 26): both run under ENGINE_LOCK today, but the read->unlink
# must stay atomic against a concurrent write even if another caller ever
# slips between them — an unserialized clear could delete the OTHER run's
# live flag (and with it, boot restore).
_WEEK_FLAG_LOCK = threading.Lock()


def write_week_flag(name: str) -> None:
    """Mark a week run in flight (content: campaign name + ISO timestamp).
    Written by the engine job only once it HOLDS the engine home, so the
    campaign named here is always the run that owns the home — a queued
    job waiting its turn must not overwrite the running job's attribution
    (boot restore would blame the wrong campaign)."""
    with _WEEK_FLAG_LOCK:
        flag = week_flag_path()
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(json.dumps({
            "campaign": name,
            "started": datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")


def clear_week_flag(name: str | None = None) -> None:
    """Clear the in-flight flag. With a campaign name, only clear the flag
    THIS campaign wrote: the flag is one shared file, and two queued engine
    jobs (a week behind a forecast) both write it — the first finisher must
    not delete the second's marker, or a crash during the second run skips
    boot restore entirely. The compare-and-unlink runs under the same
    module lock as write_week_flag, so the two can never interleave."""
    with _WEEK_FLAG_LOCK:
        flag = week_flag_path()
        if name is not None:
            try:
                doc = json.loads(flag.read_text(encoding="utf-8"))
                if not (isinstance(doc, dict)
                        and doc.get("campaign") == name):
                    return  # a later run's flag — leave it alone
            except (OSError, ValueError):
                pass  # unreadable/absent: the unlink below settles it
        try:
            flag.unlink()
        except FileNotFoundError:
            pass


def restore_interrupted_week():
    """Boot-time repair (server.py lifespan): a left-behind week flag means
    the server died mid-week. Restore the engine home's state.yaml from its
    .bak (when one exists), delete the flag, and warn loudly. Returns
    {"campaign","restored"} or None when no flag was left.

    The flag's campaign attribution is trustworthy (ticket 26): it is
    written only while the engine lock is held, so the name in the flag is
    always the campaign whose copy-in made the current .bak — restore
    rolls the home back to exactly the state that campaign found."""
    flag = week_flag_path()
    flag = week_flag_path()
    if not flag.is_file():
        return None
    doc = {}
    try:
        loaded = json.loads(flag.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            doc = loaded
    except (OSError, ValueError):
        pass  # unreadable flag: still restore, still clear
    name = str(doc.get("campaign") or "unknown")
    state = engine_state_path()
    bak = engine_state_backup_path()
    if bak.is_file():
        copy_file_atomic(bak, state)
        _log.warning("interrupted week run for campaign %s — engine home "
                     "restored", name)
    else:
        _log.warning("interrupted week run for campaign %s — no state.yaml.bak "
                     "in the engine home, leaving it as it stands", name)
    clear_week_flag()
    return {"campaign": name, "restored": bak.is_file()}


# ------------------------------------------------------- chronicle parsing


def chronicle_meta(filenames: list) -> dict | None:
    """The primary chronicle for a run = the newest new file by mtime
    (sorted-by-name is wrong for reruns: 0005-a-2.md sorts before
    0005-a.md). Title parsed from the file's `# Week N — Title` heading."""
    if not filenames:
        return None
    chronicle_dir = engine_chronicle_dir()
    fn = max(filenames, key=lambda f: (chronicle_dir / f).stat().st_mtime)
    title = fn
    try:
        for ln in (chronicle_dir / fn).read_text(encoding="utf-8").splitlines()[:1]:
            m = re.match(r"#\s*Week\s+\d+\s*[—–-]\s*(.+?)\s*$", ln)
            if m:
                title = m.group(1)
    except (OSError, UnicodeDecodeError):
        pass
    week_m = re.match(r"(\d{4})", fn)
    return {"week": int(week_m.group(1)) if week_m else None,
            "title": title, "file": f"simulation/chronicle/{fn}"}


def held_from_chronicle(path: Path) -> set:
    """The chronicle's `Held (scheduler): a, b` line (tick.py write_chronicle)."""
    held = set()
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("Held") and ":" in ln:
            for tok in re.split(r"[,\s]+", ln.split(":", 1)[1].strip()):
                if tok:
                    held.add(tok)
    return held


# ------------------------------------------------------------ the run flows


def run_week_proposal(name: str, d: Path, log) -> dict:
    """Copy-in -> tick -> STAGE A PROPOSAL, run under the engine lock
    (post_week's job holds the campaign lock outside). Nothing is copied
    into the campaign here: the engine's output becomes a pending proposal
    bundle the DM reviews. Flag clearing happens however the block exits."""
    try:
        base_doc = spine.load_state(name)
        base_week = spine.state_clock_week(base_doc)
        # fail BEFORE any spend: a hand-edited float week (week: 4.0) would
        # otherwise crash tick.py mid-run after the faction calls, and re-
        # crash on every retry — each attempt a full week's API cost
        if not isinstance(base_week, int) or isinstance(base_week, bool):
            raise RuntimeError(
                f"meta.clock.week is {base_week!r}, not an integer — fix "
                "simulation/state.yaml before running a week")
        base_revisions = {
            "simulation/state.yaml": publication.file_revision(
                d / "simulation" / "state.yaml"),
            "lore/timeline.yaml": publication.file_revision(
                d / "lore" / "timeline.yaml"),
        }
        input_set_revisions = {
            "simulation/chronicle": publication.fingerprint_dir(
                d / "simulation" / "chronicle"),
            "lore/entities": publication.fingerprint_dir(
                d / "lore" / "entities"),
        }
        chronicle_dir = engine_chronicle_dir()

        copy_state_in(name)
        # capture AFTER copy-in: the copy clears the home's run outputs, so
        # this is the clean baseline every new chronicle is diffed against
        before = {p.name for p in chronicle_dir.glob("*.md")} \
            if chronicle_dir.is_dir() else set()
        log("state copied into engine home (previous engine state kept as "
            "state.yaml.bak)")
        log("running src/tick.py --live (scheduler, proposals, dice, chronicle)")
        # STUDIO_ENGINE_HOME_HELD: this parent holds the home's cross-process
        # lock for the whole section; the child re-taking it would deadlock
        rc, lines = run_py(AGENTIC_SIMULATOR, "src/tick.py",
                           "--live", timeout=WEEK_TIMEOUT, on_line=log,
                           env={STUDIO_ENGINE_HOME_HELD: "1"})
        if rc != 0:
            for ln in lines[-10:]:  # make sure the tail shows the failure
                log(ln)
            raise RuntimeError("tick --live failed rc=%s: %s"
                               % (rc, tail_detail(lines, 10)))

        engine_state = engine_state_path()
        try:
            new_state_text = engine_state.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"the engine wrote no state: {e}")
        after_doc = yaml.safe_load(new_state_text) or {}
        if not isinstance(after_doc, dict):
            raise RuntimeError("the engine's state does not parse as a "
                               "mapping — no proposal staged")
        proposed_week = spine.state_clock_week(after_doc)

        # invalid candidates never become approvable bundles (a float clock
        # would slip the != check — 5.0 == 5 — and poison the manifest)
        if not isinstance(proposed_week, int) or isinstance(proposed_week, bool) \
                or (base_week is not None and proposed_week != base_week + 1):
            raise RuntimeError(
                f"the engine did not advance exactly one week (base week "
                f"{base_week} -> {proposed_week}) — no proposal staged")
        new_files = sorted(
            (chronicle_dir.glob("*.md") and
             {p.name for p in chronicle_dir.glob("*.md")} - before)
            if chronicle_dir.is_dir() else set())
        if not new_files:
            raise RuntimeError("the engine wrote no chronicle — no proposal "
                               "staged (a run with no chronicle cannot be "
                               "reviewed)")
        n_factions = len(base_doc.get("factions") or [])
        proposal_failed = [ln for ln in lines
                           if "HOLDS (proposal failed" in ln]
        scheduler_held = {m.group(1) for ln in lines
                          if (m := _HOLDS_RE.match(ln))
                          and "proposal failed" not in ln}
        if n_factions and proposal_failed \
                and (len(proposal_failed) + len(scheduler_held)) >= n_factions:
            tail = " | ".join(proposal_failed[-3:])
            raise RuntimeError(
                "every faction call failed or held — reporting generation "
                f"failure rather than staging an empty week ({tail})")

        # the resolver's replay trail (normalized proposals + seed + rule
        # version) rides with the proposal when the engine wrote one
        replay_path = engine_replay_path(proposed_week)
        replay_text = replay_path.read_text(encoding="utf-8") \
            if replay_path.is_file() else None
        try:
            replay_doc = yaml.safe_load(replay_text) \
                if replay_text else None
        except yaml.YAMLError:
            replay_doc = None

        # the engine's RNG seed for the proposed week (replay identity; the
        # LLM responses themselves are nondeterministic and are NOT replayed)
        run_record = {
            **run_record_identity("agentic-simulator/src/tick.py --live"),
            "seed": ((after_doc.get("meta") or {}).get("rng_seeds") or {})
            .get(str(proposed_week)),
            "rule_version": replay_doc.get("rule_version")
            if isinstance(replay_doc, dict) else None,
        }

        chronicles = {fn: (chronicle_dir / fn).read_text(encoding="utf-8")
                      for fn in new_files}
        entries = timeline_store.build_week_entries(proposed_week,
                                                    after_doc.get("events")
                                                    or [])
        # parse the chronicle meta NOW, while the engine home still holds
        # this run's files — by accept time another campaign's week or a
        # forecast may have replaced them (the bundle is the durable copy)
        chron = chronicle_meta(new_files)
        pid = proposals.create(
            name, base_week=base_week, proposed_week=proposed_week,
            base_revisions=base_revisions,
            input_set_revisions=input_set_revisions,
            artifacts={"state": new_state_text, "chronicles": chronicles,
                       "entries": entries, "replay": replay_text},
            run_record=run_record, before_state=base_doc,
            chronicle_meta=chron)
        log(f"proposed week {proposed_week} staged ({pid}) — nothing in the "
            "campaign has changed; review it, then accept or discard")

        held = {m.group(1) for ln in lines if (m := _HOLDS_RE.match(ln))}
        for fn in new_files:
            held |= held_from_chronicle(chronicle_dir / fn)
        diff = proposals.diff_states(base_doc, after_doc, entries)
        return {
            "proposal_id": pid, "status": "pending",
            "week": proposed_week, "base_week": base_week,
            "chronicle": chron,
            "held": sorted(held), "timeline_entries": len(entries),
            "diff": diff,
        }
    finally:
        clear_week_flag(name)


def run_forecast(name: str, d: Path, n_weeks: int, log) -> dict:
    """Copy-in -> N x tick --live on scratch -> record the projection. Runs
    under the engine lock (the job holds the campaign lock outside). The
    campaign is NEVER written except the forecast bundle; nothing here is
    canon-able, so there is no accept — the DM reads and discards."""
    try:
        base_doc = spine.load_state(name)
        base_week = spine.state_clock_week(base_doc)
        # fail BEFORE any spend (same rule as the week flow: a hand-edited
        # float week would otherwise burn the first tick then re-crash)
        if not isinstance(base_week, int) or isinstance(base_week, bool):
            raise RuntimeError(
                f"meta.clock.week is {base_week!r}, not an integer — fix "
                "simulation/state.yaml before running a forecast")
        copy_state_in(name)
        log("state copied into engine home (previous engine state kept as "
            "state.yaml.bak)")
        chronicle_dir = engine_chronicle_dir()
        prev_doc = base_doc
        weeks_out = []
        for i in range(1, n_weeks + 1):
            before = {p.name for p in chronicle_dir.glob("*.md")} \
                if chronicle_dir.is_dir() else set()
            log(f"projecting week {base_week + i} "
                f"(tick {i}/{n_weeks} on scratch data)")
            rc, lines = run_py(AGENTIC_SIMULATOR,
                               "src/tick.py", "--live",
                               timeout=WEEK_TIMEOUT, on_line=log,
                               env={STUDIO_ENGINE_HOME_HELD: "1"})
            if rc != 0:
                for ln in lines[-10:]:
                    log(ln)
                raise RuntimeError(
                    "forecast week %d failed rc=%s: %s"
                    % (base_week + i, rc, tail_detail(lines, 10)))
            try:
                after_text = engine_state_path().read_text(
                    encoding="utf-8")
            except OSError as e:
                raise RuntimeError(f"the engine wrote no state: {e}")
            after_doc = yaml.safe_load(after_text) or {}
            if not isinstance(after_doc, dict):
                raise RuntimeError(
                    "the engine's state does not parse as a mapping — "
                    "forecast aborted")
            week = spine.state_clock_week(after_doc)
            if not isinstance(week, int) or isinstance(week, bool) \
                    or week != spine.state_clock_week(prev_doc) + 1:
                raise RuntimeError(
                    "the engine did not advance exactly one week "
                    f"({spine.state_clock_week(prev_doc)} -> {week}) — forecast aborted")
            new_files = sorted(
                {p.name for p in chronicle_dir.glob("*.md")} - before) \
                if chronicle_dir.is_dir() else []
            if not new_files:
                raise RuntimeError(
                    f"the engine wrote no chronicle for week {week} — "
                    "forecast aborted")
            n_factions = len(base_doc.get("factions") or [])
            proposal_failed = [ln for ln in lines
                               if "HOLDS (proposal failed" in ln]
            scheduler_held = {m.group(1) for ln in lines
                              if (m := _HOLDS_RE.match(ln))
                              and "proposal failed" not in ln}
            if n_factions and proposal_failed \
                    and (len(proposal_failed) + len(scheduler_held)) \
                    >= n_factions:
                raise RuntimeError(
                    "every faction call failed in projected week "
                    f"{week} — the projection is not a forecast, it is "
                    "an outage (nothing recorded)")
            title = (chronicle_meta(new_files) or {}).get("title")
            markdown = "\n\n".join(
                (chronicle_dir / fn).read_text(encoding="utf-8")
                for fn in new_files)
            weeks_out.append({
                "week": week, "title": title,
                "markdown": markdown,
                "diff": proposals.diff_states(prev_doc, after_doc, []),
            })
            prev_doc = after_doc
        run_record = {
            **run_record_identity(
                f"agentic-simulator/src/tick.py --live x{n_weeks} "
                "(scratch forecast)"),
            # no seed/rule_version (week bundles carry them): a projection
            # is not replayable by design — each tick re-asks the model
        }
        fid = forecasts.create(name, base_week=base_week, weeks=weeks_out,
                               run_record=run_record)
        log(f"forecast staged ({fid}): {n_weeks} projected week(s), base "
            f"week {base_week} — a projection only; the campaign is "
            "unchanged and nothing here can be accepted into canon")
        return {"forecast_id": fid, "status": "active", "base_week": base_week,
                "weeks": weeks_out}
    finally:
        clear_week_flag(name)
