# 53 — Table-setup card: bundle the pinned companion, spawn it behind buttons

**Status:** proposed
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
   stamp).
2. **One button, one endpoint**: "Install the ICRPG preset" on the
   World-building tab's table-setup card → a studio endpoint spawns the
   bundled Python on `avtt-integration/publish.py <campaign> presets`
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
   buttons are NOT this ticket (companion ticket 05 lands first).

## Acceptance criteria

- [ ] Bundle contains `avtt-integration/` with its LICENSE; the VERSION
      stamp names the companion tag and its ATLAS_PIN — builder smoke
      extension.
- [ ] Fresh campaign with `system: icrpg` → button →
      `atlas-vtt/system-presets/icrpg.json` lands and Atlas picks it up
      live (manual check once, per the seam spec's M1 verify).
- [ ] Pin mismatch (companion pin 0.6.0, installed Atlas 0.7.1 — the
      real state on this machine) renders the warning inline; the install
      still succeeds (companion ticket 07's policy).
- [ ] Spawn endpoint: list-form args, no shell, refuses paths escaping
      the campaign — studio tests.
- [ ] Detection is read-only: no `.obsidian/` writes from the card path
      — studio tests.

## Implementation notes (YYYY-MM-DD)

_lands here when the status flips._
