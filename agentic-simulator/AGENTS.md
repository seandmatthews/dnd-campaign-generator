# AGENTS.md — start here

An agentic sandbox for building and playtesting D&D campaign worlds. LLM
faction agents propose actions inside a canonical world state; dice decide
outcomes; humans review chronicle deltas instead of volumes of prose.

This project is deliberately **faction-only**. Maps are a separate, standalone
project (see "Optional map input" below) — this sandbox runs with no maps at
all and treats them as an optional input.

**Full documentation: [`README.md`](README.md)** (charter, roadmap,
principles). Read it before changing anything.

## The state of play

- **Canonical world state**: `world/state.yaml` — the city of Ruravaqan
  (world: Bonia, seed 878636343), five factions in a probate war, currently
  at the week noted in `meta.clock`. Faction agents are LLMs proposing
  actions from fog-of-war views (`agents/faction.md` template); the resolver
  (`src/resolve.py`, tests in `tests/test_resolve.py`) decides with dice.
- **Run a live week**: `python src/tick.py --live` (needs the Z.ai key
  already configured; it advances the clock and writes a chronicle). The
  scheduler decides who acts — quiet factions hold instead of calling the
  LLM; proposals pass a consistency check (warnings printed, never
  auto-rejected); successful actions spawn rumors that decay one fidelity
  grade per retelling.
- **Ask why**: `python src/tick.py --why <week> <faction-id>` — the causal
  chain behind any recorded event.
- **Fold in a real session**: `python src/ingest.py notes.md` → edit the
  pending file it names → `python src/ingest.py --apply world/pending-week-N.yml`.
  History bullets have NO mechanical effect: add structured `effects:`
  entries (typed, allowlisted, dedup by stable id — see `src/effects.py`)
  to the pending file for real state changes, or drive the studio's
  session form. Validate without writing:
  `python src/effects.py --check session.yaml [--state PATH]`.
- **Run tests**: `python tests/test_<module>.py` (all zero-dependency).
- **Review**: read `chronicle/` — one file per week, prose + full roll table.

## Optional map input

Maps live in a sibling project: `../dnd-mapping` (worlds, cities, battlemaps —
it has its own AGENTS.md). The relationship is one-directional and optional:

- This sandbox **consumes** map facts as provenance in `world/state.yaml`
  under `meta.atlas` (world name, seed, home burg). No code reads it; it
  exists so the sandbox agrees with the maps by construction.
- The mapping project **never** reads `state.yaml`. It is valuable on its
  own — point an agent at it and say "make a world".
- If `meta.atlas` is absent, everything here still runs.

## Ground rules (non-negotiable)

1. `world/state.yaml` is the single source of truth. Code arbitrates; models
   propose; nothing in `src/` generates prose the state cannot back up.
2. Smallest increment that runs. A mechanic must be explainable in one
   sentence before it is implemented.
3. Machine setup notes: no Node tooling is needed to run this engine
   (stdlib Python only); map-engine installs elsewhere in the parent are
   plain `npm install`, engines invoking binaries with `node` directly.
   Dependencies: Python 3.10 + PyYAML. The LLM endpoint/key/model come from
   llm.env at the repo root (see llm.env.example).

## Directory map

```
agents/faction.md        the faction-agent prompt template
chronicle/               one reviewable delta per week
world/state.yaml         canonical world state (+ optional atlas provenance)
src/tick.py              the weekly loop (--live, --why)
src/resolve.py           dice + contested actions + overextension
src/views.py             fog-of-war views (+ rumors heard)
src/llm.py               Z.ai GLM provider
src/scheduler.py         activation gate: quiet factions hold
src/consistency.py       pre-resolution fact-check (advisory warnings)
src/rumors.py            rumors: one fidelity grade lost per retelling
src/why.py               causality inspector
src/ingest.py            session notes -> pending events -> canon
src/effects.py           typed session effects: validate/apply/dedup (CLI)
tests/                   zero-dep: python tests/test_<module>.py
```

## Key-discovery contract

`src/llm.py` finds the Z.ai key in this order: `$ZAI_API_KEY`, then a walk
of `~/.zcode/v2/provider_config.json`'s `providerRules` taking the first
non-`enc:` key, then a clear RuntimeError. The sibling copies of this
contract (`verification/check.py`, `studio/llm_client.py`) must keep the
same order and error — never hardcode a provider index, the rules array
reorders.
