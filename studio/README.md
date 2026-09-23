# Campaign Studio

One localhost app over the three D&D engines. It boots a single web page
with four tabs — Maps, World Building, Agent Simulation, Review — that read
and write campaign folders full of YAML. No database, no accounts, no
internet required: the server binds `127.0.0.1`, and a campaign is one
directory you can zip and move.

New here? Follow [`TUTORIAL.md`](../TUTORIAL.md) (served live at
`/tutorial` once the app runs) — it walks a first campaign end to end.
The **?** button in the app header has the short version, and on boot the
page shows a setup strip whenever the doctor finds something missing.

## Prerequisites

The honest boot story: this is a technical newcomer's app, not zero-config.
Someone who has never run a server can double-click `start.bat` and follow
it — but the prerequisites themselves are real, and the batch file can only
point at them, not conjure them.

- Python 3.10+ (`python` on PATH; the `py` launcher works too —
  `winget install Python.Python.3.12` or
  [python.org/downloads](https://www.python.org/downloads/)).
- Python packages: `pip install -r studio/requirements.txt`
  (fastapi, uvicorn, pyyaml, httpx).
- Node + pnpm, for the map engines. Use pnpm, not npm/npx: every tool
  recipe in `dnd-mapping` is written and tested with pnpm (npm was broken
  in the original author's setup, so it never got covered). If you have
  working npm, it will probably work — pnpm is the known-good path.
- An API key, eventually. The app works read-only without one: browse maps,
  lore, chronicles, factions, the review queue. Generating backstory or
  running a live week needs a Z.ai **coding-plan** key — set `ZAI_API_KEY`
  or put the key in `~/.zcode/v2/provider_config.json`. Requests go to the
  coding endpoint (`api/coding/paas/v4`); the pay-as-you-go endpoint returns
  error 1113 "insufficient balance" even when the plan pool is fine.
  The page itself tells you when the key is missing: a dismissible setup
  strip under the header appears on boot whenever any doctor check fails
  (it polls `/api/doctor?quick=1`, presence-only — no tokens spent).

## Boot

Double-click `studio/start.bat`, or run:

    python studio/run.py

The batch file finds a Python (`python`, else `py -3`), prints exact
install instructions if it finds neither, checks the studio's four Python
imports, offers to run the pip line when they are missing, and then boots.
The server starts on `http://127.0.0.1:8765` (env `STUDIO_PORT` overrides)
and your browser opens. `Ctrl+C` stops it.

When anything fails, run:

    python studio/doctor.py

It checks python, the imports, node, pnpm, the three engine CLIs, whether
a week run is in progress, vale (optional), the campaigns folder, and the
API key — and says precisely which prerequisite is missing and the command
that fixes it. `--json` gives the same report the `/api/doctor` route
serves. Plain doctor only advises; `--fix` performs the repairs that are
safe to automate:

    python studio/doctor.py --fix

That relinks a broken pnpm tool tree with `CI=true pnpm install --offline`
in `dnd-mapping/tools/settlemaker` and/or
`dnd-mapping/tools/Fantasy-Map-Generator` (detected by probing each tree's
critical module: settlemaker's `dist/index.js` must load, FMG's
`node_modules/vitest/vitest.mjs` must exist) and runs
`pip install -r studio/requirements.txt` when the imports fail. Everything
else stays printed advice — in particular it never auto-runs
`assemble.py`, because that writes canon.

## After any change, run the tests

    python run-tests.py

The fast tier by default: the whole pytest suite with every engine faked
and every probe local — seconds, not minutes. `--all` adds the
real-engine integration tests, which invoke the actual CLIs and cost
actual minutes. If run-tests.py is green, the change is shippable.

## Snapshots — history is your undo

The `campaigns/` workspace is its **own nested git repository** (created
automatically the first time a snapshot lands), deliberately separate from
the parent repo: the parent holds the engines and the studio and is safe
to push to a public remote, while campaign data — your premises, factions,
chronicles — never leaves the machine unless you push the campaigns repo
somewhere private. Every approval, finished week, and generation
auto-commits just the campaign it changed. Canon is therefore never more
than one commit away from a known-good state — there is no undo button
because the history *is* the undo. `git -C campaigns log -- <name>` shows
the trail; a bad approval or a bad week is one checkout away from gone.
Zipping `campaigns/` carries the undo history with it.

## The engine homes are scratch

`agentic-simulator/world/` is a working area, not canon. A live week
copies the campaign's state in, runs, and copies the results back **only
on success** — so a crashed or killed week never corrupts a campaign, and
the engine home is restored from the campaign on the next boot/week. A
`.week-run-flag` in `agentic-simulator/world/` marks a run in progress
(or left over from a crash); the doctor reports it.

The flip side of scratch: if you hand-run weeks in agentic-simulator while
a studio campaign exists, the studio's next week will copy right over your
hand-run — the flag/`.bak` mechanism protects the studio's direction only.
Work through the app, or copy the state back into the campaign yourself.

## The four tabs

- **Maps** — generate a world from a seed, map a burg (a town on the world
  map) into a city, roll a battlemap scene (dungeon / cave / camp) with a
  Foundry UVTT.
- **World Building** — the campaign's grounding: geography, timeline,
  entities, naming voice. Generates backstory entries that are constrained
  to those files, staged as *pending* lore. Missing a culture's name seed
  list? The name helper offers to stage one from the page.
- **Agent Simulation** — the faction sandbox: create it right on the tab
  (premise + 2-5 factions, no LLM involved — it writes
  `simulation/state.yaml`), then fog-of-war views, live weeks on dice
  (costs API credits), chronicles, why-chains, rumor spread. A finished
  week proposes timeline entries, also pending.
- **Review** — one queue for everything awaiting your decision: pending
  lore, pending week events, verification findings inline. Approve writes
  into the timeline; delete discards.

The header's search box greps the selected campaign — chronicles, timeline,
entities, style files, geography, pending queue, `campaign.yaml` — one
case-insensitive line match per hit, capped at 50. Enter runs it, Esc or
the X dismisses it.

## Where campaigns live

`<checkout>/campaigns/<name>/` — one folder per campaign: `campaign.yaml`,
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
