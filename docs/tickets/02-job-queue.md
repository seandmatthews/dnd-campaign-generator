# 02 — Queue campaign jobs; auto-accept what can queue; estimate what can't

**Priority:** P1
**Area:** `studio/routers/common.py` (`submit_campaign_job`), job UI
(`static/js/`), all mutating endpoints

## Problem

Every campaign mutation takes the per-campaign lock at submit time and 409s
while another job runs. Correct for safety — but a GM's natural evening is
"map three sites, run a week, record a fact," which today is four waits in
sequence with manual babysitting between each. In the 2026-10-01 session,
place-link writes 409'd for the entire duration of the week run.

Two different pains:

1. Jobs that *could* run after the current one force the GM to wait and
   re-submit by hand.
2. Jobs that genuinely can't start yet give no signal at all — no position,
   no ETA, nothing to do but poll.

## Proposal

### 1. A real per-campaign FIFO queue

`submit_campaign_job` stops fast-failing and **enqueues instead**: submit
returns 202 immediately with a queue position; a worker thread per campaign
drains the queue. The per-campaign lock stays exactly as it is — the queue
just means writers wait their turn instead of being refused.

- Queued jobs are cancellable (position shown; cancel = dequeue).
- Queue state survives in the existing jobs poll (`GET /api/jobs/{id}`
  gains `status: "queued"`, `position: N`).
- The confirm gates do not move: `confirm: true` is still given **at submit
  time**, which is what makes queueing safe — a queued week already has the
  DM's explicit spend approval; nothing in the queue needs a decision the
  DM is not present for.

### 2. Auto-accept, for chains that would otherwise dead-end on a decision

The queue is only "set the evening up and walk away" if staged output does
not pile up behind the pending-slot. Add an opt-in **auto-accept** to the
jobs that stage reviewable bundles:

- `lore-entries`, `lore-missing-pages`, `goal-review`: auto-accept runs the
  same approve path the Review tab uses (same publication transactions,
  receipts, snapshots) with the job itself as the approver, recorded in the
  transaction as `auto: true`. Verification findings still attach; findings
  at `error` level **block** auto-accept and leave the bundle pending, as
  today.
- `sim-week`: opt-in auto-accept with a loud checkbox ("publish the week
  without reading it first"). The engine already refuses invalid
  candidates; git keeps the undo; the receipt records the auto-accept.
  Default is off — reading the chronicle is the point of the feature.
- `session_effects` / `project_changes`: never auto-accepted (they encode
  DM rulings, not generation).

### 3. When something really can't queue: an honest wait estimate

Not everything can join the queue (a second campaign's week contending for
the shared engine home; a job submitted from the CLI). For those, the 409
response and the UI banner say **what is running and how long it takes**:

- Keep a small rolling history of finished jobs per type — median of the
  last few durations, scaled by roster size for weeks/forecasts — in a
  git-ignored file next to the transactions dir.
- The 409 body and the campaign banner render it in the only unit that
  matters at a table: *"Blocked by: week 2 run (5-faction roster) —
  typically 8–15 minutes. Queue up a 10-minute video."*
- Estimates are labeled as estimates; the job log remains the source of
  truth.

## Acceptance criteria

- [x] Submitting a second mutation while one runs returns 202 + queue
      position, not 409.
- [x] The queue is visible in the UI (positions, cancel buttons) and
      drains in order.
- [x] Auto-accept exists for the three staging job types, error-level
      findings block it, and every auto-approval is identifiable in the
      publication record.
- [x] Week auto-accept exists only behind an explicit checkbox, default off.
- [x] A 409 (or queue-blocked state) that cannot be enqueued carries a
      duration estimate from the rolling history, phrased as a wait a human
      can plan around.
- [x] All existing tests green; new tests for queue order, cancel,
      auto-accept-with-findings-blocked, and the ETA store.

## Implementation notes (2026-10-02)

- The queue lives in `studio/jobs.py` (`_CampaignQueue`, one FIFO +
  drainer thread per campaign); `routers/common.submit_campaign_job`
  hands it a `before_run` hook that takes the campaign lock while the
  record is still `queued` — so `GET /api/jobs/{id}` shows
  `status: "queued", position: N` for exactly the jobs that have not had
  their turn, and cancellation works during the wait. The lock discipline
  itself is unchanged (held for the job's whole run).
- Not queueable by design: a second campaign's week contending for the
  shared engine home (the `ENGINE_LOCK` 409s keep their fast-fail), and
  the short interactive writers (`busy_409`) — both now carry the
  "Blocked by: ... typically ..." line from `studio/job_history.py`
  (`.studio-jobs/history.json`, git-ignored; roster-scaled for
  weeks/forecasts, clamped; labeled an estimate).
- Auto-accept reuses the exact approval machinery: `review.approve_items`
  (extracted from the route, lock-agnostic) for the two lore jobs,
  `proposal_flows.accept_locked` (same) for goal-review and the week.
  `publication.publish(..., auto=True)` writes `auto: true` into the
  transaction manifest, the result/receipt carries `auto: true`, and the
  snapshot message gains "(auto-accepted)". The goal-review gate reads the
  bundle's findings; engine drop-notes are plain strings and never block —
  dict findings at `error` level do.
- UI: the campaign strip under the header (running + queued + estimate,
  5 s poll), a per-form queued banner with position and a Cancel button,
  auto-accept checkboxes on the two lore forms, goal review, and the loud
  week checkbox ("publish the week without reading it first"). The repo
  has no DOM test harness, so the rendering itself is covered by
  `node --check` and the API-level tests above, not an automated browser
  test.
