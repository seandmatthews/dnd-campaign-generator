# 60 — Regeneration determinism: verify the pins, record the options

**Status:** completed (implemented 2026-10-23 by the ticket-sweep lane,
e3c637b, reviewed round 1; merged by the sweep itself as 025462f with
green main gates)
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

- [x] Output stamp comes from the checkout, not a literal — direct-run
      test with a faked `git describe` (extend
      `dnd-mapping/tests/test_import_fmg.py` or new
      `test_provenance.py`) — `dnd-mapping/tests/test_provenance.py`
      (new suite, wired into run-tests.py): a faked `git describe --tags`
      (argv + cwd intercepted where the resolver calls it) becomes the
      stamp verbatim; fallback/outer-repo-guard/None paths beside it,
      plus source pins that the TS runner and gen-city.mjs literals are
      gone. The TS runner itself executes only in the integration tier
      (fast tier has no FMG clone).
- [x] Version-mismatch regeneration refuses or flags loudly — studio
      test on the world regen path (extend `test_world_gen.py`) —
      `studio/tests/test_world_gen.py::test_world_force_regen_refuses_on_fmg_version_mismatch`:
      409 naming both versions; matching version (the runner's
      `-headless` suffix aside) passes; either side unknown degrades to a
      pass, never a false refusal.
- [x] `--check` catches a stale patch (hash mismatch) —
      `setup-tools.py --check` probe in a test —
      `studio/tests/test_setup_tools.py::test_check_catches_a_stale_patch`
      (drives the real `main(["--check"])` loop; tag drift and the
      setup-mode runner re-copy pinned beside it).
- [x] `source.json` survives a force regen — studio test extending
      `test_world_gen.py` —
      `::test_world_force_regen_preserves_the_genesis_record`: the old
      dump lands byte-identical under `world/genesis/source-<seed>.json`,
      `world_source` still resolves to the live dump, and the steering
      record + fingerprint land beside the geography.
- [x] Dungeon params/provenance carry rooms — assertion in
      `dnd-mapping/tests/test_battlemap.py` —
      `::test_dungeon_provenance_carries_rooms` (`rooms=N` in the
      gen-dungeon provenance string).

## Implementation notes (2026-10-23)

- **Version stamps resolve at run time, three ways.** The vendored runner
  stamps `info.version` from `git describe --tags` (spawnSync argv, no
  shell) with the clone's `package.json` version as fallback, `-headless`
  appended as before; gen-city.mjs reads `settlemaker_version` from
  `tools/settlemaker/package.json`; Python twins of the resolver live in
  `setup-tools.py:_checkout_version` and `studio/engines.py:
  fmg_checkout_version` (setup-tools must stay stdlib-standalone, so the
  twins are synced by hand — both say so). Both twins run describe only
  when the tree holds `.git`: git walks parent dirs for a repository, and
  a tmp cwd inside an outer repo must not leak its tags in as a fake
  answer.
- **The regen gate refuses at submit time** (409 naming both versions and
  the repin command), comparing `geography.yaml meta.fmg_version`
  (stripped of `-headless`) against the live checkout. Either side
  unknown — pre-ticket-60 campaigns, no clone, no git — passes: never a
  false refusal, and a missing clone fails loudly in the engine run
  anyway. The gate is submit-time only, not re-checked under the campaign
  lock: a mismatch appearing mid-queue means the checkout changed under a
  queued job, and the job's own version log line names the live checkout.
- **Replay semantics:** a forced regen whose body omits `options`
  entirely (what the Maps tab sends when the form is empty) replays the
  recorded steering from the new `world/options.yaml`; explicit options —
  `{}` included — re-steer deliberately and become the new record. This
  deliberately changed `test_world_job_carries_fmg_options`'s old
  "nothing set → no FMG_OPTIONS" claim (that case now replays); the
  fresh-record variant of the old claim moved to the genesis test.
- **Genesis preservation** copies the old dump to
  `world/genesis/source-<seed>.json` (suffixing `-2`, `-3` on same-seed
  regens) — never a `source.*` sibling, because `spine.world_source`
  globs `source.*` and must keep resolving to the LIVE dump. The canon
  message now reads `generate world (seed N, options <8-hex sha256>)` —
  the fingerprint of the resolved `FMG_OPTIONS` payload, not the raw
  form.
- **`--check`/setup/doctor:** `--check` fails on tag drift or a stale
  runner (sha256 mismatch vs the tracked vendor copy) with the fix
  command; plain setup re-copies a stale runner (offline-safe, our own
  file) and advises on tag re-pins (network — never attempted).
  `doctor.fmg_ok` additionally hash-checks the runner and names the live
  checkout version in its detail. The vendor-patches README documents the
  now-enforced reverse direction of the copy-back rule.
- **Not exercised in the fast tier:** the TS runner's own describe call
  and gen-city's version read execute only on tool-equipped machines
  (integration tier / live use); the fast tier pins them at the source
  and behaviorally tests the identical Python resolution.
