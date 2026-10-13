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

- **Canonical world state**: `../campaign-data/engine-home/state.yaml`
  (the engine's scratch home — the studio copies a campaign's canon in
  from `../campaign-data/campaigns/<name>/simulation/state.yaml` and
  publishes accepted weeks back; `$CAMPAIGN_DATA_ROOT` moves the root).
  Faction agents are LLMs proposing
  actions from fog-of-war views (`agents/faction.md` template); the resolver
  (`src/resolve.py`, tests in `tests/test_resolve.py`) decides with dice.
- **Run a live week**: `python src/tick.py --live` (needs the LLM key
  already configured — see the key-discovery contract; it advances the
  clock and writes a chronicle). The
  scheduler decides who acts — quiet factions hold instead of calling the
  LLM; proposals pass a consistency check (warnings printed, never
  auto-rejected); successful actions spawn rumors that decay one fidelity
  grade per retelling.
- **Ask why**: `python src/tick.py --why <week> <faction-id>` — the causal
  chain behind any recorded event.
- **Fold in a real session**: `python src/ingest.py notes.md` → edit the
  pending file it names → `python src/ingest.py --apply
  ../campaign-data/engine-home/pending-week-N.yml`.
  History bullets have NO mechanical effect: add structured `effects:`
  entries (typed, allowlisted, dedup by stable id — see `src/effects.py`)
  to the pending file for real state changes, or drive the studio's
  session form. Validate without writing:
  `python src/effects.py --check session.yaml [--state PATH]`.
- **Projects**: factions can carry one concrete undertaking with a
  persistent counter (`state.yaml` `projects:`, rules in `src/projects.py`).
  One-sentence mechanic: a successful work action advances its project one
  step, a successful sabotage removes one, and reaching the target applies
  the preapproved completion effects exactly once. DM edits go through the
  CLI (`python src/projects.py --check|--preview|--apply changes.yaml`,
  `--report` for the DM list).
- **Run tests**: `python tests/test_<module>.py` (all zero-dependency).
- **Review**: read `../campaign-data/chronicle/` — one file per week,
  prose + full roll table.

## Optional map input

Maps live in a sibling project: `../dnd-mapping` (worlds, cities, battlemaps —
it has its own AGENTS.md). The relationship is one-directional and optional:

- This sandbox **consumes** map facts as provenance in the state file
  under `meta.atlas` (world name, seed, home burg). No code reads it; it
  exists so the sandbox agrees with the maps by construction.
- The mapping project **never** reads `state.yaml`. It is valuable on its
  own — point an agent at it and say "make a world".
- If `meta.atlas` is absent, everything here still runs.

## Ground rules (non-negotiable)

1. The state file (`../campaign-data/engine-home/state.yaml`) is the single
   source of truth. Code arbitrates; models
   propose; nothing in `src/` generates prose the state cannot back up.
2. Smallest increment that runs. A mechanic must be explainable in one
   sentence before it is implemented.
3. Spending is bounded: every run meters its LLM calls against an
   explicit ceiling (`LLM_MAX_CALLS`, default 24) and stops between calls
   with a clear incomplete status when exhausted — the token tally in the
   log is an estimate, never the provider's bill.
4. Machine setup notes: no Node tooling is needed to run this engine
   (stdlib Python only); map-engine installs elsewhere in the parent are
   plain `npm install`, engines invoking binaries with `node` directly.
   Dependencies: Python 3.10 + PyYAML. The LLM endpoint/key/model come from
   llm.env at the repo root (see llm.env.example).

## Directory map

```
agents/faction.md        the faction-agent prompt template
../campaign-data/engine-home/state.yaml
                         canonical world state (+ optional atlas provenance)
../campaign-data/chronicle/
                         one reviewable delta per week
src/tick.py              the weekly loop (--live, --why)
src/resolve.py           dice + contested actions + overextension
src/views.py             fog-of-war views (+ rumors heard)
src/llm.py               LLM provider (OpenAI-compatible; llm.env config)
src/scheduler.py         activation gate: quiet factions hold
src/consistency.py       pre-resolution fact-check (advisory warnings)
src/rumors.py            rumors: one fidelity grade lost per retelling
src/why.py               causality inspector
src/ingest.py            session notes -> pending events -> canon
src/store.py             state load/validate/atomic write + engine-home lock
src/events.py            canonical audit event records
src/effects.py           typed session effects: validate/apply/dedup (CLI)
src/projects.py          faction projects: counters, completion effects (CLI)
src/goals.py             ledger-grounded goal-update proposals (CLI)
tests/                   zero-dep: python tests/test_<module>.py
```

## Key-discovery contract

`src/llm.py` finds the LLM key in this order: `LLM_API_KEY` (the variable,
then `llm.env` at the repo root — `LLM_BASE_URL`/`LLM_MODEL` come from the
same file), then `$ZAI_API_KEY`, then a walk of
`~/.zcode/v2/provider_config.json`'s `providerRules` taking the first
non-`enc:` key, then a clear RuntimeError. `studio/llm_client.py` keeps
the same order (its small reader is a deliberate duplicate, D1: no imports
across projects); `verification/check.py` predates llm.env and knows only
the Z.ai steps. Never hardcode a provider index, the rules array reorders.
