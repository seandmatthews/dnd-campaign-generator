# 60 — Regeneration determinism: verify the pins, record the options

**Status:** proposed
**Priority:** P1 — "everything regenerated from a recorded seed" is
unenforced in both of its halves (tool version and parameters)
**Area:** `dnd-mapping/vendor-patches/fmg/`,
`dnd-mapping/scripts/gen-city.mjs`, `dnd-mapping/src/battlemap.py`,
`setup-tools.py`, `studio/routers/maps.py`
**Found by:** nine-lane adversarial review, 2026-10-23 (mapping lane)

## Problem

The repo's claim (README: "everything regenerated from a recorded
seed"; `docs/map-pipeline.md:151`: "deterministic per seed + FMG
version") has two unenforced halves:

1. **Tool versions are literals, never verified.** The headless
   runner writes `version: "1.153.1-headless"` as a constant
   (`vendor-patches/fmg/headless-export.test.ts:113`); gen-city writes
   `settlemaker_version: 3.0.1` as a literal (`gen-city.mjs:296`);
   `fmg_done()`/`settle_done()`/`doctor.fmg_ok()` check marker FILES
   (`.git`, the runner file, `dist/index.js`, sharp's package.json),
   never the checkout's actual tag. A `git pull` in
   `dnd-mapping/tools/` changes every same-seed regeneration while
   every provenance record still claims the old version — and a
   future FMG that only changes default option values keeps the
   runner green while all seeds yield different worlds.
2. **Parameters are half-recorded and the one record is destroyed.**
   Steered world options (culture set, template, burgs/states limits,
   latitude, precipitation — the whole `_fmg_options_payload` surface)
   survive only inside `world/source.json`, which `force:true`
   regeneration OVERWRITES (`studio/routers/maps.py:523-545`);
   `geography.yaml` meta records only seed/fmg_version/culture_set
   and climate; the snapshot message records the seed, not the
   options (`:609`). Same seed + default form = a different world,
   silently replacing the genesis record. Dungeon provenance omits
   `--rooms` (`battlemap.py:327`).
3. **Vendor patches never re-applied.** `fmg_apply` runs only when
   `fmg_done()` is False (`setup-tools.py:120-138`); once the runner
   file EXISTS, content is never compared — a fix committed to
   `vendor-patches/` changes regeneration behavior only on fresh
   clones, so different machines regenerate different worlds from
   the same seed. The vendor-patches README's copy-back rule has no
   reverse enforcement.

## Proposal

1. Stamp verified provenance: read the tool version from the checkout
   at run time (`git describe --tags` in tools/fmg; the recorded
   package version as fallback) and write THAT into dump info /
   params.yaml. On regeneration, refuse or loudly flag when the live
   tool version differs from the campaign's recorded one.
2. `setup-tools.py --check` (and `doctor.fmg_ok`) verify the tag AND
   patch freshness — hash-compare the tracked vendor-patches runner
   against the installed copy.
3. World regen replays the record: preserve `source.json` (copy it
   aside as the genesis record before overwrite, or record the full
   resolved options in `geography.yaml`); regeneration defaults to
   replaying the recorded options; the snapshot message carries the
   seed plus an options fingerprint.
4. Dungeon provenance carries `--rooms`.

## Acceptance criteria

- [ ] Output stamp comes from the checkout, not a literal — direct-run
      test with a faked `git describe` (extend
      `dnd-mapping/tests/test_import_fmg.py` or new
      `test_provenance.py`)
- [ ] Version-mismatch regeneration refuses or flags loudly — studio
      test on the world regen path (extend `test_world_gen.py`)
- [ ] `--check` catches a stale patch (hash mismatch) —
      `setup-tools.py --check` probe in a test
- [ ] `source.json` survives a force regen — studio test extending
      `test_world_gen.py`
- [ ] Dungeon params/provenance carry rooms — assertion in
      `dnd-mapping/tests/test_battlemap.py`
