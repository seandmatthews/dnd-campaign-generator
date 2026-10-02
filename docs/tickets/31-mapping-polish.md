# 31 — dnd-mapping: river grounding shape, declared caps, error copy

**Priority:** P2
**Area:** `dnd-mapping/src/import_fmg.py`, `studio/routers/maps.py`,
`dnd-mapping/tools/*` (environment)
**Found by:** 2026-10-02 full review P2-8, P3-37, P3-38, P3-39

## Problem

1. **River-settlement grounding silently skipped on browser full-JSON
   exports (P2-8, repro).** `_has_graph_arrays` (`import_fmg.py:134-141`)
   accepts only the columnar-dict cells shape; browser "full" exports carry
   cells as a list of per-cell dicts (the shape `_cell_biome:208-211`
   already supports) and vertices as a list — so grounding returns False,
   all rivers get `settlements: []`, and the summary prints "not computed —
   export lacks graph arrays", which is FALSE for this input. Reproduced on
   the tracked `worlds/raw/candidate-5-full.json`: 40 rivers, zero
   settlements. Headless dumps and `.map` saves unaffected. The module
   docstring (line 136) claims full JSON exports DO carry graph arrays.
2. **Nations' `neighbors` still silently truncated to `[:6]` with no cap
   declaration (P3-37).** The declared-cap house rule (river settlements
   print a `settlements_note` when the cap bites) is violated by its
   neighbor two blocks above (`import_fmg.py:342-345`).
3. **Literal `<slug>.uvtt` placeholder in the site-pipeline error
   (P3-38).** `routers/maps.py:690-694` prints the template instead of the
   expected filename (`scene/svg/png` are checked as concrete paths; the
   companion note correctly uses `{uvtt.name}` at `:719`). Surviving
   sibling of the past companion-note placeholder bug.
4. **Tool trees installed with pnpm despite the npm-only contract
   (P3-39).** `settlemaker/node_modules/sharp` is a symlink into `.pnpm/`,
   FMG's clone has `.modules.yaml` + an untracked `pnpm-lock.yaml` — the
   documented movability invariant ("npm's flat node_modules has no
   junctions", `setup-tools.py:20-26`, `vendor-patches/fmg/README.md`) is
   void, and the presence-only guardrails can't see it.

## Proposal

- Teach `_has_graph_arrays` both shapes (columnar dict AND list-of-dicts
  with `v`), and make the not-computed reason string honest per shape; add
  the tracked full-JSON dump as a test fixture asserting river settlements
  are computed.
- Add the `neighbors` cap note (same pattern as `settlements_note`) or
  raise/remove the cap — follow the house rule either way.
- Use the concrete expected filename in the error.
- Reinstall both tool trees with npm per the documented procedure (or
  amend the contract to match reality); have `setup-tools.py --check`
  detect symlinked `.pnpm` stores so the drift can't recur silently.

## Acceptance criteria

- [x] Importing `candidate-5-full.json` computes river settlements; the
      summary no longer claims missing graph arrays for list-shaped
      exports.
- [x] A state with 7+ neighbors either lists them all or declares the cap
      in the output (test).
- [x] The wrap-stage error names the real filename.
- [x] `setup-tools.py --check` fails on a pnpm-shaped tree; the live trees
      pass it.
