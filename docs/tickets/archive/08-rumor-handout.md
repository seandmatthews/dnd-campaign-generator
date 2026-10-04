# 08 — Rumor → handout pipeline

**Status:** completed — implemented 2026-10-02; code-verified and archived 2026-10-04.
**Priority:** P3, low (good option to have)
**Area:** Simulation tab rumor list → Knowledge tab's record flow

## Problem

Each week's chronicle and state carries rumors already written in-voice
(pilot, week 1: "Travellers swear the thorn along Greywatch Pass has grown
a man's height since the thaw, and the birds have gone quiet where the road
runs."). Getting one to the players means hand-copying it into the
knowledge form and remembering the source. Small friction, real friction.

## Proposal

**"Tell the party"** button on each rumor row (Simulation tab, and in the
chronicle reader's rumor list):

1. Pre-fills the Knowledge tab's record form: `text` = the rumor verbatim,
   `visibility` = `public`, `source_file` = the rumor's chronicle file
   (`simulation/chronicle/0001-….md`), `week` = its week.
2. The GM edits the text if they want (rumors are usually *shaped* before
   they reach the table) and submits through the **existing** record +
   reveal flow — the two-step rule stays: recording a public fact and
   revealing it to the party remain separate decisions.
3. Provenance is automatic: the fact's source file points at the
   chronicle, so staleness/reconcile work unchanged.

That's the whole feature — it reuses knowledge recording, disclosure, and
the handout untouched. The only new code is the prefill (and wiring the
button where rumors render).

## Acceptance criteria

- [x] Rumor rows carry a "Tell the party" affordance that pre-fills the
      knowledge form with text/visibility/source/week.
- [x] Submission goes through the unchanged record → reveal path (no new
      write route).
- [x] Recorded rumor-facts show their chronicle as `source_file` and
      reconcile normally after chronicle edits.
- [x] Fast-tier test for the prefill payload shape.

## Implementation notes (2026-10-02, amended by ticket 16)

Two deviations from the proposal's wording, both deliberate:

- **Where the second button lives.** The spec said "the Simulation tab,
  and in the chronicle reader's rumor list" — but the CANON chronicle
  reader renders markdown only; it has no rumor rows. The second button
  lives on the PROPOSAL panel's proposed rumors (every rumor row that
  renders anywhere — the rumors board and the proposed-week panel —
  carries one). Its source resolves against canon chronicles only, so a
  not-yet-accepted week prefills with no `source_file` rather than a
  staged path the record route would reject.
- **"Text = the rumor verbatim."** The prefill is `displayClaim`: the
  ledger-suffix-stripped claim ("… — succeeded (contested vs mint),
  margin +2" is bookkeeping about the week, not part of the rumor people
  repeat). Deliberate, mirrors the rumors board's display strip, and
  pinned by `test_handoff.cjs`; the DM shapes the wording before
  recording either way.
