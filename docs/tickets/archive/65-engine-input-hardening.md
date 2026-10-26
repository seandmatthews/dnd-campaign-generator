# 65 — Engine input hardening: hand-edited states crash or no-op, and small hygiene debts

**Status:** completed (implemented by the 61-70 ticket sweep 2026-10-25/26, 4d7bc15, reviewed round 1; merged as c414a3a with green main gates; the sweep's final fast tier AND --all passed on integrated main)
**Priority:** P3 — loud crashes and one silent no-op on hand-edited
states, plus four small hygiene debts
**Area:** `agentic-simulator/src/{effects,tick,ingest,views,llm,store}.py`,
`studio/engines.py`
**Found by:** nine-lane adversarial review 2026-10-23 (engine lane,
probe-confirmed crashes)

## Problem

`validate_state` gates only `tick --live` and `goals.py`; the other
CLIs serve hand-edited states with inconsistent tolerance:

1. `effects.py:427` — `factions: [null]` crashes
   `apply_session_record` with a raw TypeError, while a scalar
   `applied_effects` makes the dedup set become its CHARACTERS and
   silently skips effects ("applied 0 effect(s)", exit 0) — loud on
   one shape, silent on another.
2. `tick.py:632→641`, `ingest.py:99` — tolerate a missing `meta` one
   line, then hard-index `state["meta"]["clock"]["week"]` → KeyError.
3. `_HOLDS_RE` (`studio/engines.py:408`) parses `[a-z_]+` faction ids
   but `spine.slugify` keeps digits — a faction named "Sect 9" mints
   `sect_9` and drops out of the held-reporting.
4. A model-supplied `action_id` rides validation (`llm.py:394-474`
   never whitelists keys) and can mis-join the project event ledger
   (`projects.py:616-623` stamps project attribution onto any
   same-week event carrying the id).
5. Hard-killed runs (the watchdog's own `taskkill /F`) orphan
   `state.yaml.XXXX.tmp` litter in engine-home forever — the cleanup
   is in a `finally` that never runs on SIGKILL (`store.py:489-509`).
6. `engines.py:496` copies the campaign state into the home with
   plain `copy2` (non-atomic) while every other state write is
   atomic.

## Proposal

One shared state-shape gate for the session/inspect paths (refuse
loudly, naming the shape); digit-tolerant `_HOLDS_RE` (or restrict
`slugify` — ticket-time call); overwrite/whitelist `action_id` in
`_validate`; a boot-time temp-litter sweep in engine-home (matching
`copy_state_in`'s chronicle sweep); atomic copy-in.

## Acceptance criteria

- [x] `factions: [null]` and scalar `applied_effects` → loud named
      refusal, never a silent no-op — engine tests
- [x] Missing `meta.clock` → named refusal on the read paths — engine
      test
- [x] Digit-bearing faction ids parse in HOLDS lines — studio test
- [x] A model-supplied `action_id` never reaches the ledger — engine
      test on `_validate`
- [x] A SIGKILL'd run's temp files are swept at next boot — engine
      test
