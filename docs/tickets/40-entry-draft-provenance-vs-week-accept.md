# 40 — Entry drafts pin lore/timeline.yaml: the first-DM sequence dead-ends one gate later than ticket 37 fixed

**Status:** proposed
**Priority:** P2 (same sequence, same dead-end class as ticket 37 — the
E2E suite found the next gate behind it)
**Area:** `studio/loregen.py` (`_input_provenance`), `studio/routers/review.py`
(`approve_items` provenance check)
**Found by:** ticket 36's Playwright suite, first fully-green run
2026-10-04 (step 8 of `studio/tests/e2e/first_dm.spec.ts`)

## Problem

Ticket 37 removed the t-id collision from
generate-backstory → propose week 1 → accept → approve-entries. The E2E
shows the sequence still dead-ends, one gate later: the generated draft
pins the byte revision of `lore/timeline.yaml` (and the
`lore/entities` fingerprint) at GENERATION time, so a week accepted in
between makes every approval of that draft answer

    409 stale review: lore/timeline.yaml changed on disk (expected
    absent, found 58588d0c7da1) — this draft was generated from canon
    that has since changed, and submitting it again cannot succeed while
    that is true: delete the draft (Delete / Delete all) and generate a
    fresh one from the current pages.

The advice is honest and the remedy works (the E2E exercises exactly
that: delete → regenerate → approve), but for the timeline the remedy
re-pays an LLM run for prose that was perfectly fine — the timeline
moving is precisely what ticket 37 taught us to expect on this
sequence. Same DM-visible outcome as 37 pre-fix: paid-for entries stuck
behind a routine, Help-recommended order.

Design question (do not resolve here): should entry drafts pin the
timeline at all? The pin's value is real for PAGE drafts (generated
prose grounded in entity pages that a hand edit can invalidate), but a
timeline that grew by accepted weeks does not make backstory prose
wrong — the entries are "before week 1" history regardless. Options:
(a) drop `lore/timeline.yaml` from the draft's base_revisions and keep
the id-renumbering (37) as the only timeline gate; (b) keep the pin but
make approve renumber-and-accept when the only timeline change is
APPEND (ids shifted, content superset); (c) leave as-is and teach the
UI the sequence (auto-suggest regenerate).

## Also observed (same run, same guard, different writer)

The week accept's duplicate-identity guard refuses a whole week when a
faction repeats an action across weeks: week 3's entry "patrol the
crossing road" is an exact title+text repeat of week 1's, and the
accept answers 409 with the crash-recovery message ("if a previous
approve crashed midway, delete it from the pending file…"). No crash
occurred — the faction legitimately patrolled twice. Repeated actions
are NORMAL week behavior (a patrol project runs weekly), so this will
hit real campaigns. Candidate fix: scope the identity guard to WITHIN
one publish batch (its actual crash-recovery purpose: the append landed,
the pending rewrite did not), or key it on the entry id as well as
title+text. The E2E stub now writes unique per-pass action text to stay
clear of it, which is why the suite is green today.

## Acceptance criteria

- [ ] The product decision is recorded (a/b/c above for the provenance
      pin; the batch-scope fix for the identity guard) — this ticket is
      the decision record as much as the fix
- [ ] Whichever way the decision goes, ticket 36 step 8 stops needing
      its delete→regenerate detour (or the detour is asserted as the
      CONTRACT with a comment naming this ticket)
- [ ] A week whose faction repeats a prior week's action can be
      accepted — pinned by a route-level regression in
      `studio/tests/test_week_staging.py` (stage week 1 with action X,
      accept, stage week 3 with the same action X, accept succeeds)

## Implementation notes (YYYY-MM-DD)

Not yet implemented. Found by the E2E, worked around in the E2E
(unique stub action text; the delete→regenerate detour is what the
suite asserts until this ticket lands).
