# FMG vendor patches

**Automated:** `python setup-tools.py` (from the repo root) performs the
fresh-clone steps below for you. The manual equivalent:

The `tools/` tree is git-ignored (re-obtainable per `docs/map-pipeline.md`),
but the Tier-1 headless pipeline depends on **custom files that exist only as
local modifications inside the FMG clone**. This directory is the tracked
copy of those files. If the working tree is lost, these restore Tier 1.

These files modify [Fantasy Map Generator](https://github.com/Azgaar/Fantasy-Map-Generator)
— MIT, Copyright 2017–2024 Max Haniyeu (Azgaar). The clone that receives them
keeps its own LICENSE, and any release bundle built from it ships that license
and source intact.

## Contents

| file | applies to | what it is |
|---|---|---|
| `headless-export.test.ts` | `src/generators/headless-export.test.ts` | the custom vitest entrypoint that runs FMG's generation pipeline headlessly and dumps `{info, settings, pack}` (the shape `src/import_fmg.py` parses). Env: `FMG_SEED`, `FMG_W`, `FMG_H`, `FMG_OUT`, `FMG_OPTIONS` (JSON deep-merged over the randomized options — pin a culture set, heightmap template, climate or limits per world; see the runner header). |
| `package-json.patch` | `package.json` | adds `d3-quadtree` to dependencies (used by the pipeline in Node, not declared upstream at tag 1.153.1). |

Installs are plain `npm install` (npm ships with Node; upstream keeps a
package-lock.json, and npm's flat node_modules is what FMG's imports
expect). No pnpm: its symlinked store was the source of the
junction-breaks-when-moved fragility. `python setup-tools.py --check`
detects a pnpm-shaped tree (a `.pnpm` store) even when every marker file
is present, and setup removes it for a clean npm reinstall. The
`package-lock.json` inside the clone is generated — do not vendor it.

## Re-applying after a fresh clone of FMG at tag 1.153.1

```bash
cd tools/Fantasy-Map-Generator          # fresh clone, checkout 1.153.1
git apply ../../vendor-patches/fmg/package-json.patch
mkdir -p src/generators
cp ../../vendor-patches/fmg/headless-export.test.ts src/generators/
npm install --ignore-scripts --no-audit --no-fund
```

`--ignore-scripts` is load-bearing on machines whose path contains `&`
(this repo's did until the 2026-10-04 rename): npm runs install scripts
through cmd.exe, which cannot parse the ampersand. FMG's only install-time script is
`"prepare": simple-git-hooks` — dev-only git hooks nothing here needs;
esbuild/rollup ship their platform binaries as optional dependencies, so
the headless pipeline is script-free by construction.

Then the Tier-1 incantation from `docs/map-pipeline.md` works again:
`FMG_SEED=<seed> FMG_OUT=../../worlds/raw node node_modules/vitest/vitest.mjs run src/generators/headless-export.test.ts`

**If you edit the headless runner or the dependency set inside the FMG
clone, copy the changed files back here in the same commit** — this README
and `docs/map-pipeline.md` are the only other places that know Tier 1 is
not vanilla FMG.
