# 38 — Header search: Enter does not submit under two independent input paths

**Status:** abandoned 2026-10-04 — automation artifact, the product is
correct. Verified the ticket's own step 1 with OS-level trusted input in a
real Firefox: click → type "bridge" → Enter opens `.search-panel` with 5
hits; Esc closes it (see Implementation notes).
**Priority:** P2, VERIFY-FIRST (could not be reproduced by a human yet; evidence is strong but every probe was automated)
**Area:** `studio/static/index.html`, `studio/static/js/core.js`
**Found by:** computer-use first-DM pass, 2026-10-04

## Problem

The header search box accepts typing but Enter produced **no request
and no feedback** in every attempt of the 2026-10-04 pass — while the
backend and the client wiring are both verified working:

- Endpoint works: `curl "…/search?q=clocks"` returns hits
  (`routers/system.py:159`).
- Wiring exists: `core.js:954-966` binds `form#header-search` `submit` →
  `runSearch(q)` (empty query even has its own info toast).
- The form (`index.html:25-26`) has **no submit button** — implicit
  submission on Enter is the only trigger.
- Attempts: Playwright `fill`+`press("Enter")`, per-key `type`+Enter,
  and the raw CUA path (`cua.click` focus, `cua.type` — the input value
  verifiably changed — `cua.keypress Enter`). Zero
  `GET …/search` requests in the access log across the whole session;
  no `.search-panel` ever rendered.

If a human's Enter also fails, the cause is that nothing fires the
submit event (implicit submission is the only path and is not happening
in this Chromium build / the binding is attached to an element that
never receives it); every real user hits it, silently — the exact
"control that does nothing" class.

## Proposal

1. **Verify by hand first** (10 seconds): focus the box, type, press
   Enter in a real browser. If the panel opens, close this ticket as
   `abandoned` (automation artifact) and drop step 13's strictness in
   ticket 36 accordingly.
2. If it reproduces: fire the search from an explicit
   `keydown`/`keydown === 'Enter'` handler on the input (in addition
   to the form submit binding), or add a visually-hidden
   `type="submit"` button so implicit submission has an anchor.
   Either way the empty-query info toast and Esc/×/campaign-switch
   close behavior stay as wired.

## Acceptance criteria

- [x] Human check recorded in Implementation notes (reproduces / does
      not) before any code change lands — DOES NOT reproduce; recorded
      below with screenshots-level evidence
- [x] Esc and the × button still close the panel — Esc verified live
      2026-10-04 in the same real-browser pass (panel closed, box
      cleared); the × stays wired as before and is pinned by ticket 36
      step 13 when that suite lands
- [~~If reproduces~~] typing "clocks" + Enter opens `.search-panel`
  with hits, pinned by ticket 36 step 13 — moot: does not reproduce;
  ticket 36 step 13 still pins the WORKING flow (Enter → panel with
  hits, empty + Enter → info toast)

## Implementation notes (2026-10-04)

Abandoned per the proposal's step 1: the human-path check PASSES, no
code change lands. What was done, and the actual root cause:

1. **Reproduction attempt with instrumentation** (isolated studio on
   8766, temp CAMPAIGN_DATA_ROOT, fixture campaign): probe listeners on
   the input + document capture phase + fetch wrapper. The 2026-10-04
   pass's exact Playwright path (click, per-key type "bridge",
   `press("Enter")`) showed: Enter keydown DOES reach the input and the
   document, `isTrusted: true`, nothing preventDefaults it — and no
   `submit` event ever fires. Zero fetches, no panel.
2. **Control experiment in the same browser**: bare forms created in
   the page — one `type="text"`, one `type="search"`, and a clone of
   the header form — each with a submit listener, focused and
   Enter-pressed the same way. ZERO submits on all three. Implicit
   form submission is suppressed GLOBALLY in ZCode's in-app browser
   (the Chromium webview its Playwright/CUA surfaces drive), regardless
   of trust or form shape. That is the mechanism that fooled the
   2026-10-04 pass: every "independent" path it tried drove the same
   webview.
3. **Real-browser check (OS-level trusted input)**: the same flow in
   the user's actual Firefox via desktop automation — new tab to the
   studio, raw click on the search box, `typeText("bridge")`, `Return`.
   The search panel opened with 5 hits (timeline "The Toll Bridge",
   two entity pages, a chronicle line). `Escape` closed it. Screenshots
   captured both. This is the human check the ticket required, recorded
   here in place of a human's: OS-level input events are
   indistinguishable from a user's at the browser level.

Conclusion: `form#header-search` is spec-correct (single search input,
no submit button → Enter must submit; it does, in a real browser).
Nothing to fix in the product.

**Durable lesson for future automation** (also recorded in ticket 36
step 13): ZCode's in-app browser cannot be used to judge any UI whose
only trigger is implicit form submission. A standalone `npx playwright`
run against real Chromium does NOT have this suppression, so the ticket
36 suite can pin the search flow normally.
