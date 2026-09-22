# Campaign Studio

One localhost app over the three D&D engines. It boots a single web page
with four tabs — Maps, World Building, Agent Simulation, Review — that read
and write campaign folders full of YAML. No database, no accounts, no
internet required: the server binds `127.0.0.1`, and a campaign is one
directory you can zip and move.

## Prerequisites

- Python 3.10+ (`python` on PATH).
- Python packages: `pip install -r studio/requirements.txt`
  (fastapi, uvicorn, pyyaml, httpx).
- Node + pnpm, for the map engines. On this machine use pnpm only — the
  nodist npm/npx shims are broken.
- An API key, eventually. The app works read-only without one: browse maps,
  lore, chronicles, factions, the review queue. Generating backstory or
  running a live week needs a Z.ai **coding-plan** key — set `ZAI_API_KEY`
  or put the key in `~/.zcode/v2/provider_config.json`. Requests go to the
  coding endpoint (`api/coding/paas/v4`); the pay-as-you-go endpoint returns
  error 1113 "insufficient balance" even when the plan pool is fine.

## Boot

Double-click `studio/start.bat`, or run:

    python studio/run.py

The server starts on `http://127.0.0.1:8765` (env `STUDIO_PORT` overrides)
and your browser opens. `Ctrl+C` stops it.

When anything fails, run:

    python studio/doctor.py

It checks python, the imports, node, pnpm, the three engine CLIs, vale
(optional), the campaigns folder, and the API key — and says precisely
which prerequisite is missing and the command that fixes it. `--json` gives
the same report the `/api/doctor` route serves.

## The four tabs

- **Maps** — generate a world from a seed, map a burg into a city, roll a
  battlemap scene (dungeon / cave / camp) with a Foundry UVTT.
- **World Building** — the campaign's grounding: geography, timeline,
  entities, naming voice. Generates backstory entries that are constrained
  to those files, staged as *pending* lore.
- **Agent Simulation** — the faction sandbox: fog-of-war views, live weeks
  on dice (costs API credits), chronicles, why-chains, rumor spread. A
  finished week proposes timeline entries, also pending.
- **Review** — one queue for everything awaiting your decision: pending
  lore, pending week events, verification findings inline. Approve writes
  into the timeline; delete discards.

## Where campaigns live

`Documents/D&D/campaigns/<name>/` — one folder per campaign: `campaign.yaml`,
`world/`, `lore/`, `simulation/`, `maps/`. Portable by zip; a new machine
needs the parent folder, Python, and the pip line above.
`python studio/assemble.py` builds the sample Ruravaqan campaign from the
engines' own artifacts.

## Guardrails

- Generation is fed or refused: a prompt grounded in nothing does not run.
- Nothing lands unreviewed: generated prose enters the timeline only
  through the Review tab.
- Every app feature has a CLI equivalent in the engines; the studio is a
  chassis, never a requirement.

## The engines

The studio invokes three sibling projects as headless CLIs and never
absorbs them. Their docs:

- `../dnd-mapping/AGENTS.md` — worldgen, settlemaker city maps, battlemaps.
- `../agentic-simulator/AGENTS.md` — the faction week simulation.
- `../verification/AGENTS.md` — the prose verification ladder (`check.py`).
