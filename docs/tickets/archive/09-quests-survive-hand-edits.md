# 09 — Quests must survive a hand-edit (read model + every surface)

**Status:** completed — commits 9d91917..eff1282 (2026-10-02); code-verified and archived 2026-10-04.
**Priority:** P1
**Area:** `studio/quests.py`, `studio/briefing.py`, `studio/static/js/world.js`,
`studio/routers/quests.py`, `studio/tests/test_quests.py`

## Problem

Found by the 2026-10-02 post-sweep review. The module's own contract
(`quests.py:20` — *"a hand-edit must never make quests vanish"*) is violated
three ways, and the compensating error finding never reaches a human:

1. **All-or-nothing load.** `load()` catches `CampaignFileError` from
   `spine.entity_pages()` by setting `pages = []` (`quests.py:221`) — but
   `entity_pages` (`spine.py:635`) parses **every** `lore/entities/*.md`
   page, not just quests. One stray tab in any NPC or faction page empties
   the whole read model: the panel, every `quest_view` (404s), the briefing
   section, the overdue timers.
2. **The finding is invisible.** The quests panel renders the generic
   "create `lore/entities/<name>.md`" empty state and returns *before* its
   findings loop (`world.js:770` vs the loop at ~801) — a GM with five
   quests is told they have none and should go create some. The briefing's
   quests markdown (`briefing.py:196–222`) renders statuses only and drops
   the section's `findings`, so overdue timers — the campaign-killer
   scenario ticket 06 exists for — vanish from every briefing silently.
3. **The per-page guard catches the wrong exceptions.** `quests.py:233`
   catches `(OSError, QuestError)`, but `_frontmatter_of` raises
   `places.PlaceError` (`places.py:83`) and `_page_snapshot` can raise
   `UnicodeDecodeError` — so the advertised `quests/page-unreadable`
   degradation is dead code, and a page edited mid-read produces an
   unmapped 500.

Smaller items in the same read model, same ticket:

- `abandoned` quests are never exempt from overdue (`quests.py:127` exempts
  only `resolved`): an abandoned quest with a past `due` pollutes the
  OVERDUE block of every future briefing forever. Spec-faithful to ticket
  06's wording, but almost certainly not what anyone wants.
- Duplicate quest display names produce no finding (places has one) and the
  CLI resolves them arbitrarily (`quests.py:448`).
- `patch_quest` (`routers/quests.py:67`) skips the `valid_page_name` check
  `get_quest` performs (fails closed today via the dict lookup — the
  asymmetry invites drift).
- CLI `set` calls `canon.snapshot` directly (`quests.py:522`) and skips
  `publication.after_snapshot`, leaving the transaction's staging dir on
  disk and the manifest's `snapshot` field `None` — while `API.md:730`
  claims it "commits the snapshot like the route".
- Test gaps that let all of the above through: nothing covers an
  unparsable entity page, PATCH with `expected_page_revision` omitted, or
  duplicate names.

## Proposal

1. Degrade per-page: parse failures cost their own page plus an error
   finding — never the model. Either catch inside the loop or make
   `entity_pages` yield per-page errors alongside parsed pages.
2. Findings reach humans: the quests panel renders findings in the empty
   state too (move the loop before the early return); the briefing's quests
   markdown carries error findings into the document.
3. Fix the guard's exception tuple (`PlaceError`, `ValueError`).
4. Exempt `abandoned` from overdue (recommended; alternatively stop the
   timer at abandon time) — either rule, pinned by a test.
5. Add a `quests/duplicate-name` finding, mirroring places.
6. `patch_quest` validates the page name exactly like `get_quest`.
7. CLI `set` finalizes through the same path as the route
   (`finalize_publication`), so staging is pruned and the manifest carries
   the snapshot.

## Acceptance criteria

- [x] A campaign with one unparsable entity page (quest or non-quest)
      still lists every other quest — panel and briefing — with an error
      finding naming the bad file.
      (`test_one_bad_entity_page_costs_only_itself`,
      `test_findings_surface_even_with_zero_quests`)
- [x] The quests panel renders findings in the empty state; the briefing's
      quests section renders its error findings into the markdown.
      (world.js renders findings before the empty-state early return;
      briefing `_quests_markdown` + `_quests_present` count error
      findings — the briefing half is test-pinned, the panel half rides
      the route contract above; the repo has no DOM harness)
- [x] A page edited mid-load degrades to a finding (no 500).
      (`test_page_vanishing_mid_read_costs_only_itself`)
- [x] Abandoned quests do not pollute the overdue block (rule pinned by a
      test either way). (`test_abandoned_is_exempt_from_overdue` — both
      resolved and abandoned exempt)
- [x] Duplicate quest names produce a finding; the CLI refuses ambiguity
      or resolves deterministically.
      (`test_duplicate_names_flagged_and_cli_refuses` — show/set by a
      shared name exit 2 naming the stems)
- [x] PATCH validates page names like GET; CLI `set` leaves no staging
      behind and the manifest records the snapshot.
      (`test_cli_set_finalizes_like_the_route` — on a committed snapshot
      the staging is pruned, which is where the old path's manifests
      piled up; a failed snapshot still retains its staging for recovery)
- [x] New fast-tier tests: bad entity page (quest + non-quest variants),
      findings-in-empty-state, findings-in-briefing, abandoned-overdue,
      duplicate names, revision-omitted PATCH; all existing suites green.
      (`python -m pytest studio/tests -q` 495 passed)

## Implementation notes (2026-10-02)

- `quests._entity_pages_degraded` mirrors `spine.entity_pages` but catches
  per file: one unparsable page costs itself plus a `quests/lore-unreadable`
  error finding. `spine.entity_pages` itself is unchanged (other callers —
  loregen, review — still want the strict raise).
- The per-page race guard catches `(OSError, ValueError, PlaceError)` —
  the three things `_page_snapshot`/`_frontmatter_of` actually raise.
- The abandoned-overdue rule chosen: exempt (a story you walked away from
  owes no timer). Ticket 06's spec text said "not resolved"; 06's record
  stands as history, this ticket is the ruling that changed it.
- CLI `pick()` returns matches, not a guess: 0 → exit 1, 2+ → exit 2 with
  the stems listed. CLI `set` now runs `canon.snapshot_result` +
  `publication.after_snapshot` — the same pair
  `routers.common.finalize_publication` performs (the CLI cannot import
  the router without inverting the layering).
- Briefing: error-level quest findings render under the section header;
  `_quests_present` counts them so a questless campaign with a broken
  page still gets the section. Warnings stay panel/CLI-only to keep
  briefings short.
