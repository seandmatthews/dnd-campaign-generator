# 45 — Obsidian one-click vault registration

**Status:** completed (ae3f82a, 2026-10-05)
**Priority:** P2
**Area:** `studio/obsidian_vault.py`, `studio/routers/lore.py`,
`studio/static/js/world.js`, `studio/API.md`
**Found by:** GM-experience review follow-up — the Vault-not-found
walkthrough card was the last hand-run setup step in the lore flow
(2026-10-05)

> **This file is a reconstruction (2026-10-10).** The work landed under this
> ticket number without a ticket file ever being written — nothing in git
> history or on disk carried one (first gap found by the ticket-46
> adversarial review). Rebuilt from the commit and the shipped code so the
> archive is continuous; details below are from ae3f82a's diff, not from an
> original proposal.

## Problem

Registering the campaign's lore folder as an Obsidian vault required the DM
to run Obsidian by hand and click through vault creation before the
"Open in Obsidian" deep link worked at all. The World-tab card answered a
missing vault with a walkthrough of manual steps.

## Proposal (as implemented)

- `POST /lore/obsidian` merges the campaign's **lore folder** into Obsidian's
  vault list (`obsidian.json`): backup first, idempotent, server-resolved
  path, corrupt config = honest 409 — never a clobber
  (`studio/obsidian_vault.py`, `studio/routers/lore.py:137-152`; the path
  registered is `require_campaign(name) / "lore"`).
- `GET /lore/path` carries `obsidian_registered` so the UI can tell the two
  states apart.
- The World Building tab's "Authoring lore" card swaps the
  Vault-not-found walkthrough for a calm "Open" once registered, and keeps
  the quit-and-reopen caveat while Obsidian is running
  (`studio/static/js/world.js`).
- Tests: `studio/tests/test_obsidian_vault.py`,
  `studio/tests/test_lore.py`, `studio/tests/js/test_world_tab.cjs`,
  `studio/tests/js/test_copy_claims.cjs`.

## Acceptance criteria (as landed)

- [x] registering an unregistered vault merges the path into `obsidian.json`
      with a backup, and is idempotent — `test_obsidian_vault.py`
- [x] corrupt `obsidian.json` → 409 naming the problem, file untouched
- [x] card shows walkthrough before, Open after — `test_world_tab.cjs`

## Implementation notes (2026-10-10)

Scope note that matters to the Atlas seam: the registration is the **lore
subfolder as its own small vault**, not the campaign root — that is the
right scope for the lore deep-link and deliberately not what Atlas needs
(`atlas-vtt/` must live at the campaign-root vault). Documented as such in
ticket 46; a campaign-root registration would be new scope.
