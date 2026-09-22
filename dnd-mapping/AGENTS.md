# AGENTS.md — start here

A standalone D&D map pipeline. Worlds in seconds, cities on demand,
battlemaps per scene — no faction simulator, no game state, no LLM in the
loop. Point an agent at this directory and say "let's make a world."

**Full documentation: [`docs/map-pipeline.md`](docs/map-pipeline.md)** (the
authoritative guide: stub tables, build recipes, site generators, history).
Read it before generating anything.

## "Let's make a world"

```bash
# 1. Generate + import a world (~1 second, fully headless)
cd tools/Fantasy-Map-Generator
FMG_SEED=<pick a number> node node_modules/vitest/vitest.mjs run src/generators/headless-export.test.ts
cd ../.. && python src/import_fmg.py worlds/raw/headless-<seed>.json
# → worlds/candidates/world-<seed>.yaml: nations, settlements, cultures

# 2. Pick a burg from the yaml; a city map is scripts/gen-ruravaqan.mjs
#    with that burg's record + road bearings (both derivable from the dump).

# 3. Battlemap scenes wherever the story goes:
python src/battlemap.py gen-camp --seed <n> --name <site-name>   # or gen-dungeon / gen-cave
python src/battlemap.py render --dir worlds/sites/<site-name>
node scripts/render-scene-png.mjs worlds/sites/<site-name>
python src/battlemap.py wrap --dir worlds/sites/<site-name>
# → scene.png (any VTT) + <site-name>.uvtt (Foundry: walls/doors/lights live)
```

## What lives here

- **Tier 1 — worlds**: headless Fantasy Map Generator (tag 1.153.1). Full
  genesis pipeline in Node, ~1s per world. Raw dumps in `worlds/raw/`, terse
  geography summaries in `worlds/candidates/`.
- **Tier 2 — cities**: settlemaker 3.0.1 (external, GPL-3.0). Street/ward/
  building generation parameterized by a burg record from Tier 1. See
  `worlds/ruravaqan/` for a worked example.
- **Tier 3 — battlemaps**: `src/battlemap.py`. Scenes authored as data
  (extracted from real city footprints, or procedurally generated as
  dungeon / cave / camp), rendered grid-aligned (70 px per 5-ft square),
  wrapped in UVTT 0.3 for Foundry VTT.

## Ground rules (non-negotiable)

1. Every map is a derived artifact of a recorded seed + parameters. Never
   hand-edit generated outputs — fix the generator input and regenerate.
2. Lazy evaluation: generate only what the campaign actually touches. A
   thousand towns are one burg record each until someone visits.
3. Machine setup notes: no `npm`/`npx` (nodist shim broken — use `pnpm` or
   invoke binaries with `node` directly); if a project directory is ever
   moved, pnpm's absolute junctions break — run `CI=true pnpm install
   --offline` in `tools/settlemaker` to relink. Dependencies: Python 3.10 +
   PyYAML, Node 22.

## Directory map

```
worlds/raw/               FMG originals: .map saves, full/headless JSON dumps
worlds/candidates/        derived geography.yaml per world + index.md review
worlds/ruravaqan/         the city: settlemaker SVG/GeoJSON + params provenance
worlds/sites/             standalone scenes: the-undercroft, marsh-gang-camp, the-silver-mine
src/import_fmg.py         FMG exports -> geography.yaml
src/battlemap.py          scenes: extract/render/wrap + gen-dungeon/gen-cave/gen-camp
scripts/                  city generator, scene rasterizer
tools/                    FMG + settlemaker clones (external, GPL where noted)
docs/map-pipeline.md      THE documentation
```
