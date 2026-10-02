# 29 — Session-staging UI: the "Now" dropdown must follow the kind

**Priority:** P2
**Area:** `studio/static/js/sim.js`, `studio/static/js/sessions.js`
**Found by:** 2026-10-02 full review P2-5, P3-36

## Problem

1. **The "Now" dropdown keeps the wrong option set when the effect kind
   changes (P2-5, repro).** `sync` (`sim.js:1141-1161`) rebuilds the After
   control only when it is an INPUT or an *empty* select. Switching
   `set_resource` ⇄ `set_project_progress` (both selects, both non-empty)
   rebuilds nothing: a project-progress row shows none/low/medium/high; a
   resource row shows 0–8. On submit, `Number('high') || 0` (`:1187-1192`)
   coerces to **0** — engine-valid — so the staged effect silently sets a
   project's progress back to 0 (visible only if the DM reads the diff).
   The reverse direction sends a numeric string as a resource level → 422.
   The ticket-11 fix covered only the fresh-row empty-select case.
2. **`saveDraft`'s defensive skip desynchronizes positional ids
   (P3-36, suspected/latent).** A DOM card carrying a data-id absent from
   `draft.suggestions` is silently omitted from the PUT body
   (`sessions.js:379`), shifting every later card's position;
   `prepareSelection` then mints/resolves ids against the wrong cards.
   Unreachable single-tab (cards render from the same draft) but exactly
   the stale multi-tab render the skip was added to defend; the staging is
   visible in review before accept, so contained.

## Proposal

- Rebuild the After control whenever the kind's option SET differs from
  what's rendered (or unconditionally on kind change, clearing the
  selection) — and when the current value isn't valid for the new kind,
  reset to the kind's default with the row visually marked as changed.
- `saveDraft`: when a card's id is foreign to the draft, treat it as an
  error surfaced to the user (toast + row highlight), not a silent skip —
  or send it explicitly so the server's positional minting stays aligned
  and the revision check 409s honestly.

## Acceptance criteria

- [ ] Kind switch updates the dropdown options in BOTH directions; a
      stale value never submits (reset-and-flag or block) — node test on
      the state machine like `test_sessions_prepare.cjs`.
- [ ] A project-progress row can never submit a resource word, and a
      resource row can never submit 0–8 as a level.
- [ ] Foreign-id card: user sees a message; the PUT body and server
      minting stay aligned (test).
