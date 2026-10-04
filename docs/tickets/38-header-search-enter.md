# 38 — Header search: Enter does not submit under two independent input paths

**Status:** proposed
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

- [ ] Human check recorded in Implementation notes (reproduces / does
      not) before any code change lands
- [ ] If reproduces: typing "clocks" + Enter opens `.search-panel` with
      hits, and empty + Enter shows the info toast — pinned by ticket 36
      step 13 (`studio/tests/e2e/first_dm.spec.ts`)
- [ ] Esc and the × button still close the panel — pinned by the same
      spec

## Implementation notes (YYYY-MM-DD)

Not yet implemented.
