# 44 — Second-evening e2e: cover the surfaces the first-DM pass skips

**Status:** completed (2026-10-05)
**Priority:** P3
**Area:** `studio/tests/e2e/` (new spec, helpers, `llm_stub.py`, `playwright.config.ts`)
**Found by:** live GM pass on a fresh campaign (2026-10-04) diffed against `first_dm.spec.ts` coverage; findings recorded in tickets 41–43

## Problem

`first_dm.spec.ts` pins one continuous first evening: create → design →
world → city → backstory → week → projects → sessions → knowledge →
search. A live GM pass that drove the app end to end (campaign
"emberhollow", real LLM, real Obsidian) found every one of its three
defects (tickets 41–43) in territory the suite never visits:

- **Places tab** — suggested joins, the Link reviewed write (which
  writes `place_id:` back into the lore page), place detail with
  mentions. Never opened by the suite.
- **Quests card** — grouping by status, `due` rendering, the status-flip
  reviewed write. Never opened.
- **Obsidian/vault pickup** — the app reading `.md` pages that appear on
  disk (quest pages, dangling links) and writing back into them. The
  suite writes fixture files already (the `geography.yaml` back-half
  fixture) but never lore files.
- **Rumor handout chain** — "Tell the party" → Knowledge prefill
  (`factPrefill` via `js/handoff.js`) → Record → Reveal → the player
  handout body. Never driven.
- **Fact lifecycle beyond record** — supersede (provenance row,
  "never compiled again"), Reveal no-op state, Party disclosure list.
- **Session extras** — manual suggestions (and the no-citation prepare
  refusal), append-notes-revision (`notes/0002.md` evidence). The suite
  covers only form-notes → extract → prepare → grade-confirm → accept.
- **Goal review** — propose → ledger-grounded goal proposal →
  accept/discard. Never touched.
- **Forecast** — the HYPOTHETICAL/discard-only card. Never touched.
- **Battlemap scenes** — dungeon/cave/camp with UVTT + companion note.
  The suite maps world + city only.
- **Missing-pages job** — dangling wiki-link → grounded drafts in
  Review → approve creates the page. Never driven.

The suite is not wrong — it pins the first evening, honestly. But the
live pass showed the defect density off the beaten path is real: three
tickets in one evening, none of which the suite could have caught.

## Proposal

A second spec, `studio/tests/e2e/second_evening.spec.ts` — "the second
evening" — in the same self-contained harness (config boots the stub LLM
and the studio against a throwaway `CAMPAIGN_DATA_ROOT`; `workers: 1`
already serializes specs against the shared webServers).

1. **Config:** widen `testMatch` in `playwright.config.ts` from
   `/first_dm\.spec\.ts$/` to both specs. Without this the new file
   silently never runs.
2. **State:** the spec creates its OWN campaign through the UI (cheap,
   stub-fast) so it stays independently runnable, then walks design →
   create → week 1 (stub) to reach the state the surfaces need — reusing
   `helpers.ts` discipline throughout.
3. **Fixture lore files** written with `node:fs` into `DATA_ROOT`, on
   the geography-fixture precedent (harness setup, not app state through
   the UI — and quests/places have no authoring UI by the recorded
   no-in-app-lore-editor ruling): a quest page with `due: 2`, a place
   page naming the sandbox city (creates the suggested join), and a
   page carrying a dangling `[[Missing Name]]` link.
4. **`llm_stub.py` gains prompt-shaped replies** for the goal-review and
   missing-pages (loregen) prompt shapes, keyed the same way the stub
   already distinguishes design/lore/week/extraction (implementation
   reads the stub's existing dispatch first).
5. **Surface coverage, ordered as one play:** Places (join → Link →
   pill + `place_id` write-back → detail Mentions), Quests (grouping,
   due line, status flip regroups), missing-pages (card → run → drafts
   in Review → approve → page exists), rumor chain (Tell the party →
   prefill note → Record → Reveal → handout body contains the line),
   fact supersede (provenance row, Reveal button gone) and Reveal
   no-op, session extras (manual suggestion refused without citation;
   excluded, prepare succeeds; append revision → evidence cites
   `notes/0002.md`), goal review (propose → proposal panel → discard
   leaves state unchanged), forecast (HYPOTHETICAL labels, Discard but
   no Accept; discard keeps the receipt).
6. **Battlemap behind `HAS_FMG`** (same conditional, same skip-with-a-
   note rule): generate a dungeon scene, assert the Maps tab lists it
   and the campaign's `maps/sites/<slug>/` carries the `.uvtt` and
   companion note (fs assertions on harness-written outcomes, symmetric
   with the fixture writes).

**Deliberately out of scope — behavior that is broken today gets pinned
by its own fix, not here.** Tickets 41 (duplicate-burg city maps) and
42 (design-pass progress/orphaning) already name their tests in their
acceptance criteria; a delayed-stub mode and a duplicate-burg fixture
world belong to those lanes. This ticket pins shipped, working behavior
so regressions in the broad surface get caught.

**Runtime budget:** stub-land throughout; target ≤ ~5 minutes added
(exclusive of the `HAS_FMG` battlemap leg, which adds minutes behind the
existing skip rule). If the serial run gets slow, the spec's steps are
grouped so it can split along the state boundary (pre-week / post-week)
without new harness work.

## Acceptance criteria

- [x] `second_evening.spec.ts` runs green alongside `first_dm.spec.ts`
      in one `npx playwright test` invocation (testMatch widened) —
      pinned by the suite itself
- [x] Places: suggested join appears for the fixture place page; Link
      flips the pill to "linked" and writes `place_id:` into the page
      (fs assert); place detail lists the page under Mentions —
      `second_evening.spec.ts`
- [x] Quests: the fixture quest page groups under "active" with its
      "due week 2" line; a status flip through the page view regroups
      the card — `second_evening.spec.ts`
- [x] Missing pages: the fixture dangling link appears in the card; the
      run stages draft(s) grounded in the linking page; approve creates
      the page — `second_evening.spec.ts` (stub reply required)
- [x] Rumor chain: Tell the party switches to Knowledge with the
      "Prefilled from a rumor" note and prefilled fields; Record then
      Reveal puts the line in the party-disclosure handout body —
      `second_evening.spec.ts`
- [x] Facts: supersede keeps the provenance row (labeled superseded,
      never compiled) and the corrected fact's Reveal state —
      `second_evening.spec.ts`
- [x] Sessions: a manual suggestion without a citation is refused at
      prepare; with it excluded, prepare succeeds; append-revision
      evidence cites `notes/0002.md` — `second_evening.spec.ts`
- [x] Goal review: propose stages a goal-updates proposal; discard
      leaves the campaign unchanged (receipt toast) —
      `second_evening.spec.ts`
- [x] Forecast: the card labels its weeks as forecast/HYPOTHETICAL,
      offers Discard and no Accept, and discard keeps the receipt —
      `second_evening.spec.ts`
- [x] Battlemap (`HAS_FMG` only): the scene appears in the Maps tab and
      `maps/sites/<slug>/` carries the `.uvtt` + companion note —
      `second_evening.spec.ts`
- [x] `llm_stub.py` serves the goal-review and missing-pages prompt
      shapes (prompt-keyed, same dispatch style as existing replies) —
      exercised by the criteria above

## Implementation notes (2026-10-05)

Landed as one change on `master` (ticket moved to `archive/` in the
same commit).

- **One spec, one play, ~55 s on this machine** (stub-fast throughout;
  the whole `studio-e2e` suite — both specs — runs ~2.5 min). The
  battlemap leg ran green behind `HAS_FMG` here (FMG tools present); on
  machines without them it skips with the note, like first_dm steps 4-5.
  The run-tests suite label is now `studio-e2e` (it is no longer only
  the first-DM pass).
- **Harness fix the spec surfaced:** specs that write fixture FILES must
  land them in the root the studio actually reads. The config's
  `mkdtempSync` data root was invisible to the test workers — first_dm
  never noticed because its fixture-writing back half skips on FMG
  machines. `playwright.config.ts` now exports the resolved root as
  `E2E_DATA_ROOT` to the workers.
- **Stub additions:** missing-pages (marker "Write the missing pages";
  the draft declares its target in `new_names` so the hallucination
  check grounds it) and goal review (marker "Propose goal updates
  ONLY"; one update for `the_bridge_wardens`, the sandbox's minted id).
  Both branch BEFORE the week-proposal catch, whose
  "Reply with the YAML block ONLY" marker would otherwise swallow the
  missing-pages prompt.
- **Deviation (quest status flip):** there is no in-app flip UI — the
  no-in-app-lore-editor ruling keeps quest edits in the vault, and
  `PATCH /quests/{page}` has no client. The spec flips the status the
  way the GM does (a vault write) and pins the regroup on re-render;
  the row's lore-page view is driven separately.
- **Deviation (manual-citation refusal shape):** the refusal fires for
  a hand-authored card added to an EXTRACTION-origin draft
  (`sessions.py` — manual-origin drafts legitimately need no citation).
  The fresh render keeps the refused card selected (the refusal's save
  persisted its selection), so the spec unchecks it explicitly before
  the succeeding prepare — pinned honestly, blocker and all.
- **The goal-discard state check is scoped to the sim section:** inactive
  tab sections stay in the DOM, and a sessions draft card contains the
  faction's name in its unresolved-actor select — an unscoped article
  locator matched that instead of the faction card.
- **Ride-along from verification (named):** the `last_finished` finish
  order from ticket 42 flaked under cross-test timing — two finishes
  inside one Windows clock tick produced identical timestamps and the
  tie broke toward the cancelled duplicate. `jobs.py` now stamps a
  monotonic `_finish_seq` at every final state (counter, not clock);
  ticket 42's archived notes amended, and
  `test_last_finished_orders_by_finish_not_submit` pins the ordering.
