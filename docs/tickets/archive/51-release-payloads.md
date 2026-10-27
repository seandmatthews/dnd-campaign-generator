# 51 — Release payloads: split the builder, CI attaches GitHub releases

**Status:** completed (implemented 2026-10-26 on ticket/51-release-payloads,
38e8003 + review fixes f783c18, merged fa5299d; the full build ran green
through all six verification passes, the second time after the review's
hermeticity fix; the v0.1.0 tag push ran the release workflow green
end to end — run 40427302597, every step including the anonymous-
download proof — and an independent token-free download from the dev
machine hash-matched the release body)
**Area:** `release/build_release.py`, `.github/workflows/` (new release
job), repo visibility (gate)
**Found by:** the distribution design, 2026-10-12
(`docs/2026-10-12-distribution-design.md` §1–§2): the 155.7MB exe
re-ships ~150MB of unchanged bytes per iteration because the installer
and the payload are the same artifact. Measured on disk: the churny app
source is ~5–10MB zipped; Python (~11MB), node (~30MB), and
`dnd-mapping/tools/` (FMG 463MB + settlemaker 216MB installed) change a
few times a year.

## Problem

`release/build_release.py` builds exactly one artifact: the full
self-extracting exe. Every code change costs a ~20-minute local rebuild
and a manual 155MB re-delivery to the one friend, to ship usually under
a megabyte of real change. The builder already downloads the runtimes
from python.org/nodejs.org into its cache — the material for splitting
what changes from what doesn't is all in one script; it is just never
separated.

## Proposal

Per the design doc §2:

1. One stage, three payload targets, built by `build_release.py`:
   - **runtime pack** — Python embeddable + node (ships once);
   - **tools pack** — `dnd-mapping/tools/` with node_modules (ships
     once; rev-bumps only with vendor-patches changes);
   - **app payload** — studio + agentic-simulator + dnd-mapping source +
     docs + launch scripts + the pinned companion snapshot (core
     ticket 53 supplies that piece).
2. **GATE — the repo goes public** (ruling 2026-10-12). Before the
   flip: the audit-for-public pass over docs and history for anything
   not meant for the world (llm.env and campaign-data are gitignored;
   the rejected `.maps` were pruned 2026-10-04; a human eyeball is the
   remaining step). Public is what makes `releases/latest` work without
   a token in the friend's install (ticket 52) and stops private-repo
   Actions-minute billing on release builds.
3. CI release job: on a `v*` tag push, build the app payload (and the
   tools pack when the tag says tools changed), attach to the GitHub
   Release with a SHA256 in the release body. The full exe stays a
   local builder flag — the offline fallback and first-install vehicle,
   never deleted (design doc §2).

## Acceptance criteria

- [x] Stage → three payloads deterministically; an app payload laid over
      a full-exe install boots and serves (update path result == full
      install result, file tree compared) — builder smoke extension:
      the payload-set pass (each archive exactly its plan; payload.7z
      == the union, 22,312 files) + `verify_app_over_full` (172 app
      files byte-identical; boot 200s), green on both build runs —
      pinned by `studio/tests/test_payload_plan.py` at the seam
- [x] Tag push produces a GitHub Release with the payload asset; an
      anonymous download of it succeeds (proves the public gate) —
      checked in the release job: run 40427302597 (v0.1.0) green
      through "Anonymous download proof (the public gate)", and an
      independent token-free `urllib` download from the dev machine
      matched the body's `sha256:` line
- [x] The full-exe build is behaviorally unchanged — the existing build
      smoke gate (staged /tutorial 200, install + fast-path verify)
      passes on a rebuild — plus all three ticket-57 update legs, on
      both green runs

## Implementation notes (2026-10-26)

Landed on `ticket/51-release-payloads` (38e8003, review fixes f783c18).

- **The split:** `build_release.py` packs ONE stage three ways — the
  full exe as always, plus `CampaignStudio-app-<stamp>.7z` (4.0 MB
  measured), `CampaignStudio-runtime-py3.11.9-node-v24.22.1.7z`
  (33.6 MB), `CampaignStudio-tools-fmg-1.153.1-d5cf359-sharp-0.33.5.7z`
  (110.8 MB) into `release/dist/`. Ownership lives in one pure
  mapping (`payload_plan`/`stage_files`) that both the pack step and
  the post-pack self-check read, so the packed sets cannot disagree
  with the plan; `studio/tests/test_payload_plan.py` pins the seam
  (disjoint/total, prefix traps — `dnd-mapping/toolbox` and deep
  `runtime`/`tools` dirs stay app — walker/strip agreement).
- **The tools rev is derived** from `setup-tools.py`'s pins
  (`FMG_TAG`/`SETTLE_REV`/`SHARP_PIN`), never restated — the
  ticket-53 stamp lesson. A pin move that outlives the parser fails
  the build instead of shipping a stale name.
- **The determinism gate is enforced at build time:** every full build
  lists each archive against the plan (exact), asserts the three
  disjoint, and asserts `payload.7z` == union (22,312 files on the
  green run). `-x!` exclusion semantics verified empirically to be
  exact-path (a deep `studio/runtime/` stays app).
- **The equivalence gate (acceptance 1):** `verify_app_over_full`
  installs via the real `install.cmd`, extracts the app payload FRESH
  (must byte-equal the installed app surface — 172 files), lays it
  over the real install, and boots `/`, `/tutorial`, `/atlas` → 200
  (port 8797). Hermeticity: the two REAL boot write classes are
  excluded by name — `campaign-data/logs/` (launch.cmd points
  CAMPAIGN_DATA_ROOT at the data root; that is where server.py logs,
  not the dead `studio/logs` the first draft cleaned) and
  `__pycache__`/`*.pyc` (mirroring the plan's strip rules).
- **`--app-payload-only`** is the CI mode: no runtimes, no tools, no
  exe, NEVER a key (refuses `--campaigns`/`--no-key`/`--sfx-only`;
  structural smoke instead of the boot smoke; `ensure_cache(full=)`
  trims the downloads to 7zr).
- **Deviations from the proposal:** (a) the CI job also verifies the
  tag names the pyproject version (fail-loud on a careless re-tag);
  (b) the job's re-run path REPLACEs — refreshes the body and deletes
  stale `CampaignStudio-app-*` assets — because asset names carry the
  build stamp and a plain `--clobber` would accumulate two payloads
  under one body; (c) the anonymous proof selects the built asset BY
  NAME, never `assets[0]` (API order); (d) `release/.gitignore` gained
  `*.log` — a stray build log carried the key tail (review round 1).
- **Review round 1 (2026-10-26, staunch lane):** 1 P1 + 3 P2 + 2 P3,
  ALL fixed in f783c18 — the P1 (re-run corrupts the release while the
  hash proof stays green), the `dist/` vs `release/dist/` path (every
  job would have failed), the non-hermetic equivalence compare
  (passed on a timing race), the timeout leak of the detached
  launch.cmd chain (kills moved into the `finally`), the locale-decode
  crash in the 7z listing (bytes + errors=replace now), and the log
  leak. Clean areas explicitly confirmed: plan/7z semantics, the -slt
  state machine, `--app-payload-only` flag combos, pack-to-verify
  TOCTOU, the tools-less warning path.
- **Open box (acceptance 2):** the release workflow has never
  executed — its first live run IS the `v0.1.0` tag push performed
  right after this merge; green job + anonymous no-token download with
  the body hash matching ticks it from real evidence.

## Supersession review (2026-10-26)

Checked per user ruling — **not superseded**; stays parked-proposed.
Nothing has taken the work over: the builder is still single-artifact
(`build_release.py` has no payload staging — only `--sfx-only`,
`--no-key`, `--no-smoke`, `--campaigns`) and `.github/workflows/`
carries no release job. 57 hardened the exe-update contract (llm.env
preservation, frankeninstall guard, versioned filename) and is
compatible, not displacing — this ticket keeps the full exe as the
fallback exactly as the 2026-10-13 deployment ruling requires. The
real blocker is the built-in GATE: the public-repo flip (and 52's
unauthenticated `releases/latest`) waits on the key-custody ruling —
prompted key ⇒ public repo; bundled key ⇒ Discord-only artifacts.
Implementing this ticket starts with that ruling.
