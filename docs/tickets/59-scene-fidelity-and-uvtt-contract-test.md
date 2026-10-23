# 59 — Atlas scene fidelity: doors that survive import, and a consumer-contract test

**Status:** proposed
**Priority:** P1 — published scenes are missing most doors (live
evidence in ember-coast), and the exporter bug class that shipped
twice has no structural guard
**Area:** `dnd-mapping/src/battlemap.py`, `dnd-mapping/tests/`,
`studio/tests/test_integration.py`; references the companion
(dcg-avtt-integration, pinned v0.2.0)
**Found by:** nine-lane adversarial review, 2026-10-23 (mapping lane,
live evidence; tests lane designed the contract test)

## Problem

Three wrongnesses in published scenes, plus the structural gap that
let the last two through:

1. **Open doors are silently dropped.** `gen_dungeon` rolls
   `"closed": bool(rng.random() < 0.5)` (`battlemap.py:322`) —
   treating `closed` as a random play-state. In the UVTT/Atlas
   contract `closed: false` means WINDOW, and the companion's
   faithful Atlas-importer port places nothing for windows (companion
   `src/avtt/scene.py:272-275`; "Dungeondraft's windows: Atlas
   places nothing"). The cave entrance is `closed: False`
   (`battlemap.py:375`) — always dropped. Live evidence (2026-10-23):
   `campaign-data/campaigns/ember-coast/maps/sites/
   the-drowned-crypt/the-drowned-crypt.uvtt` carries 6 portals with
   closed flags `[F,F,F,T,F,F]`; the published
   `atlas-vtt/collections/ember-coast/scenes/
   the-drowned-crypt.atlasmap` contains 174 walls and exactly 1 door.
   Ticket 54's publish log read "left out: … 5 windows" and called it
   designed — it was this bug. Doorways render as gaps: no LOS
   blocking, no openable door; Foundry keeps all 6, so the two VTTs
   disagree about the same map.
2. **Doors land on solid walls.** `_door_cell` (`battlemap.py:276-
   285`) assumes the vertical corridor leg pierces room B whenever
   `bcy != acy`; when A's center row lies inside B's row span
   (`by <= acy <= by+bh-1`, `acy != bcy` — common with the 1-cell
   moat placement), the HORIZONTAL leg enters through B's side wall
   and the door is placed on B's solid wall at the wrong row.
   `tests/test_battlemap.py:185-199` covers only `acy` outside B's
   rows or equal to `bcy` — the overlap case is untested.
3. **Near-black scenes.** `wrap()` ships the SVG backdrop color as
   UVTT `ambient_light` (`battlemap.py:576`; `:454` — the two
   functions even carry different defaults): the dungeon backdrop
   `#120d0b` converts to `lighting.ambient: 0.05` (live). Outside
   torch radius the table sees black.
4. **No consumer-contract test.** The only real-content uvtt
   assertions are producer-side invariants added AFTER the 2026-10-20
   live finding; the studio site-job fake writes `'{"format":0.3}'`
   (`studio/tests/test_sites_job.py:54`), the integration chain stops
   at `assert uvtt` (existence, `test_integration.py:301-302`), e2e at
   `existsSync` (`studio/tests/e2e/second_evening.spec.ts:439`).
   Nothing anywhere imports a .uvtt the way Atlas does — which is
   precisely how the dialect and transposition bugs shipped (ticket
   54's live verify).

## Proposal

1. Fix door semantics: dungeon doors import as doors (`closed: true`
   is the honest initial state for a keyed dungeon door; emit a
   window only where a window is meant — the cave ENTRANCE is a
   door, not a window).
2. Fix `_door_cell` for the vertical-overlap case (door on the actual
   breach cell).
3. Decouple ambient from the backdrop color (a lighting value chosen
   as lighting; one shared default).
4. Add a consumer-contract round-trip to the fast tier: run
   gen→render→wrap, load the .uvtt through the companion's
   Atlas-importer port (vendored stdlib port, or subprocess against
   the pinned v0.2.0 tree), and assert DERIVED semantics: cell size =
   imageWidth/map_size within sane px bounds, every wall segment
   inside the grid, room rings closed, no portal silently dropped,
   ambient luma sane. Both 2026-10-20 bugs fail this test. Mirror one
   assertion into `test_integration.py`'s real chain so it cannot
   stay existence-only. Prefer the importer round-trip over golden
   files: it pins what Atlas computes, not bytes.

## Acceptance criteria

- [ ] All generated portals survive the importer round-trip — new
      `dnd-mapping/tests/test_uvtt_contract.py` (fast tier)
- [ ] Walls within grid, rings closed, cell px sane — same test
- [ ] Vertical-overlap door placement on the breach cell — unit in
      `dnd-mapping/tests/test_battlemap.py`
- [ ] Ambient luma within sane bounds — contract test
- [ ] Integration chain asserts portal count, not existence —
      `studio/tests/test_integration.py::test_battlemap_chain_seam`
- [ ] Drowned Crypt regenerated and republished; Atlas shows 6 doors
      (was 1) — manual check on ember-coast, shares the campaign
      sitting with tickets 46/49/54
