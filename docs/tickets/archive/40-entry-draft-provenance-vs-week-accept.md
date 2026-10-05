# 40 — Entry drafts pin lore/timeline.yaml: the first-DM sequence dead-ends one gate later than ticket 37 fixed

**Status:** completed — both decisions taken + implemented + verified
2026-10-04, all tiers green (see Implementation notes).
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

- [x] The product decision is recorded — DECIDED: (a) for the
      provenance pin, and the ticket's first candidate (batch-scope)
      for the identity guard. Full reasoning in Implementation notes.
- [x] Ticket 36 step 8 approves DIRECTLY — the delete→regenerate
      detour is gone from `studio/tests/e2e/first_dm.spec.ts`; the
      Help-recommended sequence runs end-to-end (both FMG legs green).
- [x] A week whose faction repeats a prior week's action can be
      accepted — pinned by `test_week_accepting_a_repeated_action_succeeds`
      in `studio/tests/test_week_staging.py` (route level: stage week 4,
      accept, stage week 5 with the identical action text, accept
      succeeds, both entries land as distinct t-numbers).

## Implementation notes (2026-10-04)

Landed as one change on `master` (ticket moved to `archive/` in the
same commit). Both decisions:

**1. Provenance pin: option (a) — the timeline is a feed, not a pin.**
`loregen.generate_entries` no longer puts `lore/timeline.yaml` in the
draft's `base_revisions` (it still grounds the prompt). Rationale: the
pin's value is real where generated prose is derived FROM the pinned
thing (page drafts vs hand-edited entity pages — those stay pinned,
as does geography), but backstory entries are "before week 1" history;
an accepted week appending to the timeline does not supersede them.
Option (b) (append-only renumber-and-accept) buys nothing over (a) but
complexity; option (c) teaches the UI to route around a gate that had
no reason to exist. Pinned by `test_staged_draft_pins_geography_but_
not_the_timeline` (test_loregen.py).

**2. Identity guard: scoped to within one publish batch** (the
ticket's first candidate). `timeline.append` refuses only a batch
carrying two identical entries (ambiguous input: which is the real
event?); content already in canon is not refused. Rationale: the
existing-canon check was the crash-between-writes recovery path, but
that window is owned by publication transactions + receipts since the
2026-09-24 handoff work — a crashed approve is rolled forward at boot,
parked as a conflict (which blocks approvals via
`ensure_publishable_409`), or answered from the recorded
transaction/receipt; append() can no longer see that state. What it
COULD still see was legitimate repeats — a faction patrolling the
same road in week 3 as in week 1 — and it dead-ended the whole week
accept with a crash message for a crash that never happened. The
review queue and the proposal panel both show every entry to the DM
before it lands, so a repeat the DM approves is a decision, not an
accident. `TimelineConflict`'s message now says exactly what refused:
the batch carries two identical entries.

Tests: the 37-era `test_approve_still_refuses_exact_reapprove_after_
crash` flipped to `test_approve_lands_exact_canon_repeat` (a repeat
lands, renumbered — the decision, pinned) + new
`test_approve_refuses_in_batch_duplicate`; the week-repeat regression
above; E2E step 8 simplified as noted. All tiers green (fast + the
E2E on both FMG legs + integration).

Not done, deliberately: option (b)'s append-only re-accept; any
dedup UI. Canon with a hand-approved duplicate is DM-visible junk
deletable in Obsidian — honest and recoverable, vs. the dead-ends this
ticket removes.
