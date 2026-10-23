"""The shared engine home's cross-process lock (ticket 26): standalone
tick.py --live / projects.py --apply / effects.py --apply /
ingest.py --apply refuse while another process holds
world/.engine-home.lock (a studio week/forecast job holds it for its
whole run), and save_state's tmp names are per-writer unique so two
writers can never land a half-written state. Ticket 56 closed the set
(projects.py --apply joined it) and widened effects.py's claim from the
save alone to the whole read-modify-write.

Zero dependencies — run directly:  python tests/test_home_lock.py
"""

import contextlib
import io
import os
import pathlib
import shutil
import sys
import tempfile
import threading

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import effects
import ingest
import projects
import store
import tick
from store import HomeLockBusy, standalone_home_lock


def _relocate_home_lock(tmp):
    """Point store's home lock at a tmp path (tick/effects/ingest bound
    their own references at import — patch store's globals, which the
    context manager reads at call time)."""
    lock_file = pathlib.Path(tmp) / ".engine-home.lock"
    store.HOME_LOCK_FILE = lock_file
    return lock_file


def _write_home_state(path):
    path.write_text(
        "meta:\n  clock:\n    week: 4\n  city:\n    name: T\n"
        "factions: []\nevents: []\n", encoding="utf-8")


def test_standalone_lock_refuses_a_second_holder():
    tmp = tempfile.mkdtemp()
    lock_file = _relocate_home_lock(tmp)
    try:
        with standalone_home_lock():
            assert store.home_lock_held(), "the lock file is held"
            try:
                with standalone_home_lock():
                    raise AssertionError("a second claim must not succeed")
            except HomeLockBusy as e:
                assert "engine home is busy" in str(e), str(e)
                assert str(lock_file) in str(e), \
                    "the refusal names the lock file"
        assert not store.home_lock_held(), "released with the context"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("  home lock: a second standalone claim refuses readably  OK")


def test_studio_child_env_skips_the_claim():
    """STUDIO_ENGINE_HOME_HELD (set by the studio on the engine processes
    it launches while holding the lock itself) exempts the child: the
    studio's own week job must not deadlock on its parent's claim."""
    tmp = tempfile.mkdtemp()
    _relocate_home_lock(tmp)
    old = os.environ.get(store.STUDIO_ENGINE_HOME_HELD)
    try:
        with standalone_home_lock():  # the "studio parent" holds it
            os.environ[store.STUDIO_ENGINE_HOME_HELD] = "1"
            with standalone_home_lock():  # the child: no refusal, no deadlock
                pass
    finally:
        os.environ.pop(store.STUDIO_ENGINE_HOME_HELD, None)
        if old is not None:
            os.environ[store.STUDIO_ENGINE_HOME_HELD] = old
        shutil.rmtree(tmp, ignore_errors=True)
    print("  home lock: a studio child skips the parent's claim  OK")


def test_live_main_refuses_while_the_home_is_held():
    """AC: a standalone `tick.py --live` run while a studio week holds the
    home answers exit 4 + a readable refusal — BEFORE reading state.yaml,
    so the message is the contention, not the holder's half-swapped
    scratch state (which may not even exist)."""
    tmp = tempfile.mkdtemp()
    lock_file = _relocate_home_lock(tmp)
    err = io.StringIO()
    try:
        with standalone_home_lock():
            with contextlib.redirect_stderr(err):
                rc = tick.main(["--live"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert rc == 4, f"refusal must exit 4, got {rc}"
    assert "refused:" in err.getvalue(), err.getvalue()
    assert "engine home is busy" in err.getvalue(), err.getvalue()
    assert str(lock_file) in err.getvalue(), err.getvalue()
    print("  home lock: standalone --live refuses readably  OK")


def test_effects_apply_refuses_on_the_home_state_only():
    """effects.py --apply against the SHARED home state refuses while the
    home is held; --check (writes nothing) and an explicit --state target
    (the caller's own file — how the studio's session prep invokes it)
    never claim the home lock. The home state is relocated to tmp via the
    effects module's STATE_FILE (what the gate compares against)."""
    tmp = tempfile.mkdtemp()
    _relocate_home_lock(tmp)
    home_state = pathlib.Path(tmp) / "home-state.yaml"
    own_state = pathlib.Path(tmp) / "own-state.yaml"
    record_path = pathlib.Path(tmp) / "session.yaml"
    saved_state_file = effects.STATE_FILE
    try:
        _write_home_state(home_state)
        _write_home_state(own_state)
        record_path.write_text(
            "source: session:t\nweek: 4\n"
            "events:\n  - {week: 4, actor: world, text: a quiet night}\n",
            encoding="utf-8")
        effects.STATE_FILE = home_state  # the "shared home" for this test
        with standalone_home_lock():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = effects.main(["--apply", str(record_path),
                                   "--state", str(home_state)])
            assert rc == 4, f"home-state apply must refuse, got {rc}"
            assert "refused:" in err.getvalue(), err.getvalue()

            # an explicit non-home target: no claim, the checks/applies run
            rc = effects.main(["--check", str(record_path),
                               "--state", str(own_state)])
            assert rc == 0, "--check on an own target never claims the home"
            rc = effects.main(["--apply", str(record_path),
                               "--state", str(own_state)])
            assert rc == 0, "an own --state target applies without claiming"
    finally:
        effects.STATE_FILE = saved_state_file
        shutil.rmtree(tmp, ignore_errors=True)
    print("  home lock: effects --apply claims only the shared home  OK")


def test_projects_apply_refuses_on_the_home_state():
    """AC (ticket 56): projects.py --apply is the documented DM
    canon-mutation path, and until now it saved the shared state.yaml
    with no home-lock claim at all — run it while a week tick held the
    home and the week's save overwrites the applied canon (or the
    reverse). While a fake holder owns the lock file, --apply refuses
    readably BEFORE reading state.yaml and nothing is written; the batch
    is one the apply would otherwise land, so the refusal is the lock's,
    not the content's."""
    tmp = tempfile.mkdtemp()
    _relocate_home_lock(tmp)
    home_state = pathlib.Path(tmp) / "home-state.yaml"
    changes_path = pathlib.Path(tmp) / "changes.yaml"
    saved_state_file = projects.STATE_FILE
    home_state.write_text(
        "meta:\n  clock:\n    week: 4\n  city:\n    name: T\n"
        "factions:\n  - {id: mint, name: The Mint, goal: coin, "
        "resources: {coin: low, muscle: low, legitimacy: low}}\n"
        "events: []\n", encoding="utf-8")
    changes_path.write_text(
        "operations:\n  - {id: op-1, op: create, reason: probe, "
        "project: {faction_id: mint, title: Ferry concession, "
        "objective: Buy the ferry concession}}\n", encoding="utf-8")
    before = home_state.read_text(encoding="utf-8")
    try:
        projects.STATE_FILE = home_state
        with standalone_home_lock():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = projects.main(["--apply", str(changes_path),
                                    "--state", str(home_state)])
        assert rc == 4, f"home-state apply must refuse, got {rc}"
        assert "refused:" in err.getvalue(), err.getvalue()
        assert "engine home is busy" in err.getvalue(), err.getvalue()
        assert home_state.read_text(encoding="utf-8") == before, \
            "a refusal writes nothing"
        # an explicit non-home target never claims the home: the apply runs
        own_state = pathlib.Path(tmp) / "own-state.yaml"
        own_state.write_text(before, encoding="utf-8")
        rc = projects.main(["--apply", str(changes_path),
                            "--state", str(own_state)])
        assert rc == 0, "an own --state target applies without claiming"
    finally:
        projects.STATE_FILE = saved_state_file
        shutil.rmtree(tmp, ignore_errors=True)
    print("  home lock: projects --apply claims the shared home  OK")


def test_effects_apply_locks_the_whole_read_modify_write():
    """AC (ticket 56): effects.py --apply used to take the home lock only
    around the save — the load and apply ran unlocked, so two fast
    applies interleaved last-writer-wins over state.yaml. The claim now
    covers the whole read-modify-write (mirroring ingest): while another
    process holds the home, --apply refuses BEFORE reading state.yaml.
    The home state is deliberately ABSENT — the old shape's unlocked load
    would answer an IO error (exit 1); the locked shape answers the
    contention (exit 4)."""
    tmp = tempfile.mkdtemp()
    _relocate_home_lock(tmp)
    home_state = pathlib.Path(tmp) / "home-state.yaml"
    record_path = pathlib.Path(tmp) / "session.yaml"
    saved_state_file = effects.STATE_FILE
    record_path.write_text(
        "source: session:t\nweek: 4\n"
        "events:\n  - {week: 4, actor: world, text: a quiet night}\n",
        encoding="utf-8")
    try:
        effects.STATE_FILE = home_state  # absent on purpose (see above)
        with standalone_home_lock():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = effects.main(["--apply", str(record_path)])
        assert rc == 4, f"whole-RMW apply must refuse, got {rc}"
        assert "refused:" in err.getvalue(), err.getvalue()
        assert "engine home is busy" in err.getvalue(), err.getvalue()
        assert not home_state.exists(), "a refusal writes nothing"
    finally:
        effects.STATE_FILE = saved_state_file
        shutil.rmtree(tmp, ignore_errors=True)
    print("  home lock: effects --apply locks the whole read-modify-write  OK")


def test_ingest_apply_refuses_on_the_home_state():
    tmp = tempfile.mkdtemp()
    _relocate_home_lock(tmp)
    home_state = pathlib.Path(tmp) / "home-state.yaml"
    pending_path = pathlib.Path(tmp) / "pending.yml"
    saved_state_file = ingest.STATE_FILE
    try:
        _write_home_state(home_state)
        pending_path.write_text(
            "week: 4\nproposals:\n  - {week: 4, actor: world, "
            "text: a quiet night}\n", encoding="utf-8")
        ingest.STATE_FILE = home_state
        with standalone_home_lock():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = ingest.main(["--apply", str(pending_path),
                                  "--state", str(home_state)])
        assert rc == 4, f"home-state ingest must refuse, got {rc}"
        assert "refused:" in err.getvalue(), err.getvalue()
    finally:
        ingest.STATE_FILE = saved_state_file
        shutil.rmtree(tmp, ignore_errors=True)
    print("  home lock: ingest --apply refuses on the shared home  OK")


def test_save_state_tmp_names_are_per_writer_unique():
    """The fixed `state.yaml.tmp` let two writers share one tmp — one
    os.replace could consume the other's half-written file. Each call now
    mints its own, and no litter survives either exit."""
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "state.yaml"
        real_replace = os.replace
        seen = []

        def cap_replace(tmp, dst):
            seen.append(pathlib.Path(tmp).name)
            return real_replace(tmp, dst)

        saved = os.replace
        os.replace = cap_replace
        try:
            store.save_state({"week": 4}, path)
            store.save_state({"week": 5}, path)
        finally:
            os.replace = saved
        assert seen[0] != seen[1], f"tmp names must differ: {seen}"
        assert all(n.startswith("state.yaml.") and n.endswith(".tmp")
                   for n in seen), seen
        assert not [p for p in path.parent.iterdir()
                    if p.name != path.name], "no tmp litter"
    print("  save_state: per-writer unique tmp names, no litter  OK")


def test_concurrent_save_state_writers_all_land_whole():
    """Two threads saving the same destination: with unique tmps each
    os.replace is atomic over its OWN file — the destination always holds
    one writer's whole document (the old shared tmp made that a coin
    flip between whole, stale, or a lost FileNotFound)."""
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "state.yaml"
        docs = [{"week": 4, "who": "a", "pad": "x" * 4000},
                {"week": 4, "who": "b", "pad": "y" * 4000}]
        errors = []

        def writer(doc):
            for _ in range(25):
                try:
                    store.save_state(doc, path)
                except Exception as e:  # noqa: BLE001 — recorded, asserted
                    errors.append(e)

        threads = [threading.Thread(target=writer, args=(doc,))
                   for doc in docs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        final = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert final["who"] in ("a", "b") and final["week"] == 4
        assert not [p for p in path.parent.iterdir()
                    if p.name != path.name], "no tmp litter"
    print("  save_state: concurrent writers land whole documents  OK")


if __name__ == "__main__":
    for fn in [
        test_standalone_lock_refuses_a_second_holder,
        test_studio_child_env_skips_the_claim,
        test_live_main_refuses_while_the_home_is_held,
        test_projects_apply_refuses_on_the_home_state,
        test_effects_apply_refuses_on_the_home_state_only,
        test_effects_apply_locks_the_whole_read_modify_write,
        test_ingest_apply_refuses_on_the_home_state,
        test_save_state_tmp_names_are_per_writer_unique,
        test_concurrent_save_state_writers_all_land_whole,
    ]:
        fn()
    print("all home-lock tests passed")
