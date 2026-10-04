# 15 — Simdesign hardening (read races, caps, injection clamp, dead code)

**Status:** completed — commits 9d91917..eff1282 (2026-10-02); code-verified and archived 2026-10-04.
**Priority:** P3
**Area:** `studio/simdesign.py`, `studio/tests/test_simdesign.py`

## Problem

Found by the 2026-10-02 post-sweep review of ticket 03. The happy path is
solid (real validators, real engine check, bounded re-ask, secrets
isolated — all test-pinned); the edges are not:

1. **Geography read has no retry and no OSError handling**
   (`simdesign.py:368`): the route catches
   FedOrRefused/DesignError/BudgetExceeded/RuntimeError, so a design
   clicked while the Maps pipeline swaps `world/geography.yaml` 500s. The
   codebase's own `spine._safe_load` documents this exact Windows
   sharing-violation race and retries three times.
2. **The route is synchronous with no watchdog.** On a slow provider the
   full length-extension ladder (complete()'s documented worst case
   ~2700 s) can pin a threadpool thread and the tab for the better part
   of an hour — doubled by the re-ask. Nothing is written and no lock is
   held, so it is a UX/robustness risk, not a correctness one.
3. **`places` grounding is uncapped** (`simdesign.py:356` joins verbatim
   while geography is capped at 6000 chars): a pasted 500-place list
   inflates every attempt's prompt, and the meter counts calls, not
   tokens.
4. **The re-ask splices model-controlled text into the second prompt**
   (`simdesign.py:401`): validator messages embed model-authored faction
   names verbatim. Output re-validates through the identical validators,
   so impact is low — but it is a real self-injection surface.
5. **Dead code:** `_faction_index` is never called; `_match_faction`'s
   id-arm compares hyphen slugs (`spine.slugify`) against underscore ids
   (`validate_faction_docs` mints `slugify(name).replace("-", "_")`) and
   can never fire — drift bait for whoever copies it next.
6. **Test gaps:** the unparseable-JSON arm (→ re-ask → 502) and the
   >2000-char request-premise 422 are untested.

## Proposal

Read geography through the retrying helper (or catch OSError → 422
"geography unreadable — retry"); cap the places list (≤12 names, ≤120
chars each) with a 422 naming the cap on overflow; clamp the re-ask's
quoted validator text to a bounded summary (or strip model-authored
strings from it); delete the dead code and the dead id-arm; add the two
missing tests. The sync-route/watchdog question is a recorded decision,
not a smuggled change: either accept and document it in the route
docstring, or run the pass as a job — say which and why in the ticket
close-out.

## Acceptance criteria

- [x] A design submitted during a geography.yaml swap retries or refuses
      cleanly (422), never 500.
      (`test_unreadable_geography_refuses_cleanly` — three reads with
      backoff, then FedOrRefused → 422 "could not be read"; the retry
      count is asserted)
- [x] Oversized places input is refused with a 422 naming the cap.
      (`test_places_list_is_capped` — >12 names, or a name >120 chars,
      refused before any LLM call: `calls == []`)
- [x] The re-ask prompt quotes no unbounded model-controlled text.
      (`_REASK_QUOTE_MAX = 400` clamp on the quoted validator words;
      `test_reask_quotes_are_clamped` drives a 2000-char model-authored
      faction name through a validator message into the re-ask)
- [x] `_faction_index` and the dead id-arm are gone; the form path is
      unchanged (existing tests green). (`_match_faction` is name-slug
      only, with a comment recording WHY the id arm could never fire;
      studio tier 511 passed)
- [x] New tests: unparseable JSON → re-ask → 502 with the validator's
      words; long-premise 422; the sync/wait decision recorded.
      (`test_unparseable_json_reask_then_502`,
      `test_overlong_request_premise_is_refused`; the decision is
      ACCEPTED-AND-DOCUMENTED in the route's docstring — see below)

## Implementation notes (2026-10-02)

- The sync/wait decision: ACCEPTED as-is, documented in
  `post_simulation_design`'s docstring. The pass holds no lock and
  writes nothing, so its worst case is one pinned threadpool thread and
  a closed tab; converting it to a queue job would move the design into
  a job record for a cancellable wait nobody has needed. Revisit when a
  real provider wedges someone for the full worst case.
- The geography read retries 3× with growing backoff (the
  `spine._safe_load` pattern for Windows sharing violations); a
  persistently unreadable world is FedOrRefused (422), matching the
  fed-or-refused family instead of a 500.
- Places caps live beside `_REQUEST_PREMISE_MAX` as input gates: the
  meter counts calls, not tokens, so a pasted gazetteer had no brake.
