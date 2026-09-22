# dnd-mapping

A standalone D&D map pipeline: worlds → cities → battlemaps. No faction
simulator, no game state, no LLM in the loop — every artifact is derived
from a recorded seed + parameters, so anything can be regenerated.

Split out of the sibling `agentic-simulator` project on 2026-09-21 (all three
projects now live under the common parent `Documents/D&D/`) so
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

- `worlds/candidates/` — five generated worlds, index.md review; **Bonia**
  (seed 878636343) is the chosen one so far.
- `worlds/ruravaqan/` — a 29,600-population port city generated from Bonia's
  own burg record, plus the harbour-quay battlemap scene.
- `worlds/sites/` — three procedural scenes (dungeon, camp, mine), each with
  a Foundry-importable `.uvtt` (walls, doors, lights).

## Known limits (documented in map-pipeline.md)

- Renders are schematic (flat fills + grid), not painted maps — good enough
  for VTT mechanics, plain to look at. A skin pass is a future layer.
- Batch town generation (hundreds of burgs → hundreds of city maps) is
  designed but not built; `scripts/gen-ruravaqan.mjs` is the single-city
  pattern to scale.
