# 53 — Table-setup card: bundle the pinned companion, spawn it behind buttons

**Status:** in-progress (implemented 2026-10-17 — builder staging + stamp
+ smoke, the spawn endpoint, the card, 8 studio tests; the live verify
pends the shared campaign rebuild with 46/49; the scenes button spun out
as ticket 54)
**Priority:** P1 — the friend's path to the seam's "cool stuff" (needs
companion ticket 07; independent of core 51/52)
**Area:** `release/build_release.py` (bundle + stamp), `studio/routers/`
(spawn endpoint), `studio/static/js/world.js` (+ the card),
`docs/for-new-gms/ATLAS.md`
**Found by:** the distribution design, 2026-10-12
(`docs/2026-10-12-distribution-design.md` §4–§5). The friend-facing
interface is buttons; the companion's own contract rule 4 anticipated
this ("the studio may one day spawn this repo's CLI — spawning a process
is loose coupling; merging code is not the fix").

## Problem

The companion's presets publisher exists (companion ticket 04: 780-line
validator port + installer) and reaches nobody: its channel is git clone
+ uv, and the audience is less-technical friends who have neither by
design. Meanwhile the studio bundle is already the distribution unit and
the app's cards are already the interface — the seam's mechanism just
never got a button.

## Proposal

Per the design doc §4–§5:

1. **The bundle carries the companion as a pinned release**: a git tag
   of `dcg-avtt-integration` (companion ticket 07's discipline),
   vendored at `avtt-integration/` beside the studio in the install
   root, source + AGPL LICENSE intact — the GPL settlemaker precedent
   (the bundler already ships third-party license + source; prove the
   LICENSE file lands and name the tag + its ATLAS_PIN in the VERSION
   stamp). Unlike settlemaker, the companion is not inside this repo —
   the builder grows a clone-at-tag step, with setup-tools.py's hygiene:
   an existing `avtt-integration/` that is not a clone at the pinned tag
   is refused (move it aside).
2. **One button, one endpoint**: "Install the ICRPG preset" on the
   World-building tab's table-setup card → a studio endpoint spawns the
   bundled Python on `avtt-integration/src/avtt/publish.py <campaign>
   presets` (verified 2026-10-17: the script self-shims `src/` onto
   `sys.path` and runs standalone under a bare interpreter; the
   companion's only runtime dep is PyYAML, which the bundle's Python
   already carries — nothing to install, no launcher shim needed)
   (list-form args, explicit cwd, never shell — the subprocess
   discipline both repos already share) and streams stdout into the
   card. The companion writes only `atlas-vtt/` + the embed line;
   nothing about the seam's file contracts changes.
3. **The card shows the version line**: `publish.py version` output
   (companion VERSION, ATLAS_PIN, detected Atlas from the vault's
   `.obsidian/plugins/atlas-vtt/manifest.json`) rendered in place, with
   the pin-mismatch warning surfaced inline when present. No spawn runs
   without the campaign's `system:` field matching the pack (the
   publisher refuses; the card shows its refusal text rather than
   pre-validating a second time).
4. **Atlas-plugin detection, read-only**: the card's status line checks
   the active campaign's `.obsidian/plugins/` for the plugin and shows
   either a checkmark or the four ATLAS.md steps. It never writes
   `.obsidian/` (spine-ignored: canon snapshots, doctor, gitignore).
5. **Vault registration** links to the existing World-tab card
   (`world.js:742` — the lore-subfolder vault, not the campaign-root
   vault; ATLAS.md Step 2 already explains the difference). ATLAS.md
   remains the always-true manual twin of every button here. Scenes
   buttons: companion ticket 05 landed 2026-10-12, so they are unblocked —
   fold the scenes publish into this ticket at implementation time while
   it still fits one sitting, else mint the follow-up (same endpoint
   pattern either way).

**Open ruling (call it when this ticket starts):** the bundle pins the
companion's v0.1.0, whose `ATLAS_PIN` is Atlas 0.6.0, while the table
machine runs 0.7.1 — ship v0.1.0 as-is and exercise the designed mismatch
warning inline (the lean), or wait for the companion's 0.7.1 pin bump
(its ticket 08).

## Acceptance criteria

- [ ] Bundle contains `avtt-integration/` with its LICENSE; the VERSION
      stamp names the companion tag and its ATLAS_PIN — builder smoke
      extension.
- [ ] Fresh campaign with `system: icrpg` → button →
      `atlas-vtt/system-presets/icrpg.json` lands and Atlas picks it up
      live (manual check once, per the seam spec's M1 verify). The
      rebuilt campaign here can be the same one that closes tickets 46
      and 49's pending live verifies — one sitting, three tickets.
- [ ] Pin mismatch (companion pin 0.6.0, installed Atlas 0.7.1 — the
      real state on this machine) renders the warning inline; the install
      still succeeds (companion ticket 07's policy).
- [ ] Spawn endpoint: list-form args, no shell, refuses paths escaping
      the campaign — studio tests.
- [ ] Detection is read-only: no `.obsidian/` writes from the card path
      — studio tests.

## Implementation notes (2026-10-17)

- **Builder** (`build_release.py`): `ensure_companion()` clones the
  companion at the pinned tag into `release/cache/avtt-integration` —
  reused while the cache sits exactly at the pin, re-cloned when the pin
  moves (tags are immutable), refused if the dir is not a git clone.
  `build_stage` copies it to `avtt-integration/` (`IGNORE_BASE` strips
  `.git`). The VERSION stamp gained `+v0.1.0+atlas-<pin>`, read from the
  staged companion by the bundled python (`avtt._version`) so it cannot
  drift from what shipped; install.cmd's byte-compare is untouched (it
  never parsed VERSION). Smoke grew the two builder acceptance checks:
  the LICENSE must land, and `publish.py --help` must run on the bundled
  python.
- **Studio** (`routers/table.py`): `GET …/table-setup/status`
  (existence-only detection: companion found, Atlas plugin dir present)
  and `POST …/table-setup/publish-presets` — the spawn seam: list-form
  args, explicit cwd, never a shell, 120s cap, campaign lock, the
  campaign dir server-resolved only, refusal = 200 + `ok:false` with the
  publisher's text verbatim, success = canon snapshot. Companion home:
  `$AVTT_INTEGRATION_HOME` → bundle layout → dev sibling; never imported
  (D1). `/atlas` serves ATLAS.md like `/tutorial` (the file was already
  staged by ticket 46; the card links it).
- **Card** (`world.js` `tableCard()`): status line, the Install-the-
  ICRPG-preset button, the publisher's stdout verbatim in a `<pre>`, and
  the manual-twin notes (ATLAS.md steps; the lore-subfolder vs
  campaign-root vault distinction).
- **Tests:** `studio/tests/test_table_setup.py` (8) — list-form/no-shell/
  resolved-dir spawn, verbatim refusal with no snapshot, honest 504,
  404-before-any-spawn (including a crafted escaping name), honest 409
  without the companion, read-only `.obsidian/`, home-resolution order.
- **Deviations:** (a) the card's status line checks plugin *existence*
  only — the authoritative version report stays a companion subprocess
  away rather than the studio re-implementing pin comparison; (b) the
  scenes button did not fit this sitting — spun out as ticket 54 (same
  endpoint pattern); (c) the pin ruling was taken as this ticket's
  recorded lean: v0.1.0 (ATLAS_PIN 0.6.0) against the table machine's
  0.7.1 — the designed mismatch warning is the expected behavior;
  companion ticket 08 lands on its own cadence.
- **Still open:** the live verify (acceptance 2) — the shared campaign
  rebuild with `system: icrpg` closes this ticket's manual check and
  tickets 46/49's pending verifies in one sitting.
