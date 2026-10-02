"""Tests for the shared state-file I/O (store.py).

Zero dependencies — run directly:  python tests/test_store.py
"""

import os
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from store import load_state, save_state, validate_state


def test_save_load_round_trip():
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "state.yaml"
        doc = {"meta": {"clock": {"week": 4}}, "factions": [{"id": "a"}]}
        save_state(doc, path)
        assert load_state(path) == doc
    print("  store: save/load round trip  OK")


def _faction(**over):
    f = {"id": "a", "name": "The Assembly", "goal": "hold the crossing",
         "resources": {"coin": "low"}}
    f.update(over)
    return f


def test_validate_state_accepts_a_spendable_state():
    doc = {"meta": {"clock": {"week": 4, "season": "spring"}},
           "city": {"name": "Testburg"}, "factions": [_faction()],
           "events": [], "rumors": []}
    validate_state(doc)  # must not raise
    print("  validate: a spendable state passes  OK")


def test_validate_state_names_the_first_missing_piece():
    """Each of these used to crash AFTER the faction calls' spend — or,
    for goal/resources, AFTER the canon write (write_chronicle's state
    summary direct-indexes them). Element shape, not container existence:
    a null optional (disposition:, knows:) defeats every .get() default
    downstream, and the rumor mill direct-indexes event actor and rumor
    id/claim/fidelity."""
    base = {"meta": {"clock": {"week": 4}}, "city": {"name": "T"},
            "factions": [_faction()], "events": []}
    no = lambda k, **over: {**base, "factions": [  # noqa: E731
        _faction(**{k: None, **over})]}
    cases = [
        (None, "not a mapping"),
        ({**base, "city": {}}, "city.name"),
        ({**base, "meta": {"clock": {"week": 4.0}}}, "not an integer"),
        ({**base, "meta": {}}, "not an integer"),
        ({**base, "factions": []}, "factions"),
        ({**base, "factions": [{"goal": "no id"}]}, "id"),
        (no("name"), "non-empty name"),
        (no("goal"), "non-empty goal"),
        (no("resources"), "resources mapping"),
        (no("resources", resources={"coin": "loads"}), "not one of"),
        (no("disposition"), "disposition must be a mapping"),
        (no("knows"), "knows must be a list"),
        ({**base, "events": None}, "events"),
        ({**base, "events": [{"week": 4, "text": "no actor"}]},
         "non-empty actor"),
        ({**base, "rumors": ["not a mapping"]}, "not a mapping"),
        ({**base, "rumors": [{"claim": "x", "fidelity": "hearsay"}]},
         "missing id"),
        ({**base, "rumors": [{"id": "rumor_0001", "claim": "x",
                              "fidelity": "gospel", "heard_by": [],
                              "transmissions": [], "born_week": 3}]},
         "fidelity"),
    ]
    for doc, expected in cases:
        try:
            validate_state(doc)
        except ValueError as e:
            assert expected in str(e), f"{expected!r} not named in: {e}"
        else:
            raise AssertionError(f"validate_state accepted {doc!r}")
    print("  validate: each gap is refused with the piece named  OK")


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
        test_validate_state_accepts_a_spendable_state,
        test_validate_state_names_the_first_missing_piece,
        test_save_state_retries_a_windows_sharing_violation,
        test_save_state_never_truncates_on_permanent_failure,
    ]:
        fn()
    print("all store tests passed")
