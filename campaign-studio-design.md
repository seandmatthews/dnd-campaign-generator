# Campaign Studio
## One application, three tabs, one shared spine

**Status:** design / iteration plan — built as `Documents/D&D/studio/` when
commissioned
**Serves:** a newcomer who boots one app, sees one pretty page, and moves
between Maps, World Building, and Agent Simulation without ever learning the
incantations behind them
**Inherits:** everything in this parent. The three existing projects
(`dnd-mapping`, `agentic-simulator`, `verification`) are the engines; this
document is the plan for the chassis.

---

## 0. Core Idea

The three engines already do everything — generate worlds in seconds, map
cities, battlemap scenes, run LLM faction weeks on dice, audit prose. What
does not exist is the thing a human actually wants: **one object they can
open, and tabs that talk to each other.**

The working rule of the whole studio:

> **Every layer generates from the previous layer's data, never from
> nothing.**

Geography feeds lore. Lore feeds naming. Lore and geography feed the agents.
And the agents feed lore back — a live week is history being written. The
backstory that used to be generated top-down (slop) is now earned bottom-up
(dice, state, review). That closure of the loop is the product; the tabs are
just windows into it.

---

## 1. The Spine: the Campaign Workspace

One campaign = one directory of files. No database. (Same decision as the
infinite-dm doc's YAML-over-SQL: reviewable diffs beat schema migrations at
this scale.)

```text
campaigns/<name>/
  campaign.yaml            name, which world seed, pointers, created date
  world/
    source.map|source.json FMG dump — the raw genesis record
    geography.yaml         nations, cultures, settlements (import_fmg output)
  lore/
    timeline.yaml          ordered entries: {id, when, title, text,
                           source: authored | sim:week-N | session}
    entities/              one markdown page per entity (obsidian-
                           integration.md §1): frontmatter id/kind/name/
                           aliases + body; wiki-links are relationships
    style/
      names-<culture>.txt  canonical name lists per culture
      voice.md             excerpts that DEFINE the tone; the anti-mean device
  simulation/
    state.yaml             the faction sandbox state (as today)
    chronicle/             weekly chronicle files (as today)
  maps/
    cities/<slug>/         settlemaker outputs: svg, geojson, params.yaml
    sites/<slug>/          battlemap scenes: <slug>.json/svg/png, .uvtt
                           (+ companion note <slug>.md — vault §4)
```

Design notes:

- **`lore/style/voice.md` is the anti-slop device for naming.** New names
  are generated with the name lists + voice excerpts in the prompt. This is
  a soft guarantee ("in-voice"), never a hard one — say so in the UI.
- **The entity pages are the retrieval store.** Since the vault stages
  (obsidian-integration.md §1) `spine.load_entities()` parses
  `lore/entities/*.md` into the same dicts and loregen feeds them whole into
  prompts — no vector DB, deliberately (infinite-dm §3 non-goals). If lore
  ever grows past a few hundred pages, that is a later problem with a later
  design.
- **`timeline.yaml` entries carry `source`.** Provenance per entry: authored
  (the DM wrote it), session (ingested from real play), or sim:week-N
  (emerged from the agents). The chronicle-to-lore loop (M3) writes
  sim-sourced entries only through an approval queue.

---

## 2. The Cross-Tab Contracts

The three flows, stated as reads against the spine:

| when you… | the app reads | engine | writes |
|---|---|---|---|
| generate backstory | `world/geography.yaml` (nations, towns, populations — the logistics grounding) + `lore/timeline.yaml` | LLM prompt constrained to those files | `lore/timeline.yaml` (pending, DM-approved) |
| flesh out a town | burg record + road bearings from `world/source.*`; `lore/style/` for naming voice | headless FMG dump → settlemaker (generalized) | `maps/cities/<slug>/` |
| run the agentic sim | `simulation/state.yaml` + `lore/entities/` pages + place names | tick --live (scheduler, consistency, rumors, dice) | `simulation/chronicle/`, `state.yaml`, `state["rumors"]` |
| approve a week's history | chronicle delta + `why` causal chains | human decision | `lore/timeline.yaml` entries, `source: sim:week-N` |

Note the symmetry in the last row: the Agents tab *reads* lore for naming
and context, and *writes* history back into it. That loop is the reason the
spine exists.

---

## 3. Architecture

**D1 — The engines stay untouched, headless CLIs.** The studio is a fourth
child (`Documents/D&D/studio/`) that *invokes* the other three. No imports
across projects, no shared Python package. The tested seam stays intact;
every app feature retains a CLI equivalent, forever. (Guardrail: if a
feature can only be reached through the app, we built it wrong.)

**D2 — Stack: FastAPI + uvicorn, vanilla JS frontend.** This breaks the
"PyYAML is the only dependency" rule — deliberately, and this is where the
rule said it would hurt ("until that hurts"): the app needs path routing,
static files, and long-running job management. One `index.html`, one
`app.js`, one `style.css`; no React, no build step, no node_modules of our
own. Tabs are sections; data flows over `fetch`.

**D3 — Engine invocation: subprocess, list-form args, never `shell=True`.**
The parent path contains `&` (`Documents/D&D`) — shell-string commands
*will* break on it. Every engine call is `[python, "-X", "utf8", "src/tick.py",
"--live"]` style. Where the app needs machine-readable output, we add small
`--json` flags to the engines (back-compatible; the human-readable output
stays the default). Known offenders today: `tick.py` prints YAML views
(fine), `why.py` returns rendered lines (add `--json` in M3),
`import_fmg.py` prints a summary line (parseable already).

**D4 — Path overrides where engines have fixed paths.** `import_fmg.py`
writes to `dnd-mapping/worlds/{raw,candidates}` and `tick.py` reads a fixed
`world/state.yaml`. Rather than refactor engines around the workspace, the
studio *copies artifacts in*: generate in the engine's home, then place the
outputs into the workspace. Where an engine already takes a path
(`battlemap.py --dir`, FMG's `FMG_OUT` env), use it directly. Copying keeps
the workspace self-contained (a campaign folder is portable to another
machine) and touches zero engine code.

**D5 — Long jobs: a jobs dict + polling.** Worldgen is ~2.4 s (synchronous
is fine). A live week is minutes of LLM calls. The server keeps
`jobs = {id: {status, log_tail}}`; the UI polls `GET /jobs/{id}` once a
second and streams the tail. No websockets. The expensive button carries a
confirm: "This runs a real week — it spends API credits and advances the
canon clock."

**D6 — Localhost only.** Bind 127.0.0.1, no auth, no accounts. The LLM key
is read exactly as the engines read it today (env `ZAI_API_KEY`, else
`~/.zcode/v2/provider_config.json`), through the **coding endpoint**
(`api/coding/paas/v4`) — the pay-as-you-go endpoint returns 1113
"insufficient balance" even when the plan pool is fine.

**D7 — Windows machine realities, written down where we'll hit them.**
Moving any pnpm project breaks every junction in its `node_modules` (sharp,
vitest) — repair is `CI=true pnpm install --offline` per tool tree, already
documented in the map doc. The FMG headless runner is invoked as a vitest
run from the FMG repo root with `FMG_SEED`/`FMG_OUT` env — the studio wraps
exactly that incantation. `render-scene-png.mjs` imports sharp from
settlemaker's tree by absolute path — it works, but it is the most fragile
seam in the studio.

---

## 4. Iterations

Each iteration runs standalone and is verified before the next begins. The
verb is *achieves* — what a person can now do that they couldn't before.

### M0 — The Spine and the Shell (read-only)

**Achieves:** someone boots the app and sees the existing Ruravaqan
campaign — world map, city map, chronicles, factions, rumors — in three
tabs. Nothing generates yet; the magic trick is that all of it already
exists and just needs showing.

**Ships:** workspace schema + `campaigns/ruravaqan/` assembled from
existing artifacts (copied per D4); `studio/` server with
`GET /campaign/{name}`, `GET /campaign/{name}/lore|simulation|maps`;
one page, three tabs, the settlemaker + FMG SVGs embedded (they are
genuinely pretty); chronicle rendered as HTML; state rendered as a
faction/resources/rumors board.

**Build notes:** schema files first (this is the design moment — everything
else hangs off it). A small `assemble.py` that builds `campaigns/ruravaqan/`
from `dnd-mapping/worlds/*` + `agentic-simulator/{world,chronicle}/*` — the
one-time import of today's scattered artifacts into the spine. SVGs inline
via `<object>`/`<img>` from static mounts, no server-side rasterizing.

**Verify:** fresh boot → page loads → three tabs populated, engines never
invoked. Existing CLI paths still work (spot-check `tick.py`).

**Risks:** low. Mostly plumbing. The one design trap is copying artifacts
vs referencing them — D4 says copy; do not relitigate it in code.

### M1 — Maps That Generate

**Achieves:** in the Maps tab: type a seed, get a world — browse its
nations and settlements, click a burg, get a city map, click a location,
get a battlemap scene with a UVTT for Foundry. One tab, end to end, the
"let's make a world" AGENTS.md flow behind buttons.

**Ships:** `POST /campaign/{name}/world` (seed → headless FMG → import →
copy into workspace); world browser (nations/settlements from
`geography.yaml`); `POST .../cities` (burg → generalized settlemaker
invocation); site generation wired to `battlemap.py gen-dungeon/gen-cave/
gen-camp` + render/rasterize/wrap; jobs + polling UI appears here.

**Build notes:** the one real engineering task is **generalizing
`scripts/gen-ruravaqan.mjs` into `gen-city.mjs`**: it currently hardcodes
Ruravaqan's burg record and hand-computed road bearings. The generalization
must (a) accept a burg JSON path, (b) *derive* road bearings from the world
dump programmatically (nearest neighbors with roads), (c) pass
`harbourSize: 'large'` for ports — without that knob port cities get no
piers. Everything else is wrapping known incantations. Battlemap scenes
from city footprints need the city's GeoJSON in the workspace (it is, per
schema).

**Verify:** generate a fresh seed world in the UI; pick a coastal burg and
an inland one; both produce plausible city maps; a generated camp scene
wraps to UVTT and opens in Foundry if available. CLI equivalents still work.

**Risks:** bearing-derivation quality (a city with roads pointing the wrong
way feels wrong — eyeball several); settlemaker runtime per city (seconds,
fine); sharp junction fragility (D7 — repair recipe in the UI error message
is acceptable).

### M2 — Lore

**Achieves:** the World Building tab generates backstory that is *about
this world* — it names real nations, real towns, real populations from the
geography, and names new things in the campaign's own voice. This is the
end of top-down slop: the prompt is fed, or it refuses to run.

**Ships:** lore store UI (timeline, entities, style — editable as text
forms over YAML, since YAML is the source of truth); `POST .../lore/entries`
— backstory generation constrained to `geography.yaml` + existing
timeline/entities, output staged as *pending* entries; `POST
.../lore/name` — the naming helper (culture name lists + voice excerpts in,
candidate names out) used by M1's city generation for ward/POI labels once
it exists; verification's structural ladder run on every generated entry,
findings shown inline (Vale if installed, structural layer always).

**Build notes:** the generator prompt is ~60% verbatim from the chronicle
narrator in `llm.py` (which already enforces "only state what the ledger
backs") — retarget the ledger at `geography.yaml` + `timeline.yaml`.
Reuse `check.py`'s structural findings as a pre-approval display; do NOT
auto-block. Naming-voice quality is the soft spot: expect to iterate the
prompt against `names-<culture>.txt` size — with ~50–100 canon names the
phonetics carry; with 10 they will not.

**Verify:** generate a backstory entry for a region with no authored lore;
every place/name it mentions exists in `geography.yaml` or the entity pages
(the canon-audit prompt from `verification/prompts/` checks this
mechanically — dogfood it); generated names read in-voice to the DM's ear.

**Risks:** voice drift (mitigate: voice.md excerpts are curated by the DM,
not accumulated slop); the temptation to add a vector DB (resist; entities
+ whole-file loads carry a campaign easily).

### M3 — The Live World

**Achieves:** the Agent Simulation tab becomes a cockpit: see the factions'
fog-of-war views, run a real week, read the chronicle, ask why anything
happened, watch rumors move — and approve the week's events *into the
world's history*, where the lore tab and the naming helper will then use
them. The loop closes.

**Ships:** views board (per-faction `faction_view` output); "Run week N"
with the D5 confirm; chronicle viewer; `why <week> <faction>` rendered as
an indented causal chain (`why.py` grows a `--json` flag per D3); rumor
board (state["rumors"] with fidelity grades — nice first visualization:
fidelity decays one grade per hop, show the chain); the approval queue —
chronicle delta → proposed `timeline.yaml` entries (`source: sim:week-N`),
DM edits/approves, approved entries become lore the other tabs read.

**Build notes:** the approval queue reuses `ingest.py`'s pending-file
pattern exactly — propose to a pending file, human edits/deletes, apply
merges. No interactive prompts, no modal dialogs that hold state server-side;
the pending file *is* the state of the review. Add `--json` to `tick.py
--why` (or call `explain()` — it already returns a list of lines). The week
run is the one place the studio calls the LLM heavily; surface the job log
tail live (D5) because waiting blind feels broken.

**Verify:** run a real week 5 from the UI; approve two of its events into
the timeline; confirm the lore tab now shows them and a generated backstory
entry can reference week-5 facts; scheduler holds show as "held" rather
than silent.

**Risks:** cost surprise (the confirm + a running total in the job log);
the chronicle-to-timeline summarization quality (entries should be one
sentence each — constrain hard, the chronicle prose is already written and
reviewable, the timeline only needs its skeleton).

### M4 — The Review Loop, Unified

**Achieves:** one queue for everything awaiting a human decision across
all three tabs: pending lore entries, pending timeline approvals, and
verification findings. This is the sandbox roadmap's M4 (DM review loop)
landing in its natural home — the app.

**Ships:** `/review` — a single page listing pending items from all
sources with their provenance and their verification findings inline;
approve/edit/delete per item; approved items written back through the same
pending-file mechanics.

**Build notes:** almost no new machinery — M2 and M3 both stage pending
files, `check.py` already emits `queue.md`/`findings.json`. This milestone
is mostly the view. Keep it boring on purpose.

**Verify:** every generated thing in the campaign is either approved,
pending, or explicitly discarded — nothing lands in the spine unreviewed
(except simulation's mechanical state changes, which are code, not prose).

**Risks:** queue fatigue — if every week produces 30 items the DM stops
looking. Countermeasure: the queue sorts by severity and dedupes
verification findings; the structural layer exists precisely to keep the
human looking at the few things machines cannot judge.

### M5 — Boot and Polish

**Achieves:** the actual newcomer story: clone/zip the parent, double-click
one script, browser opens on the studio with a sample campaign ready. Plus
the pretty pass: the page should look like something you'd show a friend.

**Ships:** `start.bat` / `python studio/run.py` (uvicorn + webbrowser.open);
a dependency doctor (checks python/node/pnpm/vale presence, repairs pnpm
junctions with the documented incantation, reports the key situation
distinctly: no key vs 1113-balance vs coding-endpoint-fine); a
`campaigns/sample/` bootstrap that assembles a small fresh campaign on
first run; CSS pass on the one page.

**Build notes:** the doctor is the honest answer to "someone new could boot
it": we can get a technical newcomer to the pretty page in one command, and
the doctor tells them precisely which prerequisite they lack instead of a
stacktrace. The sample campaign is what makes the first five minutes feel
like magic — budget real time for choosing a good-looking seed.

**Verify:** on a second Windows machine (or a fresh user profile), from
nothing to "generated a world and ran a week" using only the doctor's
instructions.

**Risks:** this milestone expands to fill any time given. Timebox it; the
app is already useful at M4.

---

## 5. Non-Goals

- Multi-user, hosting, accounts, auth. Localhost, one DM.
- Any build step on the frontend; any framework with a packaging story.
- Editing maps in the browser. Artifacts are files; Tiled/Foundry remain
  the editors. The studio shows and generates, it does not retouch.
- Websockets, service workers, PWA anything. Polling.
- Vector databases, embedding stores, RAG plumbing. Whole-file loads.
- Absorbing the engines. They stay headless CLIs in their own projects,
  runnable and tested without the studio existing.
- Mobile.

---

## 6. Global Guardrails

1. **CLI equivalence, always.** If a feature can only be reached through
   the app, we built it wrong (D1). Every iteration's verify step
   spot-checks the CLI paths.
2. **The spine is files.** A campaign folder is portable by zip. No server
   state that matters; jobs are ephemeral, canon is on disk.
3. **Nothing lands unreviewed.** Generated prose enters the spine only
   through pending + approve. Simulation's mechanical changes are exempt
   (they are arithmetic, not prose).
4. **Generation is fed or refused.** A tab that cannot ground a generation
   in spine data says so and stops — it does not fall back to free
   invention. The whole anti-slop architecture is this rule wearing a UI.
5. **One page.** If the studio needs client-side routing, we have shipped
   too many features.
