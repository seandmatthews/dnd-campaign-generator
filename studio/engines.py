"""Subprocess wrappers for the three engines (D3, D4).

The parent path contains `&` (Documents/D&D), so every invocation is
list-form args with cwd=<engine root> — never shell=True. Both wrappers
capture stdout+stderr line by line (stderr is merged into stdout so engine
tracebacks land in the job log) and stream each line to an optional
on_line callback, which is what keeps a live week's log tail moving.

Engine roots resolve relative to the studio parent — studio is a fourth
sibling of dnd-mapping / agentic-simulator / verification (D1).
"""

import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent
PARENT = STUDIO_DIR.parent

DND_MAPPING = PARENT / "dnd-mapping"
AGENTIC_SIMULATOR = PARENT / "agentic-simulator"
VERIFICATION = PARENT / "verification"

# The simulator engine home (agentic-simulator/world/state.yaml) is ONE
# scratch file shared by every campaign (D4): copy-in before an engine run,
# copy-out after. Every copy-in + engine-run + copy-out sequence therefore
# holds this process-wide lock — the week job for its whole run, the
# views/why routes around their copy-in + subprocess. Per-campaign locks
# (spine.campaign_lock) cannot protect it: two campaigns share the home.
# Ordering: campaign lock (outer, held by week jobs) then engine lock —
# never the reverse, or a views call could deadlock a running week.
ENGINE_LOCK = threading.Lock()


def run_py(engine_root: Path, script: str, *args, timeout: int = 600,
           on_line=None, env: dict = None):
    # -u: tick.py's per-faction progress lines are the week UI's log tail —
    # block-buffered through a pipe they arrive only when the process exits
    cmd = [sys.executable, "-X", "utf8", "-u", script, *args]
    return _run(cmd, engine_root, timeout, on_line, env)


def run_node(engine_root: Path, *args, timeout: int = 600, on_line=None,
             env: dict = None):
    """[node, *args] with cwd=engine_root. Returns (rc, lines)."""
    return _run(["node", *args], engine_root, timeout, on_line, env)


def fmg_dir() -> Path:
    """The FMG clone inside dnd-mapping — read at call time so tests can
    relocate engines.DND_MAPPING."""
    return DND_MAPPING / "tools" / "Fantasy-Map-Generator"


WEEK_FLAG_NAME = ".week-run-flag"


def week_flag_path() -> Path:
    """Marker for a week run in flight (routers/simulation.py writes it
    before copy-in and removes it after copy-out; server.py restores the
    engine home on boot when it is left behind). Read at call time so tests
    can relocate engines.AGENTIC_SIMULATOR."""
    return AGENTIC_SIMULATOR / "world" / WEEK_FLAG_NAME


# The rest of the engine home's layout is owned HERE the same way: routers
# and the doctor must rebuild none of it. All read at call time so tests can
# relocate AGENTIC_SIMULATOR.

def engine_state_path() -> Path:
    """The shared engine home's scratch state.yaml (campaigns own canon)."""
    return AGENTIC_SIMULATOR / "world" / "state.yaml"


def engine_state_backup_path() -> Path:
    """The pre-copy-in engine state, kept so a boot after a crashed week can
    restore the home (restore_interrupted_week)."""
    return AGENTIC_SIMULATOR / "world" / "state.yaml.bak"


def engine_replay_path(week) -> Path:
    """The resolver's replay trail for one week (scratch; rides with the
    proposal bundle)."""
    return AGENTIC_SIMULATOR / "world" / "replay" / f"week-{week}.yaml"


def engine_chronicle_dir() -> Path:
    return AGENTIC_SIMULATOR / "chronicle"


# The per-run transcript cap: week staging re-parses the whole run output
# for HOLDS lines (a week prints dozens), error paths read the tail — but a
# chatty node worldgen used to pin its ENTIRE transcript in RAM for the
# run's duration, doubled by the job log. Past the cap, lines are dropped
# from the head and a marker says so.
RUN_LINE_CAP = 4000


def _run(cmd: list, cwd: Path, timeout: int, on_line, env: dict = None) -> tuple:
    if env:
        # merge over the parent env (PATH etc. must survive); never mutate it
        env = {**os.environ, **{k: str(v) for k, v in env.items()}}
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
