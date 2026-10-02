# 30 — Frontend failure surfaces, copy & dead affordances

**Priority:** P2
**Area:** `studio/static/js/world.js`, `sessions.js`, `sim.js`, `core.js`
**Found by:** 2026-10-02 full review P2-6, P2-7, P3-32, P3-33, P3-34, P3-35

## Problem

1. **Entities table click-to-open is dead (P2-6, trace).**
   `normalizeEntity` (`world.js:25-33`) rebuilds each entity without the
   `file` field; `entitiesTable` keys row styling and the click handler on
   `e.file` (`:160-171`) — always undefined. The page-open feature
   (f1522e7) is unreachable from the Entities table.
2. **Sessions tab renders blank on fetch failure (P2-7).** The session
   click handler (`sessions.js:497-504`) clears the DOM *before* unguarded
   `await S.api(...)` continuations; showTab's render-level `.catch`
   doesn't cover handlers. Server down/state.yaml missing → empty tab, no
   error panel, console error only. Every other tab degrades.
3. **Prepare's inline failure message is destroyed by the re-render in the
   same catch block (P3-33).** `sessions.js:448-455`: every error except
   the awaiting-review 409 (including the draft-revision 409 with its
   "reload and reapply" guidance) sets `errBox.textContent` then
   immediately re-renders the tab, wiping it, with no toast.
4. **Accepting a `session_changes` proposal toasts "Week N is canon"
   (P3-32).** `sim.js:798-805` has the goals/sessions/projects branch in
   the title chain but not the toast chain.
5. **Conflict-parked transactions have no UI (P3-34).** `snapshotBanner`
   (`core.js:289-297`) checks snapshot states only; a
   `status: "conflict"` tx has no snapshot, and
   `POST .../publication/resolve` has no frontend caller — a
   crashed-approval conflict (which 409-blocks all publication writes)
   is resolvable only via curl.
6. **Queue-strip poll has no stale-response guard (P3-35, suspected).**
   `core.js:277-283`: 5 s interval, no sequencing — a slow response can
   briefly resurrect a finished job after a newer snapshot rendered
   (self-corrects next tick; `pollJob` itself is properly guarded).

## Proposal

- Carry `file` through `normalizeEntity`; guard the session click handler
  (render the error panel like every tab); move the prepare error message
  AFTER the re-render decision (render-then-set, or toast for the 409
  cases); add the session_changes branch to the toast; render a
  conflict-parked banner with a Resolve button wired to the existing
  endpoint; sequence the queue poll like `pollJob` (inFlight guard).

## Acceptance criteria

- [x] Entities rows with backing pages are styled clickable and open the
      page view (browser-verified or DOM-tested).
- [x] Killing the server then clicking a session shows the standard error
      panel, not a blank tab.
- [x] Prepare failures (both 409 flavors) leave a visible, actionable
      message on screen.
- [x] session_changes accept toast names what actually published.
- [x] A conflict-parked transaction renders a banner with a working
      Resolve action (against a fixture tx).
- [x] Queue strip never renders an older snapshot after a newer one
      (sequencing test or reasoned note if untestable in node).
