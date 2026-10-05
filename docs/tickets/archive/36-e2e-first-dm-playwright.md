# 36 — E2E: the first-DM pass as a Playwright suite

**Status:** completed — implemented + verified 2026-10-04; the whole
fast tier (suite included) is green locally, the no-FMG CI leg verified
via E2E_SKIP_FMG=1 (see Implementation notes).
**Priority:** P2 (the app's core promise — "a wrong thing is never silent" — is only verified by a manual ritual today)
**Area:** `studio/tests/e2e/` (new), `run-tests.py`, `.github/workflows/ci.yml`
**Found by:** computer-use first-DM pass, 2026-10-04 (this ticket is that pass's record; findings split out as 37/38/39)

## Problem

There is no automated browser E2E. "Boot the studio, create a campaign
from nothing, gen world → maps → lore → sim → week 1, accept it" exists
only as a ritual an agent performs by hand (Greywatch 2026-10-01, GUI QA
2026-10-02, Cinder Vale 2026-10-03/04, this pass 2026-10-04). The fast
tier fakes every engine; the integration tier runs real engines but no
UI; the JS harness pins logic, not flows. Every reliability property the
product advertises — cost gates, honest refusals, exactly-once accepts,
the serial job queue — is verified by hand or not at all.

The 2026-10-04 hands-on pass proves the point: it found a blocking
approval deadlock (37) that no tier covers, and a dead control (38).

## Proposal

A Playwright suite under `studio/tests/e2e/` that replays the first-DM
happy path plus the reliability probes below, against a real server with
a **stub LLM provider** (record/replay or a tiny static JSON responder
on 127.0.0.1; the studio already reads its provider from `llm.env`, so
the fixture writes one into a temp `CAMPAIGN_DATA_ROOT`). Runs in the
fast tier's timescale (< 3 min), on ubuntu + windows in CI.

### Setup (before any step)

1. Temp `CAMPAIGN_DATA_ROOT` (ticket 35's rule means one env var isolates
   everything); `llm.env` pointing at the stub provider; `STUDIO_PORT`
   set and uvicorn launched with the SAME value (booting uvicorn without
   `STUDIO_PORT` gives 403 "unknown host header" on every request — the
   host allowlist derives from that env).
2. Assert boot: `GET /api/campaigns` → 200.

### The scripted pass (selectors from the 2026-10-04 run)

Selector discipline learned live: nav tab names need `{ exact: true }`
("Review" substring-matches "Stage … for review"); form refusals render
in an inline `.error` box INSIDE the form — assert there first, toasts
(`.toast`) are secondary; in-flight buttons disable (assert `disabled`
during the wait, then wait for it to re-enable).

1. **Create campaign**: `+ New` (exact) → dialog "New campaign" → fill
   Name `e2e-probe`, Title → Create → `#campaign-select` has option
   "E2E Probe" selected.
   - Probe: Name `probe & friends!` → inline 422 "must be a lowercase
     slug", dialog still open, no folder created.
2. **Design the sandbox** (Agent Simulation tab): empty premise →
   "Design the sandbox" → toast "Describe the tension first". Fill
   premise, no places, no world → inline 422 naming `world/geography.yaml`.
   Fill places `Harnost` → click once, assert button disabled within 2s,
   click again (double-submit probe), assert exactly ONE
   `POST …/simulation/design` (200) → form prefilled (one-liner nonempty,
   ≥2 faction rows, Regenerate button present).
3. **Create simulation** → toast "Simulation created — factions are live
   below." → Week 0 banner, ≥2 faction articles.
4. **World gen** (Maps tab): empty seed → toast "Type a seed first" +
   inline explanation. Seed `4242` + culture `highFantasy` → Generate →
   button disabled → **reload mid-run** → button still disabled (no
   double-run), then wait done → World map image present, "Map a burg"
   combobox populated. Assert regenerate now requires the `force`
   checkbox (button disabled without it; the 409 note renders).
5. **City map**: pick largest burg → "Map it" → disabled in-flight →
   Cities section gains the `<name>` card.
6. **Backstory** (World Building): Focus text → Generate → cost modal
   ("This costs real credits") → Cancel first (assert nothing started),
   re-run → Confirm → stub returns 6 entries → Review shows
   "N pending entries"; per-entry verification findings render.
7. **Week 1** (Agent Simulation): Propose week 1 → cost modal → Confirm
   → while any job runs: stage a project ("+ New project" → Stage) →
   assert inline 409 "another job is already modifying this campaign …
   Blocked by: … Nothing was changed." with the form still open and
   values intact. Approve-all in Review → same inline 409 + toast.
   When the week stages: "Proposed week 1 — not canon yet" banner, diff
   table rows, Accept → cost modal → Confirm → toast "Week 1 is canon —
   accepted exactly once.", Week 1 banner, faction chips match the diff.
8. **Timeline collision regression** (pins 37, xfail until it lands):
   approving the pending entries after the week accept succeeds (today:
   "id t0001 already exists").
9. **Project**: re-stage (queue idle now) → proposal banner → Accept →
   toast "…applied exactly once." → project card "active 0/4".
10. **Session**: Sessions tab → save title+notes → open it → Extract →
    cost modal → Confirm → suggestions render, each with a blockquote +
    `notes/0001.md:1-1` source; select all → Prepare → refusal naming
    the suggestion needing grade confirmation → tick its
    `.ssn-confirm-grade` → Prepare → toast "Staged proposal …" → accept
    on Agent Simulation → toast "Session changes are canon…", Knowledge
    gains the DM-only facts.
11. **Knowledge/briefing**: "Tell the party" on a rumor → Record-a-fact
    form prefilled (assert note "revealing it to the party stays its own
    step") → Record → toast; DM briefing list capped at 10 with the
    "N more not shown" line; "Next session prep" → prep link written,
    re-run takes `-2` (never overwrites).
12. **Queue honesty**: queued job's Cancel → toast "Job cancelled — it
    was still queued, nothing ran." and the strip DROPS it (pins 39a).
    Discard a staged proposal → toast "…the campaign never changed.",
    state unchanged.
13. **Search** (pins the flow ticket 38 closed as an automation
    artifact, 2026-10-04): type "clocks" in `#search-input`, Enter →
    `.search-panel` opens with hits; empty + Enter → the info toast.
    TRAP: ZCode's in-app browser suppresses implicit form submission
    GLOBALLY (even a bare one-input form never fires submit on Enter
    there — that is what fooled the 2026-10-04 pass; ticket 38's notes
    have the control experiment). This suite runs standalone
    `npx playwright` against real Chromium, which does NOT have the
    suppression — if Enter ever fails to submit here, suspect the
    runner, not the form. Esc and the × button close the panel.

Export stays a CLI step (`export_campaign.py`) — covered by the
integration tier, asserted here only as "no export affordance exists"
(documented gap, see 39g).

### Acceptance criteria

- [x] Suite lives in `studio/tests/e2e/`, runs via `run-tests.py` fast
      tier when the local @playwright/test install exists (skip with a
      note when not, like the JS suites) — pinned by the runner change
      itself (`studio-e2e-first-dm` suite)
- [x] Steps 1–13 green against the stub provider on chromium — pinned
      by `studio/tests/e2e/first_dm.spec.ts` (verified locally on
      Windows; the ubuntu leg runs the same spec in CI, see below)
- [x] CI runs the suite on both legs — `.github/workflows/ci.yml` gains
      the browser-install step; on CI the suite runs the no-FMG leg
      (tools are git-ignored), the full-FMG leg runs locally
- [x] The selector notes above (exact tab targeting, inline `.error`
      over toasts, disabled-state waits) are encoded in a shared
      `helpers.ts` — pinned by the suite importing it

## Ruling (do not revisit)

The suite drives ONLY visible UI (black-box): no JS injection, no URL
construction, no API shortcuts for state the UI must produce. Reads of
in-page state for assertions are allowed. This matches how the 2026-10-04
pass ran and keeps the suite honest about affordances.

## Implementation notes (2026-10-04)

Landed as one change on `master` (ticket moved to `archive/` in the
same commit). Everything lives in `studio/tests/e2e/` (a new npm-scoped
package; the repo root gains no node_modules):

- `llm_stub.py` — the stub provider: stdlib OpenAI-compatible
  /chat/completions on 127.0.0.1, one canned body per flow, chosen by
  prompt markers. Non-obvious lessons baked in:
  - backstory replies are GROUNDED in the prompt's own geography
    (`name:` values) — loregen drops ungrounded multi-word names
    (loregen/hallucinated-name is an error finding), so a fixed reply
    only works for one world;
  - proposal action text is UNIQUE per faction AND per pass — two
    factions (or two weeks) with the identical action give the week
    bundle two identical timeline entries and the accept's
    duplicate-identity guard refuses the whole publish (real product
    behavior, now ticket 40);
  - the extraction reply includes a `resource_change` (the suggestion
    kind that sets requires_confirmation=["resource_grade"]) so the
    prepare-refusal probe is honest;
  - the week chronicle call sleeps (E2E_LLM_DELAY_S, default 12s) to
    give the mid-run 409 / queued-cancel probes a running job. The gate
    is the chronicle's TITLE-line instruction — "chronicler of" alone
    also matches loregen's prompts (found live: a 45s stall that read
    like a hang).
- `playwright.config.ts` — webServer array boots the stub AND the
  studio (uvicorn, STUDIO_PORT set so the host allowlist matches, per
  the ticket's setup note) against a throwaway CAMPAIGN_DATA_ROOT; one
  worker; E2E_DATA_ROOT overrides for inspecting failures.
- `helpers.ts` — navTab (data-tab attribute + visible-name assert),
  waitToast, confirmModal/cancelModal (asserting the 39d headers),
  countPostsDuring (network observation only — the black-box ruling
  holds), inlineError.
- `first_dm.spec.ts` — the 13 steps as two tests that partition on the
  FMG tools' presence: front half (1-5, full world+city generation)
  and back half (6-13). With FMG (local): one test, ~1.5 min total.
  Without (CI): the front half ends after step 3 with a note, a
  minimal geography.yaml drops into the throwaway root, and the back
  half covers 6-13 — the same rule as the integration tier's ignored
  prerequisites. E2E_SKIP_FMG=1 forces the CI leg locally (that is how
  it was verified: both legs green).

Deviations from the ticket text, with reasons:

- The search probe searches "bridge", not "clocks" (fixture world).
- The mid-run world-gen probe asserts the queue strip ("Running:") on
  the reloaded page, not a disabled button — nothing re-disables the
  button across a reload (the disabled state lives in the page's JS
  watch, which the reload detaches); the no-double-run guarantee is
  the server's 409/force gate. The original "button still disabled"
  reading was a misdiagnosis of what the DOM carries.
- Step 7's project stage has NO cost modal in the shipped UI (the
  form's Stage button POSTs directly; the 39d "Staging is free"
  header belongs to the project-card action path) — the spec matches
  the shipped affordance.
- Step 8 approves through ticket 40's provenance gate: the E2E found
  the week accept still dead-ends the draft (stale-review 409 naming
  its remedy), so the suite asserts the honest remedy (delete →
  regenerate → approve) until 40 lands. Ticket 37's renumbering
  itself is pinned at unit level (test_review.py).
- Step 11's briefing-cap probe drives extra WHOLE WEEKS to overflow
  the cap (facts and DM-only notes are not briefing items; events and
  rumors are) instead of filler facts.
- The `+ New` dialog refusal asserts the inline `.modal-error` box
  (the dialog keeps itself open with the reason — an improvement
  since the pass).

Also fixed while landing: the `.gitignore` for the package
(node_modules/test-results/playwright-report), and `package-lock.json`
is committed so CI's `npm ci` is pinned.
