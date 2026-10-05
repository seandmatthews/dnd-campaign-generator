# 42 — Design pass: no progress surface, and a reload orphan's the paid result

**Status:** proposed
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

- [ ] A design run appears in the queue strip while running and can be
      cancelled while queued — pinned by `studio/tests/e2e/first_dm.spec.ts`
      (extend the design step)
- [ ] A page reload mid-design does not lose the result: after reload the
      design block restores the finished/running design from the job
      record — pinned by `studio/tests/e2e/first_dm.spec.ts` with a stub
      provider that delays its reply
- [ ] The run meters against the same per-run ceiling (2 calls) and a
      validation failure still surfaces the validator's words — pinned by
      `studio/tests/test_simdesign*` (existing tests move with the lane)
