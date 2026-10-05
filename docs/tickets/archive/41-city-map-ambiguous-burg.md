# 41 — City map: duplicate burg names dead-end the flow

**Status:** completed (2026-10-05)
**Priority:** P3
**Area:** `studio/static/js/maps.js` (burg picker), `studio/jobs.py` / `dnd-mapping/src/import_fmg.py` (burg resolution)
**Found by:** live GM pass on a fresh campaign (seed 6161, highFantasy), 2026-10-04

## Problem

The Maps tab's burg picker lists burgs by display label
(`Stormberg — Sildgeria, pop 27,400 [port]`) but the city-map job submits
only the burg NAME. A generated world can contain two burgs with the same
name in different nations (seed 6161 has two Stormbergs, ids 2 and 266;
only one is large enough to make the top-15 picker). The job then fails
fast in `gen-city.mjs`:

    burg "Stormberg": ambiguous, 2 burgs share the name (ids 2, 266) — pass the index

Observed: `POST /api/campaign/emberhollow/maps/cities` → job error, and
after a page reload the Maps tab shows only the empty "No city maps yet —
map a burg below and it will appear here." The failure survives only as a
transient toast (gone in ~4 s) and in the job-history API; the tab has no
durable failed-jobs surface. The GM's only remedy is picking a different
burg — the error message's own advice ("pass the index") has no UI
expression.

The e2e suite cannot see this: `first_dm.spec.ts` picks
`selectOption({ index: 0 })` on a stub world where names are unique.

## Proposal

1. The burg picker submits the burg id (or nation+name pair), not the
   bare name; `gen-city` already supports disambiguation ("pass the
   index"). Keep the human-readable label as the option text.
2. A failed city-map (and battlemap) job leaves a persistent error line
   in the Cities/Battlemap sections of the Maps tab (what the job was,
   the error tail, a retry hint), cleared by the next successful run —
   same honesty contract as the queue strip, not a new job-history UI.

## Acceptance criteria

- [x] Mapping a burg whose name is ambiguous succeeds, targeting the
      picked nation's burg — pinned by a fast-tier test that submits two
      same-named burgs from different nations
- [x] The picker's submitted payload carries the burg id/index, not the
      display label — pinned by a `studio/tests/js/` test on the form
      serialization
- [x] A failed map job renders a persistent inline error in the Maps tab
      section it belongs to — pinned by a `studio/tests/js/` test

## Implementation notes (2026-10-05)

Landed as one change on `master` (ticket moved to `archive/` in the
same commit).

- **Id over nation+name pair** (the proposal's first alternative):
  `import_fmg.py` now writes `burg_id` (the dump's burg index `i`) into
  each `major_settlements` entry, the picker makes it the option VALUE
  (id-less burgs — hand-written fixtures — keep the name as the value),
  and gen-city natively resolves `--burg <index>`. The human label stays
  the option text; the attach-to-place default still follows the NAME.
- **The router resolves ids back to names** for everything derived:
  `post_city` turns a numeric `burg` into the geography.yaml settlement's
  name (422, pointing at the picker, when it does not resolve to exactly
  one), so the place default and the companion-note `burg:` provenance
  never become bare digits.
- **The persistent error line renders from the job record, not new
  state:** `GET /api/campaign/{name}/jobs` gained `last_finished`
  (newest finished job per kind — `jobs.last_finished()`), and the Maps
  tab shows the Cities/Battlemap section error whenever the kind's
  newest finished run has `status: error` (what failed, the error tail,
  a retry hint). A reload cannot wipe it; ANY later finished run of the
  kind replaces it — the proposal said "cleared by the next successful
  run", implemented as "replaced by the next finished run" so a stale
  failure never outlives newer truth (a cancelled run is a user act,
  not a failure to keep showing). The read is advisory: the jobs fetch
  failing never blocks the grids. Both map forms now refresh on failure
  too, so the line appears immediately, not only after a reload.
- **Honest lifetime:** `last_finished` shares the in-process job
  registry's lifetime (a server restart clears it) — the same contract
  as the queue strip the proposal pointed at.
- API.md moved with the code (cities POST contract, campaign-jobs
  response, Maps-tab behavior block).
- Pinned by `test_city_job_accepts_burg_id_when_names_duplicate` +
  `test_city_job_rejects_unresolvable_burg_id` (test_maps.py),
  `test_last_finished_carries_the_newest_job_per_kind`
  (test_job_queue.py), and the new
  `studio/tests/js/test_maps_burg_form.cjs` (registered as the
  studio-js-maps-burg-form suite).
