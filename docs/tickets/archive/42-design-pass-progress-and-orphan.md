# 42 — Design pass: no progress surface, and a reload orphan's the paid result

**Status:** completed (2026-10-05)
**Priority:** P3
**Area:** `studio/static/js/sim.js` (design block), `studio/routers/simulation.py::post_simulation_design`, `studio/simdesign.py`
**Found by:** live GM pass with the real provider (GLM coding endpoint), 2026-10-04

## Problem

`sand design` is a synchronous POST by recorded decision (router docstring,
ticket 15: "the pass takes no lock, writes nothing, and holds one
threadpool thread… deferred until a real provider actually wedges
someone"). With the real provider a design run is not seconds, it is
minutes: observed 2 LLM calls at ~2 min each (the first reply hit the
6144-token cap and failed validation, so the bounded re-ask ran) — about
4 minutes of a disabled button, with no log tail, no queue strip (by
design it is not a job), and no progress surface of any kind.

Worse, the result is fragile against navigation: the GM reloads (or the
browser auto-refreshes) mid-run, the fetch dies with the document, and
the server-side handler — which keeps running and completes — hands its
response to a closed socket. Observed: the run completed server-side
(both LLM calls metered, `run meter ~2 calls / ~17345 tokens`), the
access-log line never printed for the dead connection, and the page came
back with an empty form. The credits were spent; the design was computed
and thrown away. The sessionStorage design-result restore
(2026-10-03 fix) only helps when a result actually reached the page.

The e2e cannot see any of this: the stub provider answers in ~1 s, so
the suite's 30 s button-enabled wait never exercises minutes-long waits,
re-asks, or mid-run reloads.

## Proposal

Move the design pass onto the existing per-campaign job queue (kind
`sim-design`): the queue strip names it, the wait is cancellable, the
result lands in a job record that survives reloads, and the design block
renders from the job result on next load. The router docstring's deferral
condition has now been met by observation: a real provider makes the pass
a minutes-long wait and a reload destroys a paid result. Keep the
form-shape of the result (`{design, notes, usage}`) and the Create-form
flow unchanged; the design remains a prefill, never staged state.

Open question for the implementer to respect (not re-litigate): ticket
15's point that a design must never take the campaign lock — a queued
job must stay a no-lock run, or the queue's lock discipline needs a
no-lock lane.

## Acceptance criteria

- [x] A design run appears in the queue strip while running and can be
      cancelled while queued — pinned by `studio/tests/e2e/first_dm.spec.ts`
      (extend the design step)
- [x] A page reload mid-design does not lose the result: after reload the
      design block restores the finished/running design from the job
      record — pinned by `studio/tests/e2e/first_dm.spec.ts` with a stub
      provider that delays its reply
- [x] The run meters against the same per-run ceiling (2 calls) and a
      validation failure still surfaces the validator's words — pinned by
      `studio/tests/test_simdesign*` (existing tests move with the lane)

## Implementation notes (2026-10-05)

Landed as one change on `master` (ticket moved to `archive/` in the
same commit).

- **The no-lock lane:** `submit_campaign_job(..., take_lock=False)` —
  the job queues and drains in the campaign's FIFO like any other
  (positions visible, cancellable while queued) but takes the campaign
  lock at no point; ticket 15's recorded decision stands. The queue
  serializes the lane, so a second design waits its turn instead of
  running twice at once.
- **Cheap refusals stay synchronous:** `simdesign.grounding()` (extracted
  from `design_simulation`, shared by both) runs at submit — fed-or-
  refused, premise/places caps answer 422 before any queue slot is
  spent. The pass re-runs grounding inside the job against fresh ground
  truth (a race since submit re-refuses as a job error).
- **The result is the record:** the job returns
  `{design, notes, usage, places}` — `places` added so the reload
  restore can refill the city/other-places fields. DesignError,
  BudgetExceeded, and provider RuntimeErrors surface as job errors
  carrying the validator's/provider's words (the 502/503 shapes moved
  into the record; the metering ceiling is the service's, untouched).
- **The panel reads the snapshot, not sessionStorage:** on render it
  fetches the campaign-jobs snapshot (ticket 41's `last_finished` +
  running/queued) — a queued/running pass shows a status line
  ("Reloading is safe now"), a finished one restores the prefill via the
  pure `S.designJobState` slice + `S.designRestoreNote` window. The
  2026-10-03 client-side machinery (inflight marker, result cache,
  `designInflightWarning`) is REMOVED — the job record is the durable
  copy; `designElapsed`/`DESIGN_TIMEOUT_MS` stay as the job-watch
  ceiling, and the pass streams its log through a new log-tail box while
  an elapsed ticker runs.
- **`last_finished` orders by finish, not submit:** a duplicate design
  cancelled while queued was submitted after the real pass but finished
  before it — `started`-order buried the real design behind the
  cancelled record. Jobs now carry an internal microsecond
  `_finished_at`; pinned by
  `test_last_finished_orders_by_finish_not_submit`.
- **E2E:** the design step now slows the stub's design reply via a
  runtime harness control (`POST /__delay` on `llm_stub.py` — only this
  step pays the wait), then drives the queue strip ("Running:
  sim-design"), the disabled-button double-submit probe, a mid-run
  reload (inflight line), cancel-while-queued through the queue banner
  (2 posts, 1 cancelled, the running pass unharmed), and the record
  restore after the real pass lands. The old single-post count became
  `toBe(2)` — the cancelled duplicate DOES post; that is the lane
  working.
- **Playwright semantics note:** `expect(locator).not.toContainText(...)`
  FAILS with "element(s) not found" when the element rightly stopped
  existing — the strip-quietly-vanishes case. The spec waits
  `toHaveCount(0)` instead. (This one cost a debug run: the app was
  correct the whole time; the assertion could not express success.)
