# 54 — Scenes publish button on the table-setup card

**Status:** in-progress (endpoint + card section + tests live since
2026-10-20; the publish half re-proven 2026-10-24 —
the-drowned-crypt-v2, 6 doors; the manual check's Atlas half joins
the shared sitting with 46/49/53/59)
**Priority:** P2 — the second half of ticket 53's card; unblocked since
companion ticket 05 (the scenes publisher) landed 2026-10-12
**Area:** `studio/routers/table.py`, `studio/static/js/world.js`
**Found by:** ticket 53's implementation, 2026-10-17 — the amendment
opening the door ("fold the scenes publish into this ticket at
implementation time while it still fits one sitting, else mint the
follow-up") ran out of sitting.

## Problem

The table-setup card installs the ICRPG preset but cannot publish a
battlemap scene — the actual map the table looks at. The companion's
scenes publisher exists (uvttToScene port, fed-or-refused config, the
`_inbox/` hand-off whenever it can't confirm the installed Atlas version
against its pin), but its only studio-facing route is ATLAS.md's manual
steps. The seam's mechanism has one button and deserves two.

## Proposal

The same endpoint pattern as `publish-presets`:

1. `POST /api/campaign/{name}/table-setup/publish-scenes`
   `{"site"?: str}` → spawns `publish.py <campaign> scenes
   [--site <slug>]` under the studio's Python — list-form args, explicit
   cwd, never a shell, campaign lock, the campaign dir server-resolved
   only, 120s cap. Refusals and the `_inbox/` degrade note come back
   `ok:false`/stdout verbatim; exit 0 snapshots the campaign.
2. The card gains a scenes section: a site picker (the slugs under the
   campaign's `maps/sites/`), a publish button, and the publisher's
   output rendered verbatim — including the `_inbox/` note, which is the
   designed pin-mismatch behavior, not an error.

## Acceptance criteria

- [x] Spawn list-form/no-shell/resolved-dir — studio tests mirroring
      `test_table_setup.py`
- [x] Scenes refusal and `_inbox/` degrade text rendered verbatim, no
      snapshot on refusal — studio tests
- [ ] Manual once on a real campaign: publish a scene, Atlas shows it;
      the pin-mismatch path renders the `_inbox/` note — shares the
      campaign rebuild with tickets 46/49/53's pending live verifies

## Implementation notes (2026-10-20)

- **Endpoint** (`routers/table.py`): the spawn/lock/timeout/snapshot
  shape both buttons share was lifted into `_spawn_publisher` —
  `post_publish_presets` now calls it too, behavior-identical (its 8
  existing tests pin the contract and stayed untouched and green). The
  scenes site travels as ONE joined `--site=<slug>` argument, so a
  crafted value starting with `-` arrives as the slug, never as a flag
  of its own (`--site=--force`); an unknown slug is the publisher's own
  verbatim refusal — no second validation layer here. The bundled-
  companion 409 names the drag-it-by-hand manual path.
- **Card** (`world.js` `tableCard()`): a site picker + **Publish site
  scene** button beside the preset button, sharing the one verbatim
  `<pre>`. The picker is fed by the existing `GET /maps` (slug + human
  label; cities have no `.uvtt`, sites only). The button gates on BOTH
  answers — companion found AND a site to publish — with honest titles
  for each missing half. Success with `_inbox/` in the publisher's
  stdout toasts the degrade as the designed behavior ("drag it into
  Atlas by hand"), not an error.
- **Tests:** `studio/tests/test_table_scenes.py` (7) — the spawn
  contract, no-site publishes every site, the option-smuggling pin, the
  verbatim refusal with no snapshot, honest 504/409/404-before-spawn.
  `test_world_tab.cjs` gained 4 DOM cases (picker populated, the two
  gate states, the click's POST body shape) — minidom grew `<select>`
  `options`/`value` support for that, gated on the tag name so
  textarea/input `.value` semantics are untouched (the ungated version
  broke three suites; the gate is the lesson).
- **Docs:** ATLAS.md Steps 3 and 4 — the manual twin now names the
  card's two buttons as the automated path, itself still the
  always-true one.
- **Still open:** the manual check (acceptance 3) — joins the shared
  campaign sitting with 46/49/53 on `ember-coast`.

## Live verify (2026-10-20, ember-coast, dev studio on :8766)

- **The no-config refusal renders verbatim**: with no `avtt.yaml` the
  button showed the publisher's fed-or-refused text including its
  create-it-with-at-least instructions, toast fired, nothing written.
- **Then the full publish ran** (acceptance 3, studio half): after
  feeding `avtt.yaml` (`collection: ember-coast`, the documented
  five-foot grid) the button published the scene —
  `atlas-vtt/collections/ember-coast/scenes/the-drowned-crypt.atlasmap`,
  174 walls, 1 door, 4 lights, "left out: 0 outside, 0 unlits, 5
  windows" (the open portals — designed), success toast, snapshot
  `265b676`. No `_inbox/` degrade appeared because companion v0.2.0's
  pin (Atlas 0.7.1) now matches the vault — the degrade path stays
  covered by its studio tests, not reachable live here.
- **The refusal that cracked the core open**: the first publish attempt
  was refused — "The map image is too small for its grid: 2940 × 1960
  pixels for 2940 × 1960 squares." The message is a faithful port of
  Atlas 0.7.1's own importer, and chasing it found TWO core bugs,
  fixed same day (obvious-fix ruling, one sitting):
  1. **The uvtt dialect** — `dnd-mapping/src/battlemap.py` wrote
     `map_size` in PIXELS with `pixels_per_grid` beside it
     (Dungeondraft's convention); Atlas derives one cell as
     imageWidth / mapSize.x, so every generated map read as a
     1-pixel-per-cell grid and was refused on import — by the
     companion AND by Atlas itself. The exporter now writes map_size
     and all coordinates in cells, `pixels_per_grid` dropped.
  2. **Transposed walls** — `_cell_walls` grouped horizontal edge
     runs by the cell's X while the consumer read the group key as Y:
     horizontal walls were laid out transposed and half off-canvas
     (rings at row 39 of a 28-row grid), never bounding their rooms —
     in the PNG's wall ink and in every uvtt, since the generator
     landed. Fixed to group by the fixed axis; regression-pinned by
     walls-hug-the-floor-cells in `test_battlemap.py`.
  The Drowned Crypt artifacts were regenerated (seed 2060015, 7 rooms
  — floors/doors/lights byte-identical to the original, walls now
  correct) into the campaign site dir and the engine-side copy; the
  card then published the scene above. Ticket 46's walkthrough notes
  claiming the old file "passed the full 0.7.1 validation chain" were
  wrong on the size math — that simulation never ran it.
- **Remaining for completed (human hands):** in Atlas, the
  ember-coast collection shows the scene on the table — walls, the
  one door, lights; drag-and-drop import not needed anymore (the
  publisher's full path landed it), just open the collection.
