# 37 — Approving pending lore entries after a week accept dead-ends on t-id collision

**Status:** completed — implemented + verified 2026-10-04, fast + integration
tiers green (see Implementation notes).
**Priority:** P2 (blocking, visible, no self-service remedy; routine sequence, not hostile input)
**Area:** `studio/loregen.py`, `studio/timeline.py`, `studio/routers/review.py`
**Found by:** computer-use first-DM pass, 2026-10-04 (repro campaign `reliability-probe`, since deleted at user request — the on-disk evidence is captured above: pending-entries-1.yaml t0001–t0006 vs timeline.yaml t0001–t0005)

## Problem

Pending backstory entries are allocated **real** timeline ids at stage
time, but the id space is consumed later by an independent writer —
so approval can always be dead-ended by any week accepted in between.

Reproduced live, in the order the app's own Help recommends
(Generate backstory → … → Propose week 1 → accept → approve entries):

1. `POST …/lore/entries` stages `lore/pending-entries-1.yaml` with ids
   t0001–t0006 — `loregen.py:464` (`_next_entry_id(timeline_text) + i`,
   "pending entries continue the sequence") allocated them when the
   timeline was empty.
2. Accepting week 1 publishes the engine's 5 timeline entries — the
   canon `lore/timeline.yaml` now holds t0001–t0005.
3. **Approve all (6)** → refused per entry:
   `Review action failed: id t0001 already exists in lore/timeline.yaml`
   (toast + inline). `timeline.py:78` raises `TimelineConflict` on an
   existing t-number before anything else is considered.

Every pending entry collides; the DM's only outs are deleting the
entries and re-paying the LLM run, or hand-editing YAML. Nothing is
corrupted (the guard held — this is loud, not silent), but the paid-for
entries are stuck and the error names an internal id, not a remedy.

Note the same file already contains the correct mechanism:
`timeline.py` `append()` assigns "the next free t-number" to entries
that arrive **without** a usable id. The stage-time ids are advisory
and the approve-time check treats them as binding — that mismatch is
the bug.

## Proposal

Approve-time renumbering: when a chosen pending entry's own id is
already taken in `lore/timeline.yaml`, assign it the next free t-number
(the exact path provisional entries already take) instead of refusing.
The existing duplicate guard stays: the title+text identity check
(`TimelineConflict` "already in lore/timeline.yaml — if a previous
approve crashed midway…") is the thing that protects the
crash-between-writes recovery path, and it is untouched. Stage-time
allocation can stay (it is cosmetic in the pending file) or switch to
`pending-N` placeholders — either way approve no longer trusts it.

## Acceptance criteria

- [x] Approving pending entries whose stage-time ids collide with canon
      succeeds; the entries land with next-free ids — pinned by
      `studio/tests/test_review.py`
      `test_approve_keeps_t_ids_and_renumbers_collisions` +
      `test_approve_renumbers_stage_ids_after_week_accept` (stage entries
      against empty timeline, append colliding t0001–t000N to the
      timeline, approve, assert success + renumbering)
- [x] The crash-recovery duplicate guard still refuses an exact
      title+text re-approve — pinned by
      `test_approve_still_refuses_exact_reapprove_after_crash` (the
      identity guard itself is unchanged; there was no pre-existing
      identity test to leave alone — this one is new)
- [ ] Rebuilding the sequence from scratch (stage entries → accept a
      week → approve) succeeds — to be pinned by ticket 36 step 8
      (`studio/tests/e2e/first_dm.spec.ts`); the route-level repro test
      above covers the same sequence mechanically in the meantime

## Implementation notes (2026-10-04)

Landed as one change on `master` (ticket file moved to `archive/` in the
same commit).

- `timeline.py` `append()`: the `TimelineConflict` on a colliding t-id is
  gone — an entry whose own t-number is free keeps it (unchanged), a
  missing OR colliding id takes the next free t-number through the same
  identity-check + seen-set path provisional entries already used.
  Batch-internal duplicate ids still cannot double-land (the first keeps
  or renumbers, the second renumbers). `TimelineConflict`'s docstring now
  names identity as its only trigger.
- The week-accept path (`proposal_flows.py` → `timeline.append`) is
  behaviorally unaffected: `build_week_entries` only ever mints
  `pw{week}-N` ids, which always took the provisional path.
- Stage-time allocation in `loregen.py` stays exactly as it was (option A
  of the proposal); its comment now says the ids are advisory.
- Contract comments updated with the behavior: `routers/review.py`
  module docstring ("ids preserved when their t-number is free …"),
  `timeline.py` docstrings.
- Tests: the old `test_approve_keeps_t_ids_and_refuses_duplicates` had
  pinned the BUG (a t0002 collision 409 asserted as correct) — flipped to
  `test_approve_keeps_t_ids_and_renumbers_collisions`; added the ticket
  repro (`…_after_week_accept`) and the crash-guard regression
  (`…_refuses_exact_reapprove_after_crash`). 17/17 in
  `test_review.py`; whole-repo fast tier green; integration tier green.
- Lint: `ruff check` (local 0.16.9 flags many pre-existing patterns repo
  wide — 271 in `studio` at HEAD) shows the SAME 14 findings on the four
  touched files at HEAD and after the change: nothing new introduced.
- No deviations from the proposal.
