# 28 — Publication & review edge cases

**Priority:** P3
**Area:** `studio/routers/review.py`, `studio/proposal_flows.py`,
`studio/publication.py`
**Found by:** 2026-10-02 full review P3-27 (case collision), P3-28 (snapshot
field)

## Problem

1. **Same-batch approvals can silently collapse two case-colliding pages
   (P3-27).** `approve_items`' batch guard is case-sensitive string
   equality (`routers/review.py:517`, `:536` — `any(w["path"] == rel)`),
   and `exists()` is checked before any batch write lands. On Windows
   (case-insensitive filenames), approving two drafts titled "Ashen Order"
   and "ashen order" in one request writes both, the second `_apply_text`
   replacing the first — response counts `pages: 2`, one page silently
   lost, publish verification passes because both paths resolve to the
   same file.
2. **Idempotent accept replay loses the `snapshot` field once the
   transaction is pruned (P3-28).** `proposal_flows.py:197` records the
   receipt before `result["snapshot"] = snap` is added (`:221-222`);
   `receipt_with_snapshot` backfills from the transaction manifest, but a
   committed transaction is pruned by `after_snapshot`, so a retry of a
   fully-completed accept returns a response without the `snapshot` key
   the live path had. Data integrity is unaffected (it's the replay
   answer's shape), but clients keying on it (the UI surfaces snapshot
   state) see an inconsistent answer.

## Proposal

- Case-fold the batch guard (compare `Path(rel).name.casefold()` on
  Windows — or reject same-batch entries whose paths collide
  case-insensitively with a named error, which is the honest answer for
  two drafts that would become one file).
- Record the snapshot into the receipt (or re-derive it from canon history
  when the transaction is gone) so the idempotent replay's shape matches
  the live accept.

## Acceptance criteria

- [ ] One approval containing "Ashen Order" + "ashen order": refused with
      a named conflict (or provably written as two distinct files) — never
      one file with `pages: 2`.
- [ ] Retry of a completed accept (transaction pruned) returns the same
      response shape as the original, `snapshot` included (test drives the
      prune then the retry).
