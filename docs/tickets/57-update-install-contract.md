# 57 — Update contract: llm.env clobber, frankeninstall, same-name downgrade

**Status:** in-progress (implemented + verified 2026-10-23; awaiting review)
**Priority:** P1 — the friend's ordinary update silently reverts their
key file; a failed update can cement a broken install; an old
download silently downgrades
**Area:** `release/templates/install.cmd`, `release/build_release.py`,
`release/README.md`
**Found by:** nine-lane adversarial review, 2026-10-23 (release and
product lanes found the llm.env clobber independently)

## Problem

The update path — the exe a friend already owns, run again — breaks
its own promises:

1. **llm.env clobbered.** `install.cmd:50` mirrors with
   `robocopy /MIR /XD` excluding only `campaign-data` and legacy
   `campaigns`; `llm.env` ships in the payload
   (`build_release.py:257`), so every update exe overwrites the
   installed key file. `release/templates/README.txt:44` promises
   updating "leav[es] your campaigns and settings alone", and :32
   tells the friend their key lives in `llm.env` — rotation through
   that file is silently reverted on the next update (generation then
   fails 401 pointing at a file the friend already fixed). Ticket 52
   (update.cmd) leans on "the preserve-list logic install.cmd already
   proves" — it proves the opposite; this ticket is that premise's
   correction.
2. **Frankeninstall.** On robocopy exit ≥8 the script prints "Trying
   to start anyway" and launches (`install.cmd:50-56`); there is no
   check for a running studio (locked `python.exe`/`node.exe` make a
   partial mirror the default outcome of a natural double-click), and
   `VERSION` — a root file robocopy copies before deeper trees —
   updates early. A mirror that dies mid-copy leaves new-VERSION +
   mixed old/new files, and the next run of the same exe takes the
   byte-compare fast path (`:15-22`) and never re-mirrors: the
   script's own recovery advice ("close everything and run the
   installer again") defeats itself. Disk-full feeds this — the flow
   needs ~3.3GB transient (156MB exe + ~1.6GB temp extraction +
   ~1.6GB install) while `README.txt:36-37` promises "about 2 GB".
3. **Silent downgrades.** Every build ships under the same filename
   (`CampaignStudio-0.1.0.exe`, `build_release.py:429`; the version
   comes from pyproject and has not moved). Double-clicking an old
   download silently downgrades — e.g. resurrecting the pre-fix
   transposed-walls exporter — campaigns intact, nothing looks wrong,
   no prompt.
4. **Untested.** The builder's verify runs the SAME exe twice; the
   cross-version update flow (old install + new exe) is exercised by
   no gate.

## Proposal

1. Preserve `llm.env` on update: `/XF "%DEST%llm.env"` whenever an
   installed copy exists (first install still seeds it from the
   payload).
2. Refuse to mirror over a running studio (probe the port / process
   before mirroring); make the fast path require VERSION match AND a
   completion stamp written only after a robocopy exit < 8, so a
   partial mirror re-mirrors instead of launching.
3. Name the artifact with its version
   (`CampaignStudio-<VERSION>.exe`) so an old download is at least
   legible as old; keep the version visible where a friend reporting
   a bug can quote it (the exe name is enough — no UI change needed
   here).
4. Add a cross-version update leg to the builder's verify: install
   the staged PREVIOUS payload (or a synthesized older one), edit
   `llm.env` and drop a campaign marker, run the new SFX, assert the
   key file survived, campaigns intact, VERSION advanced.
5. Fix the disk-space copy in README.txt and the stale "~233 MB" in
   `release/README.md` (the artifact is 156MB).

## Acceptance criteria

- [x] Update preserves an installed `llm.env` — pinned by the new
      builder verify leg (`verify_update`, runs inside the release
      smoke)
- [x] A failed mirror re-mirrors instead of fast-pathing (no stamp →
      no fast path) — same leg, forced-robocopy-failure variant
- [x] Running-studio refusal before mirroring — same leg
- [x] Artifact filename carries VERSION — directly checkable on the
      built exe
- [x] README disk-space and size copy match measured reality —
      directly checkable

## Implementation notes (2026-10-23)

Landed on `ticket/57-update-install-contract`. The full release build
was run to the end (`python release/build_release.py`, then
`--sfx-only` after a verify-assertion fix): all three `verify_update`
legs pass inside the release smoke — "update pass: llm.env + campaigns
preserved, VERSION advanced", "refusal pass: exit 1 before mirroring,
install untouched", "failed-mirror pass: no stamp -> re-mirror, not
the fast path" — and the artifact landed as
`CampaignStudio-0.1.0+20261023144403+v0.2.0+atlas-0.7.1.exe`
(155,524,501 bytes). Fast tier green (`run-tests.py`, all suites; the
new pins live in `studio/tests/test_release_seams.py`: three
install.cmd text-shape tests, a `make_sfx` naming test, two
real-robocopy `/XF` tests, and one end-to-end install.cmd update-
contract test driving the real script over a mini payload).

Deviations from the proposal, all measured:

- (a) `/XF "%DEST%llm.env"` — the proposed form — does not work:
      robocopy IGNORES a full dest path for `/XF` (tested: the
      payload's copy still overwrote the file), and a full SOURCE
      path stops the copy but not the `/MIR` extra-DELETION when a
      `--no-key` payload ships without an `llm.env`. The landed form
      is the conditional BARE name (`if exist "%DEST%\llm.env" set
      "KEEP_KEY=/XF llm.env"`), which excludes `llm.env` at any depth
      — fine, the payload carries exactly one, at its root. Both
      rejected forms are documented in the install.cmd comment and
      pinned by the robocopy tests.
- (b) The fast-path gate is a `.install-complete` stamp (a copy of
      the payload's VERSION written only on robocopy exit < 8), and
      the running-studio probe (netstat for `STUDIO_PORT`, default
      8765) runs BEFORE the fast path, so a same-version rerun over a
      live studio also refuses instead of launching a second
      instance.
- (c) The forced-failure variant uses a share-none file handle on a
      deep file the mirror must overwrite (robocopy exits 8/10 — the
      real "file in use" failure); a file-where-source-has-a-dir
      obstruction was tried first and only exits 3, not ≥ 8.
- (d) `verify_update` legs 2-3 run `install.cmd` directly from the
      extracted payload rather than paying second/third SFX
      extractions — the same script the exe runs (leg 1 uses the real
      exe).
- (e) Disk-space copy is MEASURED, and the old numbers were wrong on
      the high side: the payload extracts to 722,281,131 bytes
      (~750 MB), not ~1.6 GB, so the friend-facing need is ~1.7 GB
      free during install (155 MB exe + ~750 MB temp + ~750 MB
      installed), ~750 MB after. `release/README.md`'s stale ~233 MB
      became the measured ~155 MB.

Observed, not fixed (pre-existing): the verify legs park one
`cmd /K ...launch.cmd` wrapper each (`start`ed consoles outlive the
killed server; `verify_sfx` has always done this — the end-of-build
`rmtree(ignore_errors)` hides it). They were killed by hand after
this build; the work dir cleans up once they are gone.
