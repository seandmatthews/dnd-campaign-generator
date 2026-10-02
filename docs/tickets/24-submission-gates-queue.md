# 24 — Submission gates must survive the queue

**Priority:** P1/P2 borderline (R1) + P2 (R2)
**Area:** `studio/routers/maps.py` (post_world), `studio/routers/simulation.py`
(post_week, post_forecast)
**Found by:** 2026-10-02 followup R1, R2 (both confirmed by trace in the
review addendum)

## Problem

1. **World-gen force gate never re-checked inside the job (R1).** The
   create-versus-replace gate runs only at submission time
   (`maps.py:486` — `geography.yaml` exists and not `force` → 409). The
   queued job (`maps.py:497-572`) unconditionally copies `source.json`,
   `geography.yaml`, and `map.svg`. While the first world is generating
   (geography not yet on disk), a second POST with a different seed and
   `force: false` passes the same check, joins the FIFO (202), and — once
   the first finishes — **replaces the first world without the required
   force approval**. Queue serialization makes it orderly; it does not make
   the submission-time precondition stay true. (World artifacts are
   seed-regenerable, which is why this is borderline rather than a clean
   P1 — but the gate's whole purpose is bypassed by timing.)
2. **Same-campaign engine 409 (R2).** `post_week` (`simulation.py:479`) and
   `post_forecast` (`:1076`) probe the global `ENGINE_LOCK` before joining
   the campaign queue and 409 whenever ANY week/forecast holds it. The
   comments justify the 409 by *cross*-campaign contention ("this
   contention CANNOT join the queue — the queue is per-campaign; the engine
   home is global") — but the code never distinguishes the holder. A week
   for THIS campaign running means you cannot queue the next (auto-accept)
   week or a forecast behind it, defeating the chaining the queue exists
   for.

## Proposal

- R1: repeat the create-versus-replace gate under the campaign lock at the
  start of the world-gen job (the job already runs under
  `submit_campaign_job`); a create-only job that finds geography on disk
  refuses with the same message the route gives — nothing run, nothing
  spent. (Same pattern as the week job's `find_pending` re-check at
  `simulation.py:492`.)
- R2: teach the probe who holds the engine home (record the holding
  campaign with the lock, e.g. a `holder` attribute set in
  `engine_lock_logged`/the flag). If the holder is this campaign, skip the
  409 and let the job queue — the in-job re-checks (pending proposal,
  active forecast) already guard correctness. Cross-campaign keeps today's
  honest 409 + estimate.

**Ruling (GM, 2026-10-02):** same-campaign requests JOIN the queue — the
user wants to queue as many weeks back-to-back as they like (week 1, then
2, then 3…), with auto-accept as the "it all goes into canon" gate (the
existing explicit opt-in per submission; without it the queue still stops
at each proposal for review, by design). No queue-depth cap. Forecasts
queue the same way. Cross-campaign 409 unchanged.

## Acceptance criteria

- [x] Blocked first world-gen + second create-only POST: second job refuses
      inside the queue (visible in its log/job status), first world intact
      — test with faked FMG, real queue threads.
- [x] A forced second job still replaces (force semantics preserved).
- [x] Same-campaign: week running → second week (auto_accept) queues and
      runs after it; forecast queues behind a week — test with the real
      lock held by a faked week.
- [x] Cross-campaign 409 behavior + estimate line unchanged (existing
      tests keep passing).
