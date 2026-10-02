# 16 — Docs & copy consistency sweep

**Priority:** P3
**Area:** `studio/publication.py`, `studio/API.md`, `studio/static/js/{sim,world}.js`,
`docs/tickets/08-rumor-handout.md`, `TUTORIAL.md`

## Problem

Found by the 2026-10-02 post-sweep review — drift left by seven lanes
landing in one evening. None of it breaks anything; all of it misleads the
next reader:

1. **`publication.py:641` — `receipt_with_snapshot`'s docstring claims it
   is "shared by the proposal accept flow and review approvals."** Review
   approvals actually call `publication.receipt` directly
   (`routers/review.py:428, 478, 622`) and never backfill the snapshot —
   the two receipt flows will drift. Either the docstring is wrong or the
   call graph is; pick one. (The layering itself — review approves pending
   lore entries, not proposals — is defensible; this ticket only makes
   the words match reality, or consolidates if that turns out to be a
   one-liner.)
2. **Ticket 08's record vs reality.** The "Tell the party" button's second
   home is the proposal panel's proposed rumors, not "the chronicle
   reader's rumor list" (the canon chronicle reader has no rumor rows),
   and "text = the rumor verbatim" is the ledger-suffix-stripped claim
   (deliberate, tested, and the right call — but the ticket should say
   so). Amend the implementation notes, or move the button — one
   paragraph either way.
3. **Ticket 01's durations are hardcoded in four agreeing places with no
   single source** (TUTORIAL.md, `sim.js` ×2, `maps.js`/`world.js`), plus
   the fragile forecast formula at `sim.js:1019` (`n + '0 minutes'`)
   that silently contradicts the panel's copy if a 1-week or two-digit
   option is ever added to the select.
4. **`world.js:672` — "Copy path" can copy the relative path.** The card
   renders `campaigns/<name>/lore/` and only swaps in the absolute path
   after the `/lore/path` fetch resolves; clicking Copy before it settles
   (or after it fails) contradicts the note "The copy button gives the
   full folder path."

## Proposal

Fix the docstring (or route review approvals through the shared helper if
it is genuinely one line); amend ticket 08's notes to say where the button
lives and what "verbatim" means; centralize the duration strings as one JS
constant the docs cross-check against (or generate from); disable Copy
until the absolute path is known (the relative path stays visible as
text either way).

## Acceptance criteria

- [x] publication.py's docstring matches the call graph (or the call
      graph matches the docstring, with a test pinning whichever wins).
      (The docstring lost: `receipt_with_snapshot` documents its ONE
      caller — proposal_flows — and that review approvals read
      `publication.receipt` directly, by design)
- [x] Ticket 08's implementation notes say where the button actually
      lives and what text it prefills. (Amended in place: proposal-panel
      rumors, not the canon chronicle reader; `displayClaim`'s
      suffix-strip is named as deliberate and test-pinned)
- [x] Duration copy has one source in the JS; the forecast line derives
      from it (no `n + '0'` string math).
      (`Studio.DURATIONS` in core.js — `minutesPerWeek`, `band(weeks)`,
      `approx(weeks)`, `weekRun` — used by all four sim.js sites;
      pinned by `studio/tests/js/test_durations.cjs`, wired as
      run-tests.py's `studio-js-durations` suite. "A few minutes"
      stays prose where it is: it carries no number that can contradict
      another string. TUTORIAL.md mirrors the numbers in prose.)
- [x] Copy path is inert (or honestly copies the relative path with a
      note) until the absolute path is known. (The button renders
      disabled ("Resolving the folder path…"), enables only when the
      resolved absolute path fills the element, and re-disables with a
      title on fetch failure; the relative prefix stays visible as text
      either way)

## Implementation notes (2026-10-02)

- The durations test is a SOURCE contract, not a unit test: core.js
  initializes the DOM at load and cannot run under Node stubs, so the
  test reads the source and asserts the single source exists, the
  `n + '0 minutes'` formula and the hardcoded literals are gone, and
  band(2) still reproduces today's measured "10–20 minutes".
- The review-approvals receipt flow was left as its own flow (two flows,
  two kinds of approval) — consolidation would be its own ticket if the
  drift ever bites; the docstring now says where each lives.
