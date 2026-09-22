# The Map Pipeline — how maps work in this project

*Last updated 2026-09-21. This is THE map documentation — everything else
about maps was consolidated into it. Verify commands by running them; the
gotchas below were all learned the hard way.*

## On-ramp: three things an agent can be asked to do

```bash
# 1. Make a world (~1 second):
cd tools/Fantasy-Map-Generator
FMG_SEED=12345 node node_modules/vitest/vitest.mjs run src/generators/headless-export.test.ts
cd ../.. && python src/import_fmg.py worlds/raw/headless-12345.json

# 2. Make a city for a burg from that world (~seconds):
#    copy scripts/gen-ruravaqan.mjs, swap in the burg's record + road bearings
#    (both derivable from the world JSON — see Tier 2), run it.

# 3. Make a battlemap scene:
python src/battlemap.py extract                        # city-anchored (harbour quay)
python src/battlemap.py gen-camp --seed 1 --name camp  # or gen-dungeon / gen-cave
python src/battlemap.py render --dir worlds/sites/camp
node scripts/render-scene-png.mjs worlds/sites/camp
python src/battlemap.py wrap --dir worlds/sites/camp
# → scene.png (drag into any VTT) + <name>.uvtt (Foundry: walls/doors/lights live)
```

## The mental model

```
FMG (world seed)          "where is everything"        worlds/raw/*.json
   ↓  import_fmg.py
geography.yaml            "what is where, tersely"     worlds/candidates/*.yaml
   ↓  settlemaker (towns)  /  battlemap.py gen-* (dungeon, cave, camp)
city / site maps          "what does it look like"     worlds/ruravaqan|sites/
   ↓  battlemap.py render + render-scene-png.mjs + wrap
battlemaps (PNG + UVTT)   "where you are standing"     .../scenes|sites/<name>/
```

Prime directive: **every map is a derived artifact** — regenerable from a
recorded seed + parameters, never hand-edited. Provenance lives beside every
output (`params.yaml`, `scene.json` provenance blocks). If you edit an SVG by
hand, you have broken the pipeline; fix the generator parameters instead.

You do NOT battlemap a whole city: a 1.5 km city at 70px per 5-foot square
is a 200,000px-wide image. City maps are context ("where are we"); battlemaps
are scenes ("where you're standing"). Lazy evaluation, applied to maps: the
world exists whole; towns and sites materialize when the campaign touches
them, at a cost of seconds each.

---

## Tier 1 — worlds (Azgaar FMG, headless)

Repo: `tools/Fantasy-Map-Generator` (tag 1.153.1, MIT). **You do not need the
browser or Playwright to generate worlds.** The whole generation pipeline is
importable Node code; `src/generators/headless-export.test.ts` (a vitest file
that is really a CLI) runs it and dumps JSON.

### Headless generation (preferred)

```bash
cd tools/Fantasy-Map-Generator
FMG_SEED=12345 FMG_W=1280 FMG_H=720 \
  node node_modules/vitest/vitest.mjs run src/generators/headless-export.test.ts
# → worlds/raw/headless-12345.json in ~1 second
cd ../.. && python src/import_fmg.py worlds/raw/headless-12345.json
```

How it works (so you can fix it when upstream changes): the runner bypasses
`components/lifecycle.ts` — its imports drag in dialogs and UI tabs — and
replicates the five essential lines of the app's `generate()`:
`Options.setGraphSize`, `options.map.seed = seed`, `Math.random = aleaPRNG(seed)`,
`Options.randomize()`, `await GenerationPipeline.run({})`. The pipeline is
declared in `src/generators/generation-pipeline.ts` (grid → heightmap → … →
journeys); the step modules are globals attached by side-effect imports of
`@/generators`.

The Node stubs the runner needs, and why (if upstream refactors, these are
the things that break):

| stub | because |
|---|---|
| eval `public/libs/alea.min.js` **with `globalThis.aleaPRNG = aleaPRNG` appended** | it declares `const aleaPRNG` — a const in eval code vanishes when the eval scope ends |
| eval `public/libs/flatqueue.js`, `polylabel.min.js`, `simplify.js` | classic-script globals the generators call (FlatQueue in cultures expansion) |
| `location = { href: "http://localhost/" }` | `Options.randomize()` reads `?options=` URL params through Pins |
| `localStorage` no-op | Pins reads pinned options; none in Node → everything re-rolls per seed |
| empty `grid` / `pack` / `mapName` globals | the pipeline assigns them wholesale |
| import `@/generators` | side effects: every generator module attaches its global (`Grid`, `Burgs`, `States`, …) |

Do NOT use `npx`/`npm` on this machine — the nodist shim is broken. Use
`node node_modules/vitest/vitest.mjs …` and `pnpm`.

If this directory is ever moved or renamed, every pnpm junction in
`tools/*/node_modules` breaks (Windows junctions store absolute paths).
Relink from the local store, per tool: `CI=true pnpm install --offline`.

### Browser generation (only when you need the app)

Interactive editing, `.map` saves, rendered-layer exports — things the app
does and the pipeline doesn't. **Build recipe** (once):

```bash
cd tools/Fantasy-Map-Generator
git fetch --depth 1 origin tag 1.153.1 && git checkout 1.153.1
ELECTRON_SKIP_BINARY_DOWNLOAD=1 pnpm install
echo "shamefully-hoist=true" > .npmrc && ELECTRON_SKIP_BINARY_DOWNLOAD=1 pnpm install
pnpm add d3-quadtree                      # the tag imports it via npm hoisting; pnpm hides it
NETLIFY=true node node_modules/vite/bin/vite.js build   # NETLIFY=true => base "/" for localhost
python -m http.server 8642 --directory dist
```

Browser-automation quirks (only relevant when driving the UI): prefer DOM-id
clicks via `evaluate` over Playwright locators (actionability flakes on FMG's
custom dialogs); a hidden jQuery-UI `#alert` modal can block clicks after
generation — dismiss it (Cancel); downloads land in `~/Downloads`, sometimes
as GUID `.tmp` files (verify by content); the heightmap-template dropdown
populates lazily; typed seeds don't take via synthetic events — FMG
re-randomizes, so read the true seed from the saved file.

### Which to use when

- Batch candidate worlds, reproducible seeds, agent loops → **headless**.
- "What does it look like," manual edits, exports of rendered layers → browser.
- Note: a headless world with the same seed as a browser world is NOT
  identical (options randomization paths differ) — a headless seed is its own
  world, deterministic per seed + FMG version.

### Importing

`python src/import_fmg.py <file.json|file.map>` / `--all`. Handles: FMG's
full JSON export, `.map` saves (line-oriented: header | settings JSON |
nameBases | embedded SVG | positional sections — parsed by content signature:
states contain `"salesTax"`, burgs `"port":`, provinces `"burg":`, cultures
`"base":`), and headless dumps (same shape as full export). FMG lists open
with a literal `0` placeholder at index 0 — filter non-dicts. Units: city
GeoJSON coordinates are **settlement units, not meters** —
`metadata.scale.meters_per_unit` (≈10.76 for our 29.6k city).

---

## Tier 2 — settlements (settlemaker)

Repo: `tools/settlemaker` (v3.0.1, GPL-3.0 — external tool; never import its
code into `src/`). Built with `pnpm install` + `node
node_modules/typescript/bin/tsc` → `dist/index.js`.

`scripts/gen-ruravaqan.mjs` is the template: import `generateSettlement` from
the built dist, pass a burg record + seed, get `{svg, geojson, model, kind,
degradedFlags}`. Ruravaqan's inputs all come from real data: FMG's own burg
record (population, port/walls/plaza/temple), road bearings computed from
neighboring burgs' coordinates, `oceanBearing` (assumed — the one
non-provenance-backed input), seed derived from the world seed.

Knobs that matter:

- `harbourSize: 'large'` — WITHOUT it a `port: true` city gets **no piers**.
- `engine: 'city'` for anything above village scale (auto switches at 1,000).
- Culture sets / name languages come from the world's cultures, if you care.
- `degradedFlags` tells you which requests were dropped (walls on too-small
  terrain, etc.).

Coordinates: city GeoJSON is `local_origin_y_down` in settlement units; the
SVG renders y-down as well. Buildings are BIG at this scale — a harbour
warehouse is ~50 m across. Plan scenes accordingly.

## Tier 3 — battlemaps

`src/battlemap.py` (subcommands `extract` / `render` / `wrap`) + `scripts/
render-scene-png.mjs`. Full chain for the pilot scene:

```bash
python src/battlemap.py extract            # GeoJSON footprints -> scene.json
python src/battlemap.py render             # scene.json -> scene.svg
node scripts/render-scene-png.mjs harbour-quay   # scene.svg -> scene.png (sharp)
python src/battlemap.py wrap               # scene.json + png -> .uvtt
```

- **extract** picks real building footprints from the GeoJSON (harbour ward,
  southernmost first) and frames a *strip*: warehouse walls cropped along the
  top edge, ~14 m of cobble quay, water, one pier (artistic — piers exist in
  the SVG but not the GeoJSON), crates/bollards as seeded dressing. A scene
  is 46×30 grid squares (70 m × 46 m); whole buildings don't fit and
  shouldn't.
- **render** emits an SVG at exact pixel size (70 px per 1.524 m square,
  faint grid baked in). Deliberately schematic/flat — the pretty-skin problem
  is unsolved (settlemaker skins or a Dungeondraft paint-over are the options).
- **rasterize with sharp, not browser screenshots** — the in-app browser
  rendered fixed-size SVGs at inconsistent zoom/tiling; sharp (in
  settlemaker's node_modules) is deterministic.
- **wrap** assembles UVTT format 0.3: base64 image, `line_of_sight` wall
  rings from building polygons, `portals` from doors. Foundry imports this
  natively (walls/doors/fog work); Owlbear Rodeo takes the plain PNG and you
  draw fog by hand.

Adding a new city-anchored scene = new extract function following `extract`'s
shape (the manor, the Reliquary storerooms are candidates). Scene files are
data: an agent can author one from a prose description, and a human can
hand-fix the JSON.

### Sites: dungeons, caves, camps (no city needed)

Not every scene anchors to a city. `battlemap.py` has geometry generators
that feed the same scene schema, so render/rasterize/wrap work unchanged:

```bash
python src/battlemap.py gen-dungeon --seed S --name the-undercroft [--rooms 7]
python src/battlemap.py gen-cave --seed S --name the-silver-mine
python src/battlemap.py gen-camp --seed S --name marsh-gang-camp
# then render -> rasterize -> wrap with --dir worlds/sites/<name>
```

- **gen-dungeon** — non-overlapping rooms joined by L-corridors; walls are
  merged cell-edge runs (156 wall loops in the pilot), doors at room
  entrances (some closed), torch lights per room.
- **gen-cave / mines** — cellular automata (44% fill, 4 smoothing passes,
  largest cavity kept); the boundary becomes jagged rock wall rings. Mines
  would layer structured galleries on the same primitive.
- **gen-camp** — open-air: tents ringed around a campfire, palisade arcs
  (partial walls with a gate gap), marsh pools, watchfire lights, loot.
  Lights render as radial glows AND land in the UVTT `lights` array with the
  scene's `ambient` color — the wrapper is lighting-aware.

Site outputs live in `worlds/sites/<name>/`: scene.json (with
provenance: generator + seed), scene.svg, scene.png, `<name>.uvtt`.

---

## Directory map

```
worlds/
  raw/                      FMG originals, never edited: *.map, *-full.json, headless-*.json
  candidates/               derived geography.yaml per world + index.md (chosen: Bonia)
  ruravaqan/                the city: ruravaqan.svg/.geojson, params.yaml, preview.png
    scenes/harbour-quay/    scene.json, scene.svg, scene.png, harbour-quay.uvtt
  sites/                    generated sites (no city anchor): the-undercroft,
                            marsh-gang-camp, the-silver-mine — same 4-file layout each
tools/
  Fantasy-Map-Generator/    FMG clone (tag 1.153.1) + our headless-export runner
  settlemaker/              settlemaker clone (v3.0.1)
scripts/
  gen-ruravaqan.mjs         Tier 2 generator
  render-scene-png.mjs      Tier 3 rasterizer (sharp)
src/
  import_fmg.py             Tier 1 -> geography.yaml
  battlemap.py              Tier 3: extract/render/wrap + gen-dungeon/gen-cave/gen-camp
```

## History

The superseded research and guides (mapping-software-deep-dive.md,
fmg-agent-guide.md) were consolidated into this file and removed on
2026-09-21. Their still-true conclusions: UVTT is the interchange standard;
settlemaker and TownGeneratorOS are GPL-3.0 (external tools only, never
import into `src/`); Azgaar FMG is MIT; WotC's Sigil VTT shut down
2026-10-31 (platform-risk lesson: keep artifacts in files we own).
