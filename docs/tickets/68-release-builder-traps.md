# 68 — Release builder + friend-docs traps

**Status:** proposed
**Priority:** P3 — small builder behaviors and friend-facing gaps the
2026-10-23 review and the 57/58 landing left open
**Area:** `release/build_release.py`, `release/templates/README.txt`,
`studio/doctor.py`, `docs/for-new-gms/TUTORIAL.md`
**Found by:** nine-lane adversarial review 2026-10-23 (release lane);
57/58 landed the big halves

## Problem

1. `--sfx-only` reuses the cached payload — template edits
   (install.cmd/launch.cmd/README.txt) are silently not tested.
2. An offline build fails even with a fully warm cache (the
   nodejs.org index fetch is unconditional).
3. AV quarantine is the expected first-wall for an unsigned
   self-extractor carrying 17 `.exe`/10 `.dll` binaries, and the
   SmartScreen guidance lives in README.txt — INSIDE the exe the
   friend can't yet run. Nothing reaches them before the first
   double-click.
4. If AV eats a sharp binary, the doctor's `--fix` advice says
   `npm install` — npm is NOT bundled, contradicting README.txt's
   "everything is bundled".
5. The tutorial nowhere discloses that campaign prose (lore,
   chronicles, session notes) leaves the machine for the LLM
   provider — it discloses spend and key locality only.

## Proposal

`--sfx-only` warns loudly when tracked templates are newer than the
cache; the index fetch is skipped when the pinned node zip is already
cached; a sidecar `BEFORE-YOU-RUN.txt` ships beside the exe (share
message includes it) carrying the SmartScreen steps + AV-exception
guidance; the doctor's sharp-repair path names the bundled-python
reinstall instead of npm; one honest disclosure line in TUTORIAL.md's
LLM section.

## Acceptance criteria

- [ ] `--sfx-only` with newer templates warns (or refuses) — builder
      unit test
- [ ] Warm-cache offline build reaches the smoke stage — builder unit
      test with the network call stubbed
- [ ] The sidecar doc is staged beside the exe — extends
      `test_release_seams.py`
- [ ] Doctor's tool-repair advice never names npm — studio test
- [ ] Tutorial carries the prose-leaves-the-machine disclosure —
      directly checkable
