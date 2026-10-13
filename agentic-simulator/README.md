# D&D Agentic Simulator

An agentic sandbox for building and playtesting D&D campaign worlds.
LLM-driven faction agents act inside a simulated world state; humans review
what *changes*, not volumes of prose.

Started over beers, 2026-09-21. Built slowly, in a restrained fashion.

## Why this exists

LLMs generate lifetimes of text per day; quality does not scale with volume.
The fix is not better generation — it is changing the unit of review. This
project holds a world *state*, not a manuscript, and generates prose only
for what someone actually touches. Verification effort goes to seeds
(premises, schemas, deltas), never to leaves.

## Principles

1. **State is the source of truth; agents only propose.** The state file
   (`../campaign-data/engine-home/state.yaml`; per-campaign canon under
   `../campaign-data/campaigns/<name>/simulation/`) is canonical. Agents
   read filtered views and propose actions; code resolves and writes back.
   Never trust a model's memory of the world.
2. **Dice dispose.** Outcomes come from rolls and rules, not model judgment.
   Randomness is an anti-slop device: it knocks the world off the median
   that a storyteller regresses to.
3. **Fog of war.** Each agent sees only what its faction could plausibly
   know. Information asymmetry is the engine of emergence; shared knowledge
   is the engine of choreography.
4. **Lazy evaluation.** We hold state, not prose. The eastern kingdoms have
   no history until a player asks, and then the state constrains it.
5. **Review seeds, not leaves.** Generation is hierarchical; a layer is
   approved before the next one hangs off it.
6. **Debug the pipeline, not the product.** A bad output traces back to a
   prompt, a constraint, or a schema — fix it there, then regenerate.

## Maps are a separate project

World generation, city maps, and battlemaps live in the sibling directory
`../dnd-mapping` (FMG, settlemaker, the battlemap pipeline — it has its own
README and AGENTS.md). Maps are valuable on their own; this sandbox takes
them as an **optional input**. The only coupling is `meta.atlas` in the
state file, a provenance pointer (world name, seed, home burg) that
no code reads. Remove it and everything here still runs.

## Layout

```
agentic-simulator/  (in Documents/dnd-campaign-generator)
  README.md                  this file
  requirements.txt           PyYAML — the only dependency, until that hurts
  agents/
    faction.md               prompt template for faction leaders (used at M2)
  src/
    store.py                 state load/validate/atomic write + engine-home
                             lock; resolves the campaign-data root
    views.py                 fog of war: the slice of state each faction sees
    resolve.py               dice + action resolver (contested actions, overextension)
    tick.py                  weekly tick: --live runs the LLM-driven week, --why explains
    llm.py                   LLM provider (OpenAI-compatible, llm.env-configured)
                             for faction proposals + chronicles
    scheduler.py             activation gate: quiet factions hold instead of acting
    consistency.py           pre-resolution fact-check of proposals (advisory)
    rumors.py                rumors with fidelity decay on each retelling
    why.py                   causality inspector: every event explains itself
    ingest.py                session notes -> pending events -> canon
    events.py                canonical audit event records
    effects.py               typed session effects: validate/apply/dedup (CLI)
    projects.py              faction projects: counters + completion effects (CLI)
    goals.py                 ledger-grounded goal-update proposals (CLI)
  # runtime data lives OUTSIDE this directory, under ../campaign-data/
  # ($CAMPAIGN_DATA_ROOT moves it):
  #   engine-home/state.yaml   canonical world state during a run
  #                            (+ optional meta.atlas provenance)
  #   chronicle/               one reviewable delta per week
  tests/                     zero-dependency suites; pytest or direct-run
                             (resolve, scheduler, consistency, rumors, ingest,
                             views, why, tick, llm, store, effects, goals,
                             projects, places, hand-edit tolerance, home lock,
                             review + seam fixes)
AGENTS.md                    the on-ramp for any agent new to this directory
```

See [`AGENTS.md`](AGENTS.md) for the quickstart.

## Roadmap

- **M0 — skeleton (done).** State schema, fog-of-war views, dice, a tick
  loop that runs without any LLM. Validates the plumbing.
- **M1 — one city, five factions (done).** The whole loop ran by hand once
  (chronicle 0002), then on real LLM agents (chronicle 0003): proposals from
  fog-of-war views, real dice, state diffs, one chronicle per week. Contested
  actions (targeting a rival that acted the same week) resolve by opposed
  rolls in `resolve_tick` — ties to the defender.
- **M1.5 — Infinite DM graft (done).** Five subsystems from
  `infinite-dm-design.md` grafted onto the M1 loop: activation scheduler
  (quiet factions hold), pre-resolution consistency check, rumors with
  fidelity decay, causality inspector (`--why`), session-note ingestion.
- **M2 — divergence harness.** Replay the same week N times under different
  seeds and choices; measure trajectory spread. Linearity becomes a number.
- **M3 — playtest agents.** Player-persona party runs through content;
  per-scene engagement reports; adversarial charop review of custom rules.
- **M4 — DM review loop.** The human reads chronicle diffs and approves or
  vetoes world events; rejected events trace back to pipeline fixes.

## Ground rules

- Every increment is the smallest one that still runs.
- Code arbitrates; prompts carry content. Nothing in `src/` generates prose.
- A mechanic must be explainable in one sentence before it is implemented.
