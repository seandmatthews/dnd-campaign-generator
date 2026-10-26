# 59 — Atlas scene fidelity: doors that survive import, and a consumer-contract test

**Status:** completed (implemented + reviewed 2026-10-23 by the
ticket-sweep lane, 0b0a9e6, round 1; the sweep's merge was
auto-reverted by its flaking gate and re-landed as fbe97d6 with the
fast tier green. The republish half of the last box is DONE
2026-10-24: the Drowned Crypt regenerated from seed 2060015 (6
portals, all closed:true — doors, not windows) and republished via
the pinned v0.2.0 companion as `the-drowned-crypt-v2` — 232 walls,
6 doors, 4 lights, ambient 0.42, contract-rule-3 refusal → --force
sibling exactly as designed (campaign snapshot d182adb). Closed
2026-10-26 per user ruling: the human's look at the v2 scene in Atlas
is retired, not satisfied; see the close-out below)
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

- [x] All generated portals survive the importer round-trip — new
      `dnd-mapping/tests/test_uvtt_contract.py` (fast tier)
- [x] Walls within grid, rings closed, cell px sane — same test
- [x] Vertical-overlap door placement on the breach cell — unit in
      `dnd-mapping/tests/test_battlemap.py`
- [x] Ambient luma within sane bounds — contract test
- [x] Integration chain asserts portal count, not existence —
      `studio/tests/test_integration.py::test_battlemap_chain_seam`
- [ ] Drowned Crypt regenerated and republished; Atlas shows 6 doors
      (was 1) — manual check on ember-coast, shares the campaign
      sitting with tickets 46/49/54

## Implementation notes (2026-10-23)

Landed on `ticket/59-scene-fidelity-and-uvtt-contract-test`. In
`battlemap.py`: dungeon doors are `closed: true` (was a coin flip),
the cave ENTRANCE is `closed: true` (was `false` — a window Atlas
drops); `_door_cell` answers B's SIDE wall at A's center row when A's
center row lies inside B's row span (the breach the horizontal leg
actually carves); the ambient is decoupled from the backdrop —
`scene["backdrop"]` paints the SVG, `scene["ambient_light"]` is the
UVTT lighting value with ONE shared default `AMBIENT_LIGHT #6e6a63`
(Rec. 709 luma 0.42; the dungeon's backdrop `#120d0b` used to ship as
lighting.ambient 0.05); and `wrap()` closes every wall ring by
repeating its first point — found while writing the contract test:
the exporter's wall rectangles were OPEN chains (N points → N-1 wall
segments, every ring short one edge — the live ember-coast file
carried 58 open rings, which is exactly why the published scene had
174 walls: 58×3; closed rings import as 232: 58×4). The proposal's
"rings closed" assertion could not pass without it.

The consumer contract: `dnd-mapping/tests/test_uvtt_contract.py` +
`dnd-mapping/tests/atlas_import_port.py`, a vendored stdlib port of
the companion v0.2.0 importer (parse + convert; light-emission math
and the .atlasmap envelope omitted — documented in its docstring —
the port runs everywhere the fast tier does, where the companion's
nested git repo cannot). The test gen→render→wraps a 7-room dungeon
and a cave, imports the .uvtt, and asserts the DERIVED semantics:
importer accepts; cell size = imageWidth/map cells, within 10–200 px;
nothing clipped or dropped (`skipped` all zero); every wall segment on
the image; every ring a closed loop; every scene door present in the
file AND placed as a door (zero dropped as windows); ambient luma
0.2–0.9. A third test doctors a good file with each 2026-10-20 bug —
pixel-dialect map_size is refused "too small for its grid",
transposed walls land in skipped/outside — so the guard provably has
teeth.

Verified, exact commands run in the worktree: `python run-tests.py`
(all suites green, including the new `dnd-mapping-uvtt-contract`
suite this ticket adds to the fast tier; no table-companion crash —
this branch's run-tests.py carries no companion suite);
`python -m pytest studio/tests -q` (661 passed, 12 integration
deselected); `python -m pytest agentic-simulator/tests -q` (250
passed); and the seam test itself,
`python -m pytest studio/tests/test_integration.py::test_battlemap_chain_seam -m integration -q`
— 1 passed in 8.5s against the REAL engines: the worktree lacks the
gitignored `dnd-mapping/tools`, so it was junctioned read-only from
the main checkout for the run, and the module's ruravaqan NEEDS gate
was satisfied with a stub `state.yaml` the seam test never reads
(only the real battlemap CLI and real sharp ran); junctions and stub
removed afterward.

Drowned Crypt (criterion 6, unticked — the live sitting owns it):
regenerating its exact provenance (`gen-dungeon seed=2060015`) under
the fixed code, in a temp dir, then importing through the port gives
6 scene doors → 6 importer doors, 0 windows dropped, ambient 0.42,
232 walls in 58 closed rings. The republish on ember-coast and the
manual Atlas check belong to the shared campaign sitting
(tickets 46/49/54).

Ride-along: `worlds/ruravaqan/scenes/harbour-quay/harbour-quay.uvtt`
regenerated — the tracked record was a pre-2026-10-20 museum piece
(pixel map_size, flat portals, the dialect Atlas refuses) no later
ticket had re-run; it now carries the current wrap's output with the
ticket's fixes (its rings were already closed; image bytes identical,
no rasterizer involved).

## Close-out (2026-10-26)

Closed by user ruling with the final Atlas box retired, not satisfied:
the exporter fixes are pinned by the consumer-contract test, and the
v2 scene republished clean through the pinned companion (above); what
is retired is only the human look at the v2 scene in Atlas. Real use
at the table is now the verify — an issue found there mints a NEW
ticket, not a back-reference here. Shared ruling across
46/49/53/54/59; the table-surface verify formally lives in
dcg-avtt-integration (its AGENTS.md §"Atlas-side e2e", 6dcd5ea).
