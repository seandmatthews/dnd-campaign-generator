# 39 — Frontend honesty polish: strip, logs, and copy drift from the 2026-10-04 pass

**Status:** proposed
**Priority:** P3 (each item individually small; all are "what the UI claims ≠ what is true")
**Area:** `studio/static/js/` (sim.js, maps.js, core.js), `studio/static/index.html`
**Found by:** computer-use first-DM pass, 2026-10-04

## Problem

Seven small honesty/polish findings from the hands-on pass, grouped as
one frontend lane because each is a render/copy fix:

- **(a) Cancelled jobs linger in the job strip as queued.** After
  "Job cancelled — it was still queued, nothing ran." (verified: server
  logged `cancelled while queued`), the strip kept showing
  `Queued: map-site (position ?)` for 25+ s across polls — `position ?`
  because the job has no queue position anymore. The strip should drop
  cancelled jobs (or label them `cancelled`).
- **(b) ANSI escape codes leak into the worldgen log UI.** The FMG
  headless run's streaming log renders raw `[32m[39m`-style sequences
  inline (visible in the Maps log box during the 4242 run). Strip SGR
  sequences before render.
- **(c) "One metered LLM call" undercounts.** Generate backstory says
  one call; the run meter logged ~3 calls / ~42k tokens (generation +
  audit; a retry can add one). Copy should say "a couple of metered
  passes" or similar. Same section's "a few minutes" is fine (estimate
  framing), but the call count is a factual claim.
- **(d) Accept-confirm modals reuse the spend header.** Accepting a
  week/session/project opens "This costs real credits" — accepting
  spends nothing (the meter never moves). The body text is accurate;
  the header is not.
- **(e) The seed is not shown after generation.** The Seed box clears on
  reload and no UI surface names the seed that made the world ("the
  seed drives every roll" per the form's own copy). Show it in the
  world panel (geography.yaml already carries it).
- **(f) Campaign dropdown sorts by slug, not title.** "The Cinder Vale"
  sorts last after "Reliability Probe" (`the-` prefix). Sort the options
  by display title.
- **(g) Export is undiscoverable.** `export_campaign.py` is CLI-only and
  neither the Help panel nor any tab mentions it. At minimum the Help
  "First campaign?" list or the campaign header should say how to export
  (and that Foundry `.uvtt` files drop from the Maps tab).

Also observed, no action needed: repeated `GET /favicon.ico → 404`
(console noise in every session; adding a favicon or a 204 kills it).

## Proposal

Fix (a)–(g) in `studio/static/js/*` (+ one Help-panel paragraph for g);
(b) may need a server-side strip in the log streaming path instead if
the codes arrive pre-escaped. No behavior changes beyond render/copy.

## Acceptance criteria

- [ ] (a) — after Cancel, the strip no longer lists the job (or lists it
      as cancelled) — pinned by ticket 36 step 12
- [ ] (b) — the worldgen log box contains no `\u001b`/`[NNm` sequences —
      pinned by `studio/tests/js/test_maps_log_clean.cjs` (render the
      streamed lines through the same sanitizer)
- [ ] (c)/(d) — copy strings updated — pinned by
      `studio/tests/js/test_copy_claims.cjs` asserting the rendered
      panels' text
- [ ] (e) — after a world gen, the Maps panel shows the seed — pinned by
      ticket 36 step 4
- [ ] (f) — options render in title order — pinned by
      `studio/tests/js/test_campaign_select_order.cjs`
- [ ] (g) — Help panel names the export command — pinned by the Help
      content test in the same cjs suite

## Implementation notes (YYYY-MM-DD)

Not yet implemented.
