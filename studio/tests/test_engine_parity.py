"""Seam parity: the studio's place normalization must behave EXACTLY like
the engine's (store.places_of / store._place_id).

The studio drives the engine through its CLI, never by importing across
projects — so the two implementations are separate by design and can drift
silently. Before this test they already had: the studio defaulted kindless
places-list entries to "city" (engine: "place") and derived ids with an
uncapped slug (engine: 80-char cap, "place" fallback) — a long place name
got different ids on the two sides, so a faction.place reference that
resolved in the studio's Places tab was REFUSED by validate_state at spend
time. This test imports the engine's own modules (test-only; the studio's
runtime never does) and asserts the implementations agree on a matrix of
hostile states.
"""

import sys
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent.parent
ENGINE_SRC = STUDIO_DIR.parent / "agentic-simulator" / "src"
sys.path.append(str(ENGINE_SRC))  # appended: studio modules always win

import effects as engine_effects  # noqa: E402
import store as engine_store  # noqa: E402

import sessions  # noqa: E402
import spine  # noqa: E402

# hostile matrix: every entry must normalize IDENTICALLY on both sides
STATES = [
    {},                                     # nothing at all
    {"city": {"name": "Tasospon"}},          # legacy single city
    {"city": {"name": "  Tasospon  ", "id": "explicit", "kind": "port"}},
    {"city": {"name": ""}},                  # nameless city degrades to []
    {"city": "not a mapping"},
    {"places": []},                          # empty list -> city fallback
    {"places": [{"name": "Tasospon"}]},      # kindless -> engine's "place"
    {"places": [{"name": "Tasospon", "id": "tsp", "kind": "city"}]},
    {"places": [{"name": "Tasospon"}, {"name": "Harmouth and the "
                                              "Kingdom of Skyleia"}]},
    {"places": [{"name": "x" * 150}]},       # id capped at 80 on both sides
    {"places": [{"name": "!!!"}]},           # no word chars -> "place"
    {"places": [{"name": "Ünïcodé Tøwn"}]},  # non-ascii stripped by the slug
    {"places": [{"name": "A"}, "junk", {"nope": 1}, {"name": "  "}]},
    {"places": [{"name": "A"}], "city": {"name": "B"}},  # list wins
]

NAMES = ["Tasospon", "x" * 150, "!!!", "  spaced  ", "Ünïcodé",
         "A B-C_d.e/f", "", "123", "MiXeD CaSe"]


def test_place_normalization_is_identical_across_the_seam():
    for i, s in enumerate(STATES):
        ours = spine.state_places(s)
        theirs = engine_store.places_of(s)
        assert ours == theirs, (
            f"state #{i} ({s!r}) normalizes differently: "
            f"studio={ours!r} engine={theirs!r}")
    print(f"  parity: {len(STATES)} states normalize identically  OK")


def test_place_id_derivation_is_identical_across_the_seam():
    for name in NAMES:
        assert spine.place_id(name) == engine_store._place_id(name), (
            f"place_id({name!r}) diverges: "
            f"studio={spine.place_id(name)!r} "
            f"engine={engine_store._place_id(name)!r}")
    print(f"  parity: {len(NAMES)} id derivations identical  OK")


def test_engine_vocabulary_has_not_drifted():
    """The resource ladder and field allowlist are declared in the engine
    and copied into studio modules. If the engine ever adds a grade, key,
    or field, these asserts fail until the studio copies catch up — the
    alternative is a create-form that validates fine and a week that
    refuses at spend time."""
    assert sessions.RESOURCE_KEYS == engine_store.RESOURCE_KEYS, \
        "sessions.RESOURCE_KEYS drifted from the engine's store.py"
    assert sessions.RESOURCE_LEVELS == engine_store.RESOURCE_LEVELS, \
        "sessions.RESOURCE_LEVELS drifted from the engine's store.py"
    assert sessions.FIELD_KEYS == engine_effects.FIELD_KEYS, \
        "sessions.FIELD_KEYS drifted from the engine's effects.py"
    print("  parity: resource/field vocabulary matches the engine  OK")


if __name__ == "__main__":
    test_place_normalization_is_identical_across_the_seam()
    test_place_id_derivation_is_identical_across_the_seam()
    test_engine_vocabulary_has_not_drifted()
    print("all engine-parity tests passed")
