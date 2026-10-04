# dnd-mapping

A standalone D&D map pipeline: worlds → cities → battlemaps. No faction
simulator, no game state, no LLM in the loop — every artifact is derived
from a recorded seed + parameters, so anything can be regenerated.

Split out of the sibling `agentic-simulator` project on 2026-09-21 (all three
projects live under the common parent
`Documents/dnd-campaign-generator/`) so
the maps can be used on their own. That project consumes this one as an
**optional input** (a provenance block in its `world/state.yaml`); this
project never reads game state.

## The three tiers

| tier | question it answers | tool | speed |
|---|---|---|---|
| worlds | where is everything? | headless Fantasy Map Generator (tag 1.153.1) | ~1 s per world |
| cities | what does a town look like? | settlemaker 3.0.1 (external, GPL-3.0) | seconds per city |
| battlemaps | what does it look like where you're standing? | `src/battlemap.py` (scenes → SVG → PNG → UVTT) | seconds per scene |

Everything is headless — no browser needed for any of it. The full guide
(with the FMG stub table, browser build recipe, site generators, and the
history of decisions) is [`docs/map-pipeline.md`](docs/map-pipeline.md);
the command quickstart is [`AGENTS.md`](AGENTS.md).

## Status

- `worlds/candidates/` — TRACKED curated worlds under review in
  `index.md`; **Bonia** (seed 878636343) is the chosen one so far. Fresh
  import drops land in `campaign-data/scratch/map-candidates/`
  (git-ignored); promoting one is a copy here + `git add`.
- `worlds/ruravaqan/` — a 29,600-population port city generated from Bonia's
  own burg record, plus the harbour-quay battlemap scene.
- `campaign-data/maps/sites/` — procedural scenes (dungeon, cave, camp,
  mine), each with a Foundry-importable `.uvtt` (walls, doors, lights);
  regenerated on demand (scratch under the git-ignored campaign-data root,
  ticket 35). `tests/test_import_fmg.py` is the suite for the
  importer; run it via `python run-tests.py` at the repo root.

## City generation

`scripts/gen-city.mjs` generates ANY burg from a world dump — it derives
road bearings, ocean bearing, and port-ness from the dump itself:

```bash
node scripts/gen-city.mjs --world worlds/raw/candidate-03.map      --burg "Ruravaqan" --out worlds/ruravaqan --seed 636343
```

(The one-off `gen-ruravaqan.mjs` this replaces was removed; its output path
was stale anyway.)

## Known limits (documented in map-pipeline.md)

- Renders are schematic (flat fills + grid), not painted maps — good enough
  for VTT mechanics, plain to look at. A skin pass is a future layer.
- The custom FMG headless runner is NOT vanilla FMG: tracked copies of the
  files that make it work live in `vendor-patches/fmg/` (see its README).
- The importer reports sections it could not match in a drifted `.map`
  (`WARNING: sections not found`) instead of importing them as empty.
