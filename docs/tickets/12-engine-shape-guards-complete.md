# 12 — Engine element-shape guards: finish the job

**Priority:** P3
**Area:** `agentic-simulator/src/store.py`, `agentic-simulator/src/rumors.py`,
engine tests

## Problem

Found by the 2026-10-02 post-sweep review. The arch-fix wave's
element-shape validation fell short of its own contract ("refuse a partial
edit naming the missing piece") in two spots:

1. `store.py:258` — the transmissions guard calls `t.get(k)` **before** its
   own `isinstance(t, dict)` check, so `transmissions: ["week 3"]` crashes
   `validate_state` with an AttributeError (raw traceback, exit 1) instead
   of the ValueError whose message even contains an unreachable `'its
   mapping shape'` branch. The rumor-level loop just above gets the order
   right — this one was missed.
2. `store.py:229–252` validates `id/claim/fidelity/heard_by/transmissions/
   born_week` but not `quiet` — a hand-written `quiet:` (null) makes
   `rumor.get("quiet", [])` return `None`, and `f not in None` raises
   TypeError mid-`run_live` (`rumors.py:72`). The wave fixed this exact
   null-optional pattern for `disposition:`/`knows:` but stopped one field
   short. `heard_by` element types are also unvalidated —
   `sorted(rumor["heard_by"])` on mixed types raises.

Both are pre-spend (nothing is lost), but they surface as tracebacks, not
diagnostics — which is what the wave existed to fix.

## Proposal

Reorder the transmissions guard (isinstance first, one pass); validate
`quiet` (absent, null, or list) and `heard_by` elements (strings) in
`validate_state` with the same message style as the neighboring checks;
add tests for each hostile shape.

## Acceptance criteria

- [x] `transmissions: ["week 3"]` → ValueError naming the rumor,
      transmission, and shape problem — no traceback.
      (`test_non_mapping_transmission_is_refused_not_crashed`,
      `test_missing_transmission_fields_name_the_fields`)
- [x] `quiet:` null / non-list, and non-string `heard_by` elements →
      clean ValueError at load, before any spend.
      (`test_explicit_null_quiet_is_refused_at_validation` — note the
      guard checks key PRESENCE, because `quiet:` with a null value is
      exactly the shape `.get("quiet", [])` returns None for;
      `test_non_string_heard_by_entries_are_refused`)
- [x] Engine suite and the studio parity tests green. (223 passed;
      parity 3 passed)

## Implementation notes (2026-10-02)

- The transmissions guard now checks `isinstance(t, dict)` FIRST (the
  tmiss comprehension runs only on mappings), and the unreachable
  `'its mapping shape'` ternary is gone — each failure mode has its own
  message.
- `quiet` uses `"quiet" in r`, not `r.get("quiet") is not None`: an
  explicit null is the crash shape (the `.get` default only applies when
  the key is absent), so absence is the only thing that skips the check.
- `heard_by` entries must be non-empty strings — `rumors.spread` sorts
  the list, and mixed types raise TypeError mid-week.
