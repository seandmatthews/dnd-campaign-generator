# FMG vendor patches

**Automated:** `python setup-tools.py` (from the repo root) performs the
fresh-clone steps below for you. The manual equivalent:



The `tools/` tree is git-ignored (re-obtainable per `docs/map-pipeline.md`),
but the Tier-1 headless pipeline depends on **custom files that exist only as
local modifications inside the FMG clone**. This directory is the tracked
copy of those files. If the working tree is lost, these restore Tier 1.

## Contents

| file | applies to | what it is |
|---|---|---|
| `headless-export.test.ts` | `src/generators/headless-export.test.ts` | the custom vitest entrypoint that runs FMG's generation pipeline headlessly and dumps `{info, settings, pack}` (the shape `src/import_fmg.py` parses). Env: `FMG_SEED`, `FMG_W`, `FMG_H`, `FMG_OUT`. |
| `package-json.patch` | `package.json` | adds `d3-quadtree` to dependencies (used by the pipeline in Node, not declared upstream at tag 1.153.1). |
| `npmrc` | `.npmrc` | `shamefully-hoist=true` — required so vitest/sharp resolve through hoisted node_modules. |
| `pnpm-workspace.yaml` | `pnpm-workspace.yaml` | allowBuilds acks pnpm wants on install. |

The `pnpm-lock.yaml` inside the clone is generated — do not vendor it.

## Re-applying after a fresh clone of FMG at tag 1.153.1

```bash
cd tools/Fantasy-Map-Generator          # fresh clone, checkout 1.153.1
git apply ../../vendor-patches/fmg/package-json.patch
cp ../../vendor-patches/fmg/npmrc .npmrc
cp ../../vendor-patches/fmg/pnpm-workspace.yaml pnpm-workspace.yaml
mkdir -p src/generators
cp ../../vendor-patches/fmg/headless-export.test.ts src/generators/
CI=true pnpm install --offline         # or plain `pnpm install` on a new machine
```

Then the Tier-1 incantation from `docs/map-pipeline.md` works again:
`FMG_SEED=<seed> FMG_OUT=../../worlds/raw node node_modules/vitest/vitest.mjs run src/generators/headless-export.test.ts`

**If you edit the headless runner or the dependency set inside the FMG
clone, copy the changed files back here in the same commit** — this README
and `docs/map-pipeline.md` are the only other places that know Tier 1 is
not vanilla FMG.
