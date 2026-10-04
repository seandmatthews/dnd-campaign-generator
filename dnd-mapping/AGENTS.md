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
# → campaign-data/scratch/map-candidates/world-<seed>.yaml (scratch, not
#   tracked): nations, settlements, cultures

# 2. Pick a burg from the yaml; its city map is one command (inputs are
#    derived from the dump — bearings, ocean side, port piers):
node scripts/gen-city.mjs --world worlds/raw/<dump> --burg "<name>" --out worlds/<city>

# 3. Battlemap scenes wherever the story goes:
python src/battlemap.py gen-camp --seed <n> --name <site-name>   # or gen-dungeon / gen-cave
python src/battlemap.py render --dir ../campaign-data/maps/sites/<site-name>
node scripts/render-scene-png.mjs ../campaign-data/maps/sites/<site-name>
python src/battlemap.py wrap --dir ../campaign-data/maps/sites/<site-name>
# → scene.png (any VTT) + <site-name>.uvtt (Foundry: walls/doors/lights live)
```

## What lives here

- **Tier 1 — worlds**: headless Fantasy Map Generator (tag 1.153.1). Full
  genesis pipeline in Node, ~1s per world. Raw dumps in `worlds/raw/`, terse
  geography summaries dropped in `campaign-data/scratch/map-candidates/`
  (scratch); curated ones are copied into the tracked `worlds/candidates/`
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
3. Machine setup notes: tool installs are plain `npm install` (setup-tools
   automates them; FMG's adds `--ignore-scripts` because npm runs scripts
   through cmd.exe, which cannot handle the `&` in this repo's default
   path). Engines invoke binaries as `node node_modules/<pkg>/...` directly
   — no global installs. Dependencies: Python 3.10 + PyYAML, Node 18+.

## Directory map

```
worlds/raw/               FMG originals: .map saves, full/headless JSON dumps
worlds/candidates/        TRACKED curated geography.yamls + index.md review
worlds/ruravaqan/         the city: settlemaker SVG/GeoJSON + params provenance
campaign-data/scratch/    run scratch: map-candidates/ (import drops),
                          gen-city-smoke/
campaign-data/maps/sites/ battlemap scenes (slugs): regenerated on demand —
                          canon copies live in the campaign repos
src/import_fmg.py         FMG exports -> geography.yaml
src/battlemap.py          scenes: extract/render/wrap + gen-dungeon/gen-cave/gen-camp
scripts/                  city generator, scene rasterizer
tools/                    FMG + settlemaker clones (external, GPL where noted)
docs/map-pipeline.md      THE documentation
```
