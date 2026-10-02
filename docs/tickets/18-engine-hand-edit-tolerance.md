# 18 — Engine hand-edit tolerance: ids, aliases, rules, and read paths

**Priority:** P2 (one finding burns the full LLM spend on every retry)
**Area:** `agentic-simulator/src/store.py`, `scheduler.py`, `llm.py`,
`resolve.py`, `ingest.py`, `consistency.py`, `views.py`, `why.py`
**Found by:** 2026-10-02 full review P2-1, P3-2…P3-7

## Problem

state.yaml is hand-edited by design, and `validate_state` is the gate — but
seven shape gaps let a plausible hand edit crash the week (one of them after
the full provider spend) or silently misbehave:

1. **Numeric faction id (P2-1, repro).** An unquoted `id: 123` passes
   `validate_state` (it stringifies for the check) and `effects.py`, then
   `scheduler.activation` dies at `words.add(faction["id"])` +
   `len(w)` (`scheduler.py:28-30` — `TypeError: 'int' has no len()`). Worse,
   fixing only that exposes the real seam: `llm._validate` canonicalizes
   targets to `str(fid)` while `resolve._known_targets` collects raw ids, so
   resolution raises `ValueError: unknown target '123'` — after the week's
   entire LLM spend, re-burned on every retry.
2. **Scalar `aliases` value (P3-2, repro).** `aliases: {mint: ferrand}` →
   `" ".join("ferrand")` → single-letter tokens (`ingest.py:61`); the alias
   never matches and session bullets misattribute to `world`.
3. **Scalar `generic_words` (P3-3, repro).** `generic_words: 'council
   harbor'` → `set("council harbor")` = characters, replacing (not
   supplementing) the default filter (`consistency.py:108`).
4. **`meta.rules.default_dc` unvalidated (P3-4, trace).** `default_dc:
   twelve` passes validation, survives all proposals, then `int('twelve')`
   crashes at `resolve.py:88-91` — the fail-fast gap class validate_state's
   docstring claims is closed.
5. **Duplicate faction ids (P3-5, trace).** Places ids are checked for
   duplicates, factions aren't (`store.py:182-184`); `by_actor` keeps the
   last (`tick.py:161`) and two factions' consequences silently merge.
6. **`disposition: null` on a rival (P3-6, repro).** `views.py:132-134`
   `f.get("disposition", {}).get(...)` → `AttributeError` (the neighboring
   `resources` read uses `or {}`); the views CLI and studio
   `/simulation/views` route 500.
7. **Faction without `name` (P3-7, repro).** `why.py:48-50` direct-indexes
   `faction["name"]` → `KeyError` traceback through the CLI and the
   `/simulation/why` route.

## Proposal

Normalize at the boundary, don't sprinkle isinstance checks downstream:

- Decide the canonical faction-id type (str) and enforce it in
  `validate_state` with a readable refusal — or normalize on load — so
  scheduler, llm, and resolve all see the same thing (fix the
  str-vs-raw-id seam between `llm._validate` and `resolve._known_targets`
  explicitly, with a test that runs a numeric-id state end-to-end through
  proposal→resolve with a faked provider).
- validate_state: check `aliases` values and `generic_words` are
  list-shaped (or tolerate scalars by wrapping — pick one, document it);
  check `meta.rules.default_dc` is int/numeric-string; check faction ids
  unique (mirroring the places check).
- views/why: degrade with diagnostics instead of tracebacks (`or {}`
  pattern; a missing-name faction shows its id).

## Acceptance criteria

- [x] Numeric-id state: `--live` refuses pre-spend with a readable
      validate_state message (or runs correctly if normalization is chosen)
      — no `TypeError` in scheduler, no post-spend `ValueError` in resolve.
- [x] Scalar alias / scalar generic_words: either refused by validate_state
      with a named error or coerced to one-item list — test both shapes.
- [x] `default_dc: twelve` is a pre-spend refusal naming the key.
- [x] Duplicate faction ids refused like duplicate place ids.
- [x] views/why on `disposition: null` and nameless factions return
      diagnostics, never tracebacks (unit tests per case).
