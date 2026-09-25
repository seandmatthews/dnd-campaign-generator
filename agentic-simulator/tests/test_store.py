"""Tests for the shared state-file I/O (store.py).

Zero dependencies — run directly:  python tests/test_store.py
"""

import os
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from store import load_state, save_state


def test_save_load_round_trip():
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "state.yaml"
        doc = {"meta": {"clock": {"week": 4}}, "factions": [{"id": "a"}]}
        save_state(doc, path)
        assert load_state(path) == doc
    print("  store: save/load round trip  OK")


def test_save_state_retries_a_windows_sharing_violation():
    """A concurrent reader holding the destination open makes os.replace
    raise PermissionError for an instant; the writer must retry instead of
    losing the week (the studio's writers have had this tolerance all
    along — this pins it on the one file both sides hammer)."""
    real_replace = os.replace
    calls = []

    def flaky_replace(tmp, dst):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(32, "sharing violation")
        return real_replace(tmp, dst)

    saved = os.replace
    os.replace = flaky_replace
    try:
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "state.yaml"
            save_state({"week": 5}, path)
            assert len(calls) == 2, "one retry recovered the sharing violation"
            assert load_state(path) == {"week": 5}
    finally:
        os.replace = saved
    print("  store: a sharing violation retries instead of failing  OK")


def test_save_state_never_truncates_on_permanent_failure():
    """Three failed replaces raise, but the destination is untouched —
    the tmp file pattern means no half-written state survives a crash."""
    real_replace = os.replace

    def always_busy(tmp, dst):
        raise PermissionError(32, "sharing violation")

    os.replace = always_busy
    try:
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "state.yaml"
            path.write_text("week: 4\n", encoding="utf-8")
            try:
                save_state({"week": 5}, path)
            except PermissionError:
                pass
            else:
                raise AssertionError("a permanently busy file must raise")
            assert path.read_text(encoding="utf-8") == "week: 4\n", \
                "the old state survives intact"
            leftovers = [p.name for p in path.parent.iterdir()
                         if p.name != path.name]
            assert leftovers == [], f"no litter left behind: {leftovers}"
    finally:
        os.replace = real_replace
    print("  store: permanent failure raises, destination intact  OK")


if __name__ == "__main__":
    for fn in [
        test_save_load_round_trip,
        test_save_state_retries_a_windows_sharing_violation,
        test_save_state_never_truncates_on_permanent_failure,
    ]:
        fn()
    print("all store tests passed")
