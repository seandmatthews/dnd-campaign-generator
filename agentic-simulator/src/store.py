"""State-file I/O shared by every module that reads or writes world/state.yaml.

One loader, one dumper, one default path — the four per-module copies this
replaces had diverged in signature only. `load_state(path=None)` /
`save_state(state, path=None)`: omit the path for the engine's own
world/state.yaml; ingest passes its --state target explicitly.

save_state writes tmp + os.replace: a crash or kill mid-write used to
truncate world/state.yaml in place — for the standalone workflow that file
is the single source of truth, so the write itself must be atomic.
"""

import os
import pathlib
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "world" / "state.yaml"


def load_state(path=None):
    with open(path or STATE_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_state(state, path=None):
    path = pathlib.Path(path or STATE_FILE)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(state, f, sort_keys=False, allow_unicode=True, width=100)
    # A concurrent reader holding the destination open makes Windows raise
    # PermissionError for an instant; the reader is the innocent party —
    # brief retries instead of losing the week to a sharing violation. (The
    # studio's writers have had this tolerance all along; this is the one
    # writer on the file both sides hammer.)
    for attempt in range(3):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 2:
                tmp.unlink(missing_ok=True)  # no litter, old state intact
                raise
            time.sleep(0.05 * (attempt + 1))
