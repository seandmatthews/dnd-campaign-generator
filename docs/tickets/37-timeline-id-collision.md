# 37 — Approving pending lore entries after a week accept dead-ends on t-id collision

**Status:** proposed
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

- [ ] Approving pending entries whose stage-time ids collide with canon
      succeeds; the entries land with next-free ids — pinned by
      `studio/tests/test_review.py` (or `test_loregen.py`) regression:
      stage entries against empty timeline, append colliding t0001–t000N
      to the timeline, approve, assert success + renumbering
- [ ] The crash-recovery duplicate guard still refuses an exact
      title+text re-approve — pinned by the existing
      `TimelineConflict` identity test (unchanged)
- [ ] Rebuilding the sequence from scratch (stage entries → accept a
      week → approve) succeeds — pinned by ticket 36 step 8
      (`studio/tests/e2e/first_dm.spec.ts`) once it lands; until then a
      manual pass over the same sequence is the check

## Implementation notes (YYYY-MM-DD)

Not yet implemented.
