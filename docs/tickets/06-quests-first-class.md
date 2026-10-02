# 06 — Quests as first-class entities (spec)

**Priority:** P1
**Area:** new `studio/quests.py` read model + panel; briefing integration;
`agentic-simulator` untouched (v1)

## Problem

The pilot campaign's four quests exist only as hand-written lore pages —
prose with wiki-links. Nothing tracks their status, nothing surfaces them
when planning, and the briefing (which is excellent about *events*) has no
notion of *open story*. A GM planning session 3 has to remember what's
hooked and what's live.

The pattern to copy already exists in this repo: **places**. A place is a
markdown page (Obsidian-canonical) + a thin studio read model
(`studio/places.py`) + a registry panel + reviewed short writes. Quests are
the same shape with a different frontmatter contract.

## What we want, exactly

### 1. A quest is a lore page

Markdown under `lore/entities/`, Obsidian-canonical, wiki-linked like every
other page. New frontmatter contract for `kind: quest`:

```yaml
---
kind: quest
name: The Troll's Tithe
status: hooked          # hooked | active | resolved | abandoned (default: hooked)
given_by: Falburn       # optional — page name of the patron
places:                 # optional — page names, matched through the links index
  - Greywatch Hollow
  - Greywatch Pass
factions:               # optional — page or faction names
  - Stoneyvale Wardens
due: 6                  # optional — week number (or ISO date) when the timer runs out
level: "2-4"            # optional, free text, never parsed
---
```

Rules: unknown `status` values fall back to `hooked` with a review finding
(same posture as entity validation elsewhere); everything except `status`
is optional; extra frontmatter keys are ignored, never errors. The body is
prose as today — structure, complications, secrets.

### 2. The read model

`studio/quests.py` (mirror of `places.py`):

- `load(name)` — index all entity pages with `kind: quest`, normalized via
  the links index (page names → place/faction ids where resolvable).
- `quest_view(name, page)` — the page + backlinks + matching engine events?
  (v1: page + backlinks only; event joins via `place_ids`/`subjects` are a
  stretch goal.)
- Status changes: `PATCH /api/campaign/{name}/quests/{page}`
  `{"status": "...", "expected_page_revision": "..."}` — a reviewed short
  write (campaign lock or 409, publication path, revision-checked,
  snapshot). Hand-edits in Obsidian are equally valid; the read model reads
  disk on every list call, so there is no cache to invalidate.

CLI equivalent (guardrail): `python studio/quests.py <campaign> list` /
`show <name>` / `set <name> <status>`.

### 3. The panel

A **Quests** card on World Building (no new tab): quest pages grouped by
status (`hooked` / `active` / `resolved` / `abandoned`) with counts; a row
per quest showing name, `given_by`, due flag (overdue rows are loud);
clicking opens the existing lore-page view. Empty state explains the
frontmatter contract in three lines and links ticket 04's Obsidian card.

### 4. Briefing integration (the payoff)

`briefing.build` gains a **quests section**:

- quests with `status: active` whose `places`/`factions` intersect the
  factions/places changed in the covered week range;
- `hooked` quests count (one line, "3 hooks not shown");
- **overdue** quests: `due` in the past (week or date) while status is not
  `resolved` — listed first, always, because a timer the GM forgot is the
  one that kills the campaign.

The next-session prep file (ticket 07) consumes this section directly.

### 5. Out of scope for v1 (recorded, not forgotten)

- Session-notes extraction proposing status changes (the extractor's
  effect types are engine-typed; quest status is studio-side — needs its
  own suggestion kind). V2.
- Sim factions "knowing" about quests (goals already cover faction intent;
  quests are DM-side structure, not engine state). Deliberately out.
- Quest templates / generators. Ticket 03 owns ideation.

## Acceptance criteria

- [x] Frontmatter contract above parses; unknown statuses degrade to
      `hooked` + finding; non-quest pages are invisible to the read model.
      (`studio/tests/test_quests.py::test_frontmatter_contract_defaults_and_degradation`)
- [x] PATCH route is a reviewed short write (409 on lock, 409 on stale
      revision, snapshot committed).
      (`test_patch_route_is_a_reviewed_short_write`)
- [x] Panel groups by status; overdue detection works for both week
      numbers and ISO dates. (overdue:
      `test_overdue_weeks_dates_boundary_and_resolved_exemption`; panel:
      the Quests card in `static/js/world.js` groups by status and counts,
      against the route contract pinned by
      `test_quest_routes_surface_findings_and_view` — the repo has no
      browser/JS-DOM harness to render the panel itself)
- [x] Briefing includes the quests section with intersection filtering.
      (`test_briefing_quests_section_intersection_and_overdue_first`)
- [x] CLI list/show/set implemented; fast-tier tests for loader, PATCH,
      briefing section, overdue logic. (`test_cli_list_show_set` and the
      suite above; `python -m pytest studio/tests -q` 456 passed,
      `python -m pytest agentic-simulator/tests -q` 219 passed)
