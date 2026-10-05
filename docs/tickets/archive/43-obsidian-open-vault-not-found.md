# 43 — "Open in Obsidian" on a brand-new campaign errors "Vault not found"

**Status:** completed (2026-10-05)
**Priority:** P3
**Area:** `studio/static/js/world.js` (Authoring lore card copy), `docs/for-new-gms/TUTORIAL.md`, `studio/README.md`
**Found by:** live first-GM pass on a fresh campaign (emberhollow), 2026-10-04, Obsidian 1.13.7

## Problem

The World Building card says: "With Obsidian installed, Open focuses it
(the first click on a new folder asks Obsidian to add the vault; that
dialog is Obsidian's own)." Observed with Obsidian 1.13.7 on Windows: the
`obsidian://open?path=…` URI for a not-yet-registered folder does not
offer to add a vault — Obsidian shows a modal error, "Vault not found.
Unable to find a vault for the URL obsidian://open/?path=…". The GM is
stuck with an error dialog and no path forward from inside the app.

What actually works (observed on the same pass):

- Obsidian's own "Open folder as vault" (vault manager), pointing at
  `campaigns/<name>/lore`; after that one-time registration, both the
  app's obsidian:// link and `path=` URIs resolve.
- Hand-editing `%APPDATA%/obsidian/obsidian.json` (the ticket-35
  maneuver) — but that is not a newcomer path.

So the integration is fine; the app's copy promises an Obsidian behavior
that no longer exists, and the error the GM actually hits is unexplained.

## Proposal

1. Fix the copy on the Authoring-lore card (and TUTORIAL/README where
   they repeat it): first click on a NEW campaign's lore folder shows
   Obsidian's "Vault not found" until the folder is registered once via
   Obsidian's vault manager ("Open folder as vault" → the lore path the
   Copy path button gives). Say exactly that, in that order.
2. The card's error state should carry the same guidance when the vault
   is not yet registered (the app can detect the click fell through —
   the URI opens nothing in the app; a static "first time? register the
   folder" hint under the Open button is enough; no vault probing).

## Acceptance criteria

- [x] The Authoring-lore card copy describes the real first-click
      behavior and the vault-manager registration step — checkable
      against `studio/static/js/world.js` render text
- [x] TUTORIAL.md and README.md contain no "Obsidian asks to add the
      vault" claim — `grep`-checkable
- [x] A first-GM e2e or js test pins the card text — pinned by
      `studio/tests/js/` test on the lore card render

## Implementation notes (2026-10-05)

Landed as one change on `master` (ticket moved to `archive/` in the
same commit).

- The claim turned out to live ONLY in the card
  (`world.js` `authoringCard()`); grep confirms TUTORIAL.md and
  README.md never repeated it, so criterion 2 holds with no doc change
  (the two docs say "open the campaign folder in Obsidian" without the
  add-vault claim — nothing dishonest to fix there).
- The card's form-note now states the real behavior ("a folder Obsidian
  has never seen has no vault to focus: the first click shows
  Obsidian's own 'Vault not found' error, not an add-vault dialog"),
  and a static `.lore-open-hint` note under the buttons carries the
  recovery path in newcomer order ("Open folder as vault" → the path
  the Copy button gives). No vault probing, per the proposal.
- Pinned by a new case in `studio/tests/js/test_copy_claims.cjs`
  (renders the world tab through minidom and asserts the card text).
- **Ride-along, named:** the same suite had been silently passing with
  only its first test ever running since it landed (ticket 39):
  `confirmRun` attaches its click handler to the modal overlay and
  minidom does not bubble clicks, so the 39d tests' `.click()` on the
  cancel button never reached it, their promise never settled, and the
  drained node event loop exited 0. Fixed by firing the click on the
  overlay with the button as target, and a stalled guard now fails the
  suite loudly if any test's promise never settles (instead of the
  silent exit-0 false green).
