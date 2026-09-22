"""Subprocess wrappers for the three engines (D3, D4).

The parent path contains `&` (Documents/D&D), so every invocation is
list-form args with cwd=<engine root> — never shell=True. Both wrappers
capture stdout+stderr line by line (stderr is merged into stdout so engine
tracebacks land in the job log) and stream each line to an optional
on_line callback, which is what keeps a live week's log tail moving.

Engine roots resolve relative to the studio parent — studio is a fourth
sibling of dnd-mapping / agentic-simulator / verification (D1).
"""

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent
PARENT = STUDIO_DIR.parent

DND_MAPPING = PARENT / "dnd-mapping"
AGENTIC_SIMULATOR = PARENT / "agentic-simulator"
VERIFICATION = PARENT / "verification"


def run_py(engine_root: Path, script: str, *args, timeout: int = 600,
           on_line=None, env: dict = None):
    """[sys.executable, -X utf8, script, *args] with cwd=engine_root.
    Returns (rc, lines)."""
    cmd = [sys.executable, "-X", "utf8", script, *args]
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


def _run(cmd: list, cwd: Path, timeout: int, on_line, env: dict = None) -> tuple:
    if env:
        # merge over the parent env (PATH etc. must survive); never mutate it
        env = {**os.environ, **{k: str(v) for k, v in env.items()}}
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"engine not found for {' '.join(cmd[:2])}: {e}")

    lines: list = []
    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        proc.kill()

    watchdog = threading.Timer(timeout, _kill)
    watchdog.start()
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            if on_line is not None:
                on_line(line)
        rc = proc.wait()
    finally:
        watchdog.cancel()
    if timed_out.is_set():
        lines.append(f"[studio] engine killed after {timeout}s timeout")
        rc = rc if rc else 124
    return rc, lines


def copy_file(src: Path, dst: Path) -> Path:
    """Copy-in/copy-out primitive (D4): place one artifact, mkdir as needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst
