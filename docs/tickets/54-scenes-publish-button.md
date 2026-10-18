# 54 — Scenes publish button on the table-setup card

**Status:** proposed
**Priority:** P2 — the second half of ticket 53's card; unblocked since
companion ticket 05 (the scenes publisher) landed 2026-10-12
**Area:** `studio/routers/table.py`, `studio/static/js/world.js`
**Found by:** ticket 53's implementation, 2026-10-17 — the amendment
opening the door ("fold the scenes publish into this ticket at
implementation time while it still fits one sitting, else mint the
follow-up") ran out of sitting.

## Problem

The table-setup card installs the ICRPG preset but cannot publish a
battlemap scene — the actual map the table looks at. The companion's
scenes publisher exists (uvttToScene port, fed-or-refused config, the
`_inbox/` hand-off whenever it can't confirm the installed Atlas version
against its pin), but its only studio-facing route is ATLAS.md's manual
steps. The seam's mechanism has one button and deserves two.

## Proposal

The same endpoint pattern as `publish-presets`:

1. `POST /api/campaign/{name}/table-setup/publish-scenes`
   `{"site"?: str}` → spawns `publish.py <campaign> scenes
   [--site <slug>]` under the studio's Python — list-form args, explicit
   cwd, never a shell, campaign lock, the campaign dir server-resolved
   only, 120s cap. Refusals and the `_inbox/` degrade note come back
   `ok:false`/stdout verbatim; exit 0 snapshots the campaign.
2. The card gains a scenes section: a site picker (the slugs under the
   campaign's `maps/sites/`), a publish button, and the publisher's
   output rendered verbatim — including the `_inbox/` note, which is the
   designed pin-mismatch behavior, not an error.

## Acceptance criteria

- [ ] Spawn list-form/no-shell/resolved-dir — studio tests mirroring
      `test_table_setup.py`
- [ ] Scenes refusal and `_inbox/` degrade text rendered verbatim, no
      snapshot on refusal — studio tests
- [ ] Manual once on a real campaign: publish a scene, Atlas shows it;
      the pin-mismatch path renders the `_inbox/` note — shares the
      campaign rebuild with tickets 46/49/53's pending live verifies
