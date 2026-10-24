# 63 — Frontend failure honesty: expensive runs fail invisibly, refused cancels look successful

**Status:** proposed
**Priority:** P2 — the costliest failures in the app are the least
visible ones
**Area:** `studio/static/js/{core,sim,sessions,knowledge,maps}.js`
**Found by:** nine-lane adversarial review 2026-10-23 (frontend lane)

## Problem

A cluster of affordances that fail without a user-visible signal:

1. **Failed week/forecast/goal-review/extract runs leave no durable
   trace on their tabs** — `renderSectionError` covers only maps/world
   kinds; the sim and sessions tabs read nothing from
   `last_finished`. A paid 30-min run that fails while the GM is in
   another window leaves a normal-looking tab with a live
   "Propose week" button.
2. **Refused cancels are silent** — `core.js:92-97`
   `Studio.cancelJob(job.id).catch(() => {})` discards the 409
   ("already running"): the cancel did NOT happen but the banner
   vanishes and the job spends to completion; on network failure the
   button sticks at "Cancelling…".
3. **Double-submit**: "Extract changes" has no in-flight guard
   (`sessions.js:392-415` — repeated clicks each spend provider
   credits); "Record fact" and "Save session" have no disable
   (`knowledge.js:312-341`, `sessions.js:593-610`).
4. **"Suggest options" spends with no confirm and no timeout**
   (`maps.js:450-469`) — every other metered action confirms first.
5. **Server restart mid-run reads "unknown job: <hex>"** with no
   explanation that the in-memory registry died and the spend is
   lost; the lost-contact copy claims "it may still finish".
6. **Knowledge tab renders any /simulation failure as "No factions —
   create the simulation first"** (`knowledge.js:621-624`
   `catch (_) {}`) — a wrong diagnosis shown as fact.
7. **The extract toast points at a job log that no surface renders**
   (`sessions.js:403`).

## Proposal

Wire the failed-run kinds into `renderSectionError` (the maps/world
pattern); surface cancel refusals as a toast and restore the button;
use `runJob`'s button-gating for extract and in-flight disables for
the record/save forms; `confirmRun` + a timeout for suggest-options;
honest restart/lost-contact copy; render the read-failure in the
knowledge visibility field instead of the confident claim; point the
extract toast at the queue strip (the surface that does name the
job).

## Acceptance criteria

- [ ] A failed week leaves a persistent section error on the Sim tab
      after navigation — cjs harness + studio test on the jobs payload
      shape
- [ ] A refused cancel (409) toasts and re-enables the button — cjs
      harness on `cancelJob`'s catch branch
- [ ] Double-click cannot fire a second extract/record/save — cjs
      harnesses asserting the disabled guard
- [ ] Suggest-options confirms before spending — cjs harness
- [ ] The remaining copy fixes — cjs copy-claims style assertions
