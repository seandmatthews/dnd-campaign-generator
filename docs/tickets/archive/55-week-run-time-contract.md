# 55 — One honest week-run time contract (watchdog, poll, docs)

**Status:** completed (implemented 2026-10-23 by the ticket-sweep lane,
ca182cf, reviewed round 2; the sweep's merge was auto-reverted by its
flaking gate and re-landed as 6362c88 with the fast tier green — the
live duration re-measure rides on the campaign's next real week run)
**Priority:** P1 — the core loop can be killed mid-spend while every
doc claims it fits
**Area:** `studio/engines.py`, `studio/routers/simulation.py`,
`studio/static/js/{core,sim}.js`, `docs/for-new-gms/TUTORIAL.md`
**Found by:** nine-lane adversarial review, 2026-10-23 (LLM-dependency
and product lanes found it independently; the frontend lane the poll
half)

## Problem

Three numbers describe one week run and none agree:

1. The studio kills it at 30 minutes: `WEEK_TIMEOUT = 1800`
   (`studio/engines.py:406`), passed as the subprocess timeout for the
   live tick at `:661` and forecasts at `:791`.
2. The code's own worst-case math says ONE LLM call can legally take
   ~2700s: `studio/routers/simulation.py:923-927` computes "3 attempts
   × 300s × 3 passes (base + two length extensions) plus backoff ≈
   2700s" and sizes the goal-review watchdog at 2800s — while a whole
   multi-faction week (N proposals × retries × length doublings +
   narration) runs under the 1800s cap. `complete()` supports the
   math: 300s/POST, 3 HTTP attempts, token budget self-extending by
   doubling to 24,576 completion tokens
   (`agentic-simulator/src/llm.py:214-260`).
3. The docs promise 10–20 minutes: `docs/for-new-gms/TUTORIAL.md:182`
   ("expect 10–20 minutes with a full roster") and the UI's duration
   table (`studio/static/js/core.js:1059`, `weekRun: '10–20
   minutes'`). Observed reality on this codebase is 60–90 minutes.

The queue logic budgets for a world the watchdog forbids:
`engine_lock_deadline_s()` (`engines.py:430-434`) allows **93
minutes** of waiting for the shared engine home — for a run the
watchdog kills at 30. Meanwhile the frontend poll gives up at 31
minutes **counting queue time** (`core.js:196`, the `pollJob`
default; `sim.js`'s `runWeek` passes no `timeoutMs`), then tells the
GM "the job keeps running server-side — reopen this tab in a few
minutes" (`core.js:213-217`) — wrong by up to an hour when the job
is merely queued behind another run.

Game-night failure mode: GM proposes the week at 19:00; at 19:30 the
watchdog kills the run (rc=124), nothing is staged, a full week of
API spend is discarded; the retry re-spends and re-kills. There is
no knob anywhere for the cap.

## Proposal

Make the three numbers one honestly derived number:

1. Size the week/forecast watchdog above the same worst-case formula
   the goal-review path already uses (shared helper or a documented
   reason why a whole week gets less than one call's worst case);
   make the cap env-configurable (`STUDIO_WEEK_TIMEOUT_S`) with a
   derived default, not a hardcoded one.
2. Make the week-run poll scale with the run kind the way forecast's
   already does (n×31+5): `runWeek` passes an explicit `timeoutMs`
   sized to watchdog + queue margin, and the give-up copy
   distinguishes queued from running (the job snapshot knows which).
3. Re-measure and fix the 10–20-minute claims in TUTORIAL.md and the
   `core.js` duration table — they claim to mirror each other, so
   keep them one source.

## Acceptance criteria

- [x] Watchdog ≥ derived worst case (goal-review formula) — pinned by
      a studio test asserting the relationship (new
      `studio/tests/test_run_timeouts.py`, beside the goal-review
      sizing test) — WEEK_TIMEOUT is now DERIVED (engine POST ceiling ×
      per-POST timeout + margin = 7800 s ≥ the goal-review formula's
      2800 s, itself derived via the shared `llm_call_watchdog_s()`),
      pinned by `test_week_watchdog_covers_one_llm_call_worst_case`,
      `test_goal_review_watchdog_is_the_shared_formula` and the
      derivation/env-knob tests in the same file
- [x] `runWeek` polls with a run-kind-sized timeout, not the default
      — pinned by a js harness asserting the poll call shape —
      `timeoutMs: S.weekPollTimeoutMs(sim.run_contract, 1)` (queue
      dead-man cap + watchdog + slack, served by
      `GET /simulation` `run_contract`); pinned by
      `studio/tests/js/test_run_timeouts.cjs` (sizing math, clearing
      the 31-min default, and the source call shape); the forecast's
      poll re-sources the same helper
- [x] Give-up copy distinguishes queued from running — js harness on
      the `pollJob` give-up branch — `pollJob` tracks the last-seen
      job status; queued says "nothing of yours has started or been
      spent", running names the server watchdog; both branches driven
      by the harness against the real `S.pollJob`
- [x] TUTORIAL.md and the duration table carry re-measured numbers —
      directly checkable against a logged real run's duration — both
      carry 60–90 min/week (the review's 2026-10-23 logged
      observation; ticket 01's 10–20 was the 2026-10-01 measurement),
      the mirror between them pinned by `test_durations.cjs`, and a
      real campaign's own durations land in
      `.studio-jobs/history.json` (the queue strip's "typically" band)
      to check against
