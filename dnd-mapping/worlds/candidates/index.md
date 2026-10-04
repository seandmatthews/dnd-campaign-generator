# Atlas candidates — generated worlds

**CHOSEN 2026-09-21: candidate 3, Bonia.** The sandbox (Varn premise) is
anchored to **Ruravaqan**, a non-capital port of Shadia (merchant republic,
~29,600 inhabitants, coordinates 276.8/442.9) — see `meta.atlas` in
`world/state.yaml`. Other candidates remain here for future campaigns.

Generated 2026-09-21 with the locally built FMG (v1.153.0, `tools/Fantasy-Map-Generator`),
except candidate-01 (live site, v1.153.1). Raw sources in `worlds/raw/`
(`.map` saves, loadable in FMG); each has a derived `geography.yaml` produced
by `src/import_fmg.py` — regenerable, never hand-edit.

One honesty note: the seed values typed during automation did **not** take
(FMG re-randomized; details in `docs/fmg-agent-guide.md`). The seeds below are
the true ones read from the saved files, and they are reproducible when the
seed is entered through real UI input.

| # | World | Seed | Nations | Settlements | Cultures | vibe |
|---|-------|------|---------|-------------|----------|------|
| 1 | Pitbar | 682802326 | 21 | 1115 | 13 | Political archipelago — Breilbach Empire, Stia, Marsish Empire; big powers on shared seas. (no screenshot captured) |
| 2 | Seia | 145719006 | 21 | 1680 | 16 | High Fantasy super-continent — Scorcrosian Empire, Kingdom of Hethlindra, See of Shatharb; dense, many small states. |
| 3 | Bonia | 878636343 | 18 | 566 | 11 | Oriental archipelago — Enekian Khaganate, Hdian Caliphate, Kuiz Guo; island chains, naval flavor. |
| 4 | Lombreux | 985178135 | 16 | 1141 | 8 | Mediterranean, Antique cultures — Ragusian Empire, Holy State of Calerania, Republic of Zepatia; inland sea, few large powers. |
| 5 | Bouieland | 137451204 | 21 | 709 | 7 | High Island (one big continent) — Posian Empire, Holy State of Fjerduria; frontier seas, mixed Nordic/other phonologies. |

**Raw saves pruned 2026-10-04:** per the map-pipeline.md rule (a rejected
world's save is a judgment, not a fact — record the seed, delete the file),
the rejected candidates' `.map` saves (01 Pitbar, 02 Seia, 04 Lombreux,
05 Bouieland) are deleted from the repo; the seeds in the table above are
the reproduction record (re-enter them through real FMG UI input).
`worlds/raw/` keeps the chosen save (candidate-03.map, Bonia) and the
pinned `candidate-5-full.json` test fixture.

## Later worlds (not from the batch above)

| World | Seed | Raw source | Notes |
|-------|------|------------|-------|
| world-913 (Tasospon) | 913 | `campaigns/sable-ford/world/source.json` (full JSON export) | The Sable Ford campaign world. Re-imported with the river/culture schema 2026-09-25; the raw stays in the campaign repo, so `--all` does not regenerate this one. |
| world-607942 | 7942 | none tracked | Imported with the river/culture schema 2026-09-25. SUPERSEDED by world-607942-7942-eafab7 (the biome/latitude schema import has different bytes, so the collision-widened filename is now the canonical one). |
| world-607942-7942-eafab7 | 7942 | none tracked | Re-imported with the biome/latitude schema 2026-09-28 (first import whose bytes carry per-settlement biome/latitude). |
| world-uie2e9 | e2e9 | none tracked | Steered generation 2026-09-28: highFantasy culture set + climate options (recorded in `meta.culture_set`/`meta.climate`); biome/latitude schema. |
| world-24601 | 4601 | none tracked | Imported before the raw/ convention was settled; no raw dump exists, so it cannot be regenerated. Still on the pre-river/culture schema. |
| world-424242 | 4242 | none tracked | Same as world-24601: no raw, not regenerable, old schema. |

## Review notes

- **Candidate 3 (Bonia)** is the most interesting fit for the Varn sandbox
  scale: archipelago geography naturally isolates factions, naval routes give
  the longshoremen-equivalent leverage, and 566 settlements means less to
  lazy-evaluate. Its culture mix (khaganates + caliphates + guos) also tests
  whether fog-of-war diplomacy survives cultural asymmetry.
- **Candidate 4 (Lombreux)** is the most "D&D classic" — an inland sea with
  competing empires is basically a ready-made merchant-politics campaign.
- **Candidate 2 (Seia)** has the highest name density and 16 cultures; the
  riskiest for slop, the richest for divergence testing later.
- Candidates 1 and 5 are solid but conventional.

## To review a candidate yourself

1. Open the local app: `python -m http.server 8642 --directory tools/Fantasy-Map-Generator/dist` → http://127.0.0.1:8642
2. `Load` → `machine` → pick the `.map` from `worlds/raw/` (only the chosen
   save, candidate-03.map, is retained — rejected candidates must first be
   regenerated from their seed above)
3. Skim the derived summary: `worlds/candidates/<world>.yaml`
4. Screenshots for 02–05 are beside the YAMLs.
