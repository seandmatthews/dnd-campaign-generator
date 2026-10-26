# 64 — Canon write discipline: non-atomic artifacts, slug collisions, export gaps

**Status:** in-progress — implemented on ticket/64-canon-write-discipline
2026-10-25 (fast tier green; review pending)
**Priority:** P3 — three classes of quiet wrongness in canon writes,
all with cheap fixes
**Area:** `studio/routers/maps.py`, `studio/worldmap.py`,
`studio/export_campaign.py`, `studio/obsidian_vault.py`
**Found by:** nine-lane adversarial review 2026-10-23 (backend lane)

## Problem

1. **Non-atomic canon writes**: `maps.py:317` (companion note),
   `maps.py:756` (site params.yaml), `worldmap.py:197,212`
   (world/map.svg) use plain `write_text` — a crash mid-write leaves
   a truncated canon file that the NEXT snapshot commits under an
   unrelated message. Everything else in the spine uses
   `write_text_atomic`.
2. **Slug-collision silent overwrite**: two distinct names that
   slugify identically ("Drowned Crypt" vs "drowned crypt") share one
   `maps/<kind>/<slug>/` destination — the second job silently
   replaces the first's artifacts (`maps.py:476-479, 746-754`).
   `routers/review.py:406-426` refuses exactly this class with a
   409; the map jobs don't. `_BAD_SITE_NAME` also lacks the
   control-char class every spine validator carries.
3. **Export gaps**: `export_campaign.py:122` never validates the
   campaign argument (restore does — export can zip a directory
   outside the campaigns root), and `EXCLUDES` omits `.obsidian/`,
   which `canon.WORKSPACE_IGNORE` declares chalk — the export trust
   boundary disagrees with the git one. Bonus: the obsidian.json
   backup is single-generation (`obsidian_vault.py:120-121`) — after
   a second registration the ORIGINAL vault list is unrecoverable.

## Proposal

Atomic writes at all four sites; a destination-exists 409 on the
city/site jobs (matching review's guard, `--force`-free: regenerate
of the SAME site is the existing overwrite case, so key the guard on
a differing request — or take a ticket-time ruling: refuse vs
version-suffix like the publisher's rule 3); `require_campaign` on
export; `.obsidian/` in EXCLUDES; timestamped obsidian backups
(keep 3).

## Acceptance criteria

- [x] All four canon writes go through `write_text_atomic` — studio
      tests monkeypatching a mid-write crash assert no truncated file
      (`test_world_map.py::test_render_dump_svg_write_is_atomic`,
      `::test_embedded_svg_write_is_atomic`,
      `test_maps.py::test_companion_note_write_is_atomic`,
      `test_sites_job.py::test_site_params_write_is_atomic`; the world
      job's `world/options.yaml` write — same class, found while there —
      converted too)
- [x] A slug-colliding map job refuses with 409 naming the existing
      artifact — studio test
      (`test_sites_job.py::test_site_slug_collision_refuses_409`,
      `test_maps.py::test_city_slug_collision_refuses_409`,
      `test_maps.py::test_city_unidentifiable_destination_refuses_409`;
      ruling taken: refuse on a differing request, same request verbatim
      stays the regenerate-overwrite case)
- [x] Export refuses an unvalidated campaign arg and excludes
      `.obsidian/` — extends `test_export.py`
      (`test_export_refuses_an_unvalidated_campaign_argument`, the pin
      test, and the manifest exclusions test)
- [x] Two obsidian registrations leave both backups recoverable —
      extends `test_obsidian_vault.py`
      (`test_two_registrations_leave_both_generations_recoverable`,
      `test_backup_generations_prune_to_three`)
