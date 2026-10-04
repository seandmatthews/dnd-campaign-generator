# 13 — Lock & queue honesty polish

**Status:** completed — commits 9d91917..eff1282 (2026-10-02); code-verified and archived 2026-10-04.
**Priority:** P3
**Area:** `studio/spine.py`, `studio/jobs.py`, `studio/proposal_flows.py`,
`docs/tickets/02-job-queue.md` (notes correction)

## Problem

Found by the 2026-10-02 post-sweep review of ticket 02's landing. No live
bug — five honesty/discipline gaps:

1. **Stale lock docstring.** `spine.campaign_lock`'s docstring
   (`spine.py:215`) still describes the pre-ticket-02 behavior ("Long jobs
   acquire at submit time with `acquire(blocking=False)` … answering 409
   while held") — exactly backwards now: long jobs queue and block in the
   queue's `before_run` (`take_turn`), and the 409s belong to the short
   interactive writers only.
2. **`_acquire_file` opens the lock file twice** (`spine.py:148` then the
   reopen at `:158` inside the loop): the first handle is orphaned
   (refcount-closed on CPython, so not a leak in practice), and the
   *second* open sits outside the OSError-degrade handler — a transient
   Windows sharing violation there escapes as a raw 500 instead of
   degrading to process-local locking like the documented path.
3. **`locked()` can't see external holders.** It probes only the
   in-process threading lock (`spine.py:175`), so a CLI process holding
   the cross-process file lock is invisible: `submit_campaign_job` answers
   `queued: false, position: 0` while the job actually waits behind the
   CLI. Ticket 02's implementation notes overclaim this case ("a CLI
   process → position 1"). Either probe the file lock too (non-blocking
   acquire/release) or document the limitation and correct the notes.
4. **`jobs.cancel()` mutates job-record fields under the queue's lock
   only** (`jobs.py:201–202` sets `status`/`_log_all` under `q.mu`, not
   the global `_LOCK` every other mutation uses). GIL-atomic in CPython —
   a discipline break, not a live race.
5. **Proposal accept/discard 409s lost their context.**
   `proposal_flows._lock` (`proposal_flows.py:390`) maps `CampaignBusy` to
   a bare static 409 — the accept/discard buttons are the one interactive
   writer that doesn't carry ticket 02's "Blocked by: … typically N
   minutes" line.

## Proposal

Rewrite the `campaign_lock` docstring to describe the queue reality; make
`_acquire_file` a single open inside the degrade handler (keeping the
`.gitignore` bootstrap); decide and implement (or document) `locked()`
honesty for external holders and correct ticket 02's notes to match; take
`_LOCK` for the record flips in `cancel()`; reuse `busy_409`'s
blocked-line in the ProposalFlowError 409.

## Acceptance criteria

- [x] spine.py's lock documentation matches the queue reality.
      (`campaign_lock`'s docstring now describes take_turn/queue/409
      split; `locked()`'s documents its file-lock probe and its limits)
- [x] A lock-file OSError anywhere in acquire degrades loudly to
      process-local locking (test with an unwritable/unlockable path); no
      orphan opens. (`test_lock_file_unusable_degrades_to_process_local`;
      `_acquire_file` is ONE open inside the degrade handler, and
      contention retries lock the SAME handle instead of reopening)
- [x] The 202 answer for a job queued behind an external holder is
      honest, or the limitation is documented in code AND ticket 02's
      notes no longer overclaim. (Chose honest: `locked()` probes the
      file lock non-blockingly — `test_locked_and_position_see_an_
      external_process_holder` and `test_submit_position_is_honest_
      behind_external_holder` pin both halves; ticket 02's notes carry a
      correction pointing here)
- [x] All job-record mutations happen under `_LOCK`.
      (`jobs.cancel` flips the record under `_LOCK`;
      `_CampaignQueue.cancel` touches queue state only — taking `_LOCK`
      inside `q.mu` would invert the module's one lock order)
- [x] Proposal accept/discard 409s carry the same what-is-running /
      estimate line as every other busy-409 (test).
      (`test_proposal_flow_409_carries_the_wait_line`;
      `blocked_by_line` moved to job_history so common and
      proposal_flows share ONE implementation)

## Implementation notes (2026-10-02)

- `locked()` now answers for external holders: in-process probe first,
  then a non-blocking acquire/release of the file lock (contended =
  held). A degraded (`_no_file_lock`) CampaignLock honestly answers only
  for its own process — documented in its docstring.
- `_acquire_file` opens the lock file once, inside the OSError-degrade
  handler; contention retries `msvcrt.locking`/`flock` on the SAME
  handle. The old double-open's first handle was refcount-closed on
  CPython (not a leak in practice), but its second open sat outside the
  degrade path — a transient Windows sharing violation there escaped as
  a 500.
- `blocked_by_line` lives in `job_history` (services import services;
  proposal_flows must not import routers), re-exported from
  `routers.common` for the routers that import it from there.
