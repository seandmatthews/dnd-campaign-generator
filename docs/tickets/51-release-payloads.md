# 51 — Release payloads: split the builder, CI attaches GitHub releases

**Status:** proposed
**Priority:** P1 — the prerequisite for every friend-visible update path
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

- [ ] Stage → three payloads deterministically; an app payload laid over
      a full-exe install boots and serves (update path result == full
      install result, file tree compared) — builder smoke extension.
- [ ] Tag push produces a GitHub Release with the payload asset; an
      anonymous download of it succeeds (proves the public gate) —
      checked in the release job.
- [ ] The full-exe build is behaviorally unchanged — the existing build
      smoke gate (staged /tutorial 200, install + fast-path verify)
      passes on a rebuild.

## Implementation notes (YYYY-MM-DD)

_lands here when the status flips._
