# 19 — effects.py batch semantics: strip asymmetry, weeks, duplicate ids

**Status:** completed — commit e5fdbf5 (2026-10-02); code-verified and archived 2026-10-04.
**Priority:** P2
**Area:** `agentic-simulator/src/effects.py` (validator/applier),
`src/ingest.py` (feeder)
**Found by:** 2026-10-02 full review P2-2, P2-3, P3-8

## Problem

Three validation/normalization gaps in the session-record batch path:

1. **Validator strips what the applier writes raw (P2-2, repro).**
   `validate_effect` checks the *stripped* value (`after = str(...).strip()`
   at `effects.py:180-186`, `:193-199`, `:131-136`), but `_apply_one` writes
   the unstripped originals (`:225`, `:252-255`). `field: " goal "` +
   `after: " new goal "` validates clean, then writes
   `faction[" goal "] = " new goal "` — the real goal unchanged, the audit
   event claims the change, `applied_effects` records the id (re-approval
   can never fix it). Same for `resources[" coin "]`. The resulting state
   then fails `store.validate_state` / `projects.find_state_issues` → the
   next `--live` exits 2 until hand surgery. Trigger: stray whitespace in a
   quoted YAML scalar (hand-written record or LLM-extracted suggestion —
   `sessions.py:843-850` passes values through unnormalized).
2. **Record/history-event weeks unvalidated (P2-3, repro).** The record's
   `week` is taken verbatim (`effects.py:338`), history events store
   `ev.get("week")` or it (`:396-399`), `_effect_week` silently skips
   non-int candidates (`:270-278`); `ingest.py:131-138` feeds
   `pending.get("week")` which defaults to `None`. A record without a week
   stores `week: null` events; a string week mis-dates provenance. Then
   `store.validate_state` (`store.py:224-226`) refuses the next `--live`
   outright — canon needs hand-editing before the campaign can simulate
   again.
3. **Duplicate effect ids within one batch silently drop the second
   (P3-8, repro).** `effects.py:298-304` treats an intra-batch id collision
   as dedup: two effects sharing an id → `applied=1`, no refusal — a change
   the DM believes they approved never lands and nothing names it. This
   contradicts the module contract ("one invalid effect refuses the whole
   batch — nothing lands half-reviewed"). Cross-record dedup must keep
   working.

## Proposal

- Normalize in the validator (strip `field`/`resource`/`after`), write the
  normalized values in the applier — or refuse unnormalized input with a
  finding. Pick one; the validator and applier must see identical strings.
- Validate the record-level and history-event weeks (int ≥ 0 or None with a
  defined fallback — the studio path already coerces via
  `sessions.create`; mirror that at the ingest/manual path).
- Split dedup scopes: ids seen in a *previous* record dedup as today; ids
  duplicated *within* one batch are a refusal naming both effects.

## Acceptance criteria

- [x] Padded-value repro: `field: " goal "` / `after: " high "` either
      refuses with a finding or applies to `goal`/`coin` normalized — never
      writes `faction[" goal "]`; the state still passes validate_state
      after apply.
- [x] Record with no `week` and with `week: "8"`: defined behavior (error
      or coerced), never `week: null` in stored history events; resulting
      state passes validate_state.
- [x] Two same-id effects in one batch → whole batch refused, both named;
      same id across two records still dedups (existing tests keep
      passing).
