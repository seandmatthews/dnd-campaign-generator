# 11 — Sessions: what you checked is what stages

**Priority:** P2
**Area:** `studio/static/js/sessions.js`, `studio/sessions.py`,
`studio/routers/sessions.py`, `studio/API.md`

## Problem

Found by the 2026-10-02 post-sweep review, plus two adjacent honesty gaps
in the same area:

1. **A newly-added suggestion you check is silently excluded from the
   staged proposal.** The Prepare handler collects
   `.ssn-check:checked` `data-id` attributes *before* `saveDraft()`
   (`sessions.js:393`) and filters out empties — but cards added via the
   wave's "Add suggestion" button render `data-id=""` (`sessions.js:56`);
   the server mints ids only during the save. Checking only new cards →
   "Select at least one suggestion first" despite checked boxes. Mixing an
   old card with a new one → the proposal stages without the new card
   while the toast says it staged. The card survives as `open` in the
   draft (no data loss) — which is exactly what makes it silent.
2. **Failed draft bookkeeping is invisible.** `on_accepted`/`on_discarded`
   (`sessions.py:1133, 1152, 1178`) now swallow all exceptions —
   deliberate (advisory bookkeeping, tested), but a failed draft or
   session.yaml write (disk full, permissions) reaches the DM only as a
   log line, unlike the accept path's `status_flip_failed` response flag.
3. **One unreadable session file 4xx's the whole list.** `spine._safe_load`
   now raises after 3 retries (it used to degrade to None), so a
   session.yaml held exclusively by an external editor surfaces as an
   error for the entire sessions list instead of skipping that session.
4. **API.md predates the wave's new routes.** `POST
   .../sessions/{id}/drafts` and `POST .../drafts/{draft_id}/suggestions`
   (`routers/sessions.py:122, 134`) are undocumented, and the
   `PUT .../drafts/{id}` entry doesn't mention that id-less entries are
   minted on save.

## Proposal

1. Collect the selection after the save: `saveDraft()` returns the saved
   suggestions with their minted ids — map checked cards to those ids (key
   by card element, not `data-id`). Extract the mapping as a pure
   function so it can be pinned by a node `.cjs` test the way
   `handoff.js` is.
2. Surface bookkeeping failures: the accept/discard response gains a
   `draft_update: "ok" | "failed"` flag, rendered as a toast note — same
   pattern as `status_flip_failed`.
3. The sessions list skips an unreadable session.yaml with a log/console
   note (per-file degradation — the same posture ticket 09 restores for
   quests).
4. API.md documents both new routes and the id-minting PUT behavior.

## Acceptance criteria

- [x] Checking a newly-added suggestion — alone, and mixed with an
      existing one — stages it; pinned by a node test of the pure mapping
      plus (if the harness allows) the wired handler.
      (`studio/tests/js/test_sessions_prepare.cjs` — run-tests.py
      `studio-js-sessions`; the handler collects checked cards as
      positions before the save and resolves ids from the save's
      response)
- [x] A failed draft write on accept/discard surfaces in the response and
      the UI, not just the log.
      (`test_accept_and_discard_carry_draft_update` — canon still lands,
      `draft_update: "failed"`; sim.js toasts the repair path on both
      accept and discard)
- [x] One unreadable session.yaml no longer fails the list; it is skipped
      with a note. — **verified non-issue**: `list_sessions` reads
      through `spine.read_yaml_dict`, the tolerant reader (OSError/
      YAMLError → None → skip); the review's concern applied to
      `_safe_load` readers, not this one. No change needed.
- [x] API.md covers both new routes and the minting behavior; all
      existing suites green. (`python -m pytest studio/tests -q`
      499 passed; node suites green)

## Implementation notes (2026-10-02)

- The id resolution is positional: `saveDraft` serializes the cards in
  DOM order and the server mints ids for id-less suggestions in the same
  order, so `Studio.prepareSelection(cards, saved.suggestions)` (a pure
  function, node-tested) maps checked cards to minted ids. A checked
  card whose position returns no id is dropped LOUDLY (a visible "the
  save returned no ids" message), never silently.
- `on_accepted`/`on_discarded` now return `"ok" | "failed"` instead of
  None; `proposal_flows` carries the status into the accept result and
  the discard response as `draft_update` (session_changes only — absent
  means not applicable). The idempotent-replay paths return the stored
  receipt, whose flag came from the first run.
- The disabled-checkbox guard: accepted cards render their checkbox
  disabled, and the collector treats `checked && !disabled` as selected —
  so an accepted card can never sneak into a new proposal through the
  position mapping.
