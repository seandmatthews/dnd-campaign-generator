# Campaign Studio

One localhost app over the three D&D engines. It boots a single web page
with four tabs — Maps, World Building, Agent Simulation, Review — that read
and write campaign folders full of YAML. No database, no accounts, no
internet required: the server binds `127.0.0.1`, and a campaign is one
directory you can zip and move.

New here? Follow [`TUTORIAL.md`](../docs/for-new-gms/TUTORIAL.md) (served live at
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
  (fastapi, uvicorn, pyyaml, httpx). If you use
  [uv](https://docs.astral.sh/uv), skip this: `uv run python studio/run.py`
  creates the environment and installs the locked packages by itself.
- Node 18+, for the map engines (npm comes with it; the tool install is
  plain `npm install`, and engine invocations are `node <binary>` calls,
  so no global packages are needed).
- An LLM API key, eventually. The app works read-only without one: browse
  maps, lore, chronicles, factions, the review queue. Generating backstory
  or running a live week needs a key for **any OpenAI-compatible
  provider**: copy `llm.env.example` (repo root) to `llm.env` and fill in
  `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` — Z.ai, OpenAI, OpenRouter,
  or a local Ollama all work. The file is git-ignored; environment
  variables override it. With no llm.env, the built-in default is the Z.ai
  coding endpoint plus the key from `ZAI_API_KEY` or
  `~/.zcode/v2/provider_config.json` (the coding-plan key — the
  pay-as-you-go endpoint returns error 1113 "insufficient balance" even
  when the plan pool is fine).
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

It checks python, the imports, node, uv (optional), the three engine CLIs,
whether a week run is in progress, vale (optional), the campaigns folder,
the vault links, and the LLM key situation — and says precisely which
prerequisite is missing and the command that fixes it. `--json` gives the
same report the `/api/doctor` route serves. Plain doctor only advises;
`--fix` performs the repairs that are safe to automate:

    python studio/doctor.py --fix

That repairs a broken tool tree with plain `npm install` (FMG's install
adds `--ignore-scripts` — its only install-time script is dev-only git
hooks, and npm runs scripts through cmd.exe, which cannot handle the `&`
in this repo's default path) and runs
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

Snapshots are reported, not assumed: approve/delete/week results carry
`snapshot: {"state": "committed|no_change|unavailable|failed", ...}`. Only
`committed` (and the benign `no_change`) mean the undo trail actually has
the change; if git was missing or failed, the UI says so and
`POST /api/campaign/{name}/snapshot` retries. Approvals are also
**transactional** (studio/publication.py): a crash mid-approval is
completed (or parked as a visible conflict, never silently resolved) on
the next boot, and re-submitting the same approval answers with its
recorded result instead of applying twice. An editor that ignores the
app (Obsidian) can still make a staged approval stale — that is a 409
with nothing written, not a silent overwrite; exclusive cross-process
editing is not guaranteed.

## The engine homes are scratch

`agentic-simulator/world/` is a working area, not canon. A week run copies
the campaign's state in, runs against scratch, and stages the output as a
**proposal** — the campaign itself changes only when you accept the whole
proposed week (Simulation tab, or `POST .../simulation/proposal/{id}/accept`).
A crashed or killed run never corrupts a campaign, and the engine home is
restored from the campaign on the next boot/week. A `.week-run-flag` in
`agentic-simulator/world/` marks a run in progress (or left over from a
crash); the doctor reports it.

The flip side of scratch: if you hand-run weeks in agentic-simulator while
a studio campaign exists, the studio's next week will copy right over your
hand-run — the flag/`.bak` mechanism protects the studio's direction only.
Work through the app, or copy the state back into the campaign yourself.

## The four tabs

- **Maps** — generate a world from a seed, map a burg (a town on the world
  map) into a city, roll a battlemap scene (dungeon / cave / camp) with a
  Foundry UVTT. Both forms take an optional **attach to place** — the map's
  companion note (`<slug>.md` beside the images) wiki-links the place, and
  city maps stub their burg's page when it doesn't exist yet.
- **World Building** — the campaign's grounding: geography, timeline,
  entities, naming voice. Entities are markdown pages
  (`lore/entities/One Page Per Entity.md`, wiki-links as relationships —
  docs/design/obsidian-integration.md §1); click a row to read the page with its
  backlinks ("mentioned in"). Generates backstory entries that are
  constrained to those files, staged as *pending* lore. Missing a culture's
  name seed list? The name helper offers to stage one from the page.
  Dangling wiki-links get their own card: *Write the missing pages* turns
  each one into an LLM draft grounded in the linking pages, staged to
  Review like everything else. The **Quests** card groups the `kind: quest`
  pages by status (hooked / active / resolved / abandoned), flags overdue
  timers loudly, and opens the lore-page view on a click; a status flip is
  a reviewed write (lock- and revision-checked, snapshotted) — or just edit
  the frontmatter in Obsidian.
- **Agent Simulation** — the faction sandbox: create it right on the tab
  (premise + 2-5 factions — create is free, no LLM; arrive without a
  concept and one metered LLM pass will draft the factions from your
  premise to prefill the form, which you then edit and Create), then
  fog-of-war views, proposed weeks, the
  chronicle, why-chains, rumor spread. **Propose week** runs the engine
  against scratch data (costs API credits) and stages a proposal: the diff
  (clock, resources, events, rumors), the proposed chronicle, and the
  timeline entries appear at the top of the tab while the live campaign is
  byte-identical to before. **Accept** publishes the whole week exactly
  once (idempotent, revision-checked, transactional); **discard** keeps an
  audit receipt and changes nothing. New chronicles are linkified at
  accept: the first mention of each known name becomes a wiki-link, so
  backlinks answer "where did this faction show up in the story".
- **Review** — one queue for everything awaiting your decision: pending
  lore, pending page drafts, legacy pending week events (timeline-only —
  the old flow's weeks were already committed; approving never re-applies
  effects and deleting never undoes a week), verification findings inline —
  plus the vault's own advisory findings (dangling wiki-links,
  name/alias collisions). Approve writes into the timeline (and stubs the
  entry's new names as pages); delete discards.

The header's search box greps the selected campaign — chronicles, timeline,
entity pages, style files, geography, pending queue, `campaign.yaml` — one
case-insensitive line match per hit, capped at 50. Enter runs it, Esc or
the X dismisses it.

## Where campaigns live

`<checkout>/campaigns/<name>/` — one folder per campaign: `campaign.yaml`,
`world/`, `lore/` (timeline, `entities/` pages, style), `simulation/`,
`maps/`. Portable by zip; a new machine needs the parent folder, Python,
and the pip line above. Opening a campaign whose lore still lives in the
old `lore/entities.yaml` migrates it to pages once, automatically.
`python studio/assemble.py` builds the sample Ruravaqan campaign from the
engines' own artifacts.

## Guardrails

- Generation is fed or refused: a prompt grounded in nothing does not run.
- Nothing lands unreviewed: generated prose enters the timeline only
  through the Review tab.
- Every app feature has a CLI equivalent in the engines; the studio is a
  chassis, never a requirement.

## Briefing and the player handout

The Knowledge tab (facts, briefing, party disclosure) and the Simulation tab's data compile — with no LLM
pass — into a **DM briefing**: changes in a week range, unresolved
decisions (pending proposals clearly labeled as NOT canon, stale facts,
pending reviews), capped at ten items with an honest "N more not shown"
count, and the **quests section**: overdue timers first (a `due` the clock
or calendar has passed while the quest is not `resolved` — the forgotten
timer is the one that kills a campaign), then active quests touching the
places/factions that changed in the range, then the hooked count as one
line. `GET /api/campaign/{name}/briefing/markdown` is the durable copy.

**Next session prep** is one click beside the briefing card: it compiles
the briefing's events, the freshest week's rumors, the open quests, the
public facts still waiting to be revealed, and the open decisions into ONE
disposable vault page — `lore/prep/<date>-next-session.md`, every item
linked back to its source of record (chronicle, entity page, fact source
file). A compile, not a generation: same canon, same bytes, no LLM pass —
and a re-run takes the next free `-2` name instead of overwriting; the GM
deletes what they don't want, in Obsidian.

The **player handout** is a separate, deliberate step: revealing a fact
(only current `public` facts can be revealed) copies its approved text into
the party-disclosure record, and the handout contains only what was
revealed. Public in the world is not automatically known by the party —
the reveal IS the discovery. An empty disclosure set exports an empty
handout with an explanation, never the DM briefing.

## Budget

Every LLM-consuming run (a week, a lore generation) meters its provider
calls against an explicit ceiling (`LLM_MAX_CALLS`; unset, it scales with the roster — 2 calls per faction plus narration, minimum 24) and stops
between calls when it runs out: a WEEK aborts with nothing written (no all-holds canon week), other runs stop with a clear incomplete status — a budget
guard, not a billing estimate; the token tally in the logs is approximate
and never presented as the provider's exact charges.

## Export and restore

`python studio/export_campaign.py <campaign>` packs ONE campaign into a
zip with a sha256 manifest of every included file. It excludes machine
noise (recovery payloads, caches, temp/log files) and any credential file
dropped inside the campaign; it never packages other campaigns. Stated
plainly in the manifest and the output: **the zip does not include git
history** (the undo trail lives in `campaigns/.git`, outside the campaign
folder), and **a same-disk archive is an export, not a backup** — copy it
to a separate destination yourself; nothing uploads anywhere.

`python studio/export_campaign.py --restore <zip> --into DIR` restores
into `DIR/<campaign>` and refuses an existing directory — restore never
overwrites a real campaign by default. Every member path is validated (no
traversal), every file is hash-verified against the manifest BEFORE
anything is written, and the restored campaign is opened with the app's
own loaders so a restore that cannot load fails loudly. The exclusion
list is two-way: archive members it excludes (`.git`, credential files at
any depth, forecasts, machine noise — even when a hand-zipped archive
hash-verifies them in the manifest) are skipped with a warning naming
them, so a hand-zipped copy of a live campaign restores to the same
shape a studio export would have.

## The job queue — set the evening up and walk away

A campaign has ONE FIFO job queue: a second mutation submitted while a week
runs answers 202 with a queue position and runs when its turn comes — no
more waiting to re-click. The confirm gates do not move (the spend is
approved at submit time, so a queued week needs no DM decision later);
queued jobs show their position in the UI, with a Cancel button, and the
strip under the header shows what is running and what is queued. Short
interactive writers (approvals, fact writes) still fast-fail under
contention instead of pinning your browser to a week run — but the 409 now
says what is running and how long it usually takes, from a small rolling
duration history kept per campaign (`.studio-jobs/history.json`,
git-ignored; weeks and forecasts scale by roster size). It is labeled an
estimate; the job log remains the truth.

Chains that would otherwise dead-end on a decision can opt into
**auto-accept**: backstory and missing-page generations, and the goal
review, then approve their own staged bundle through the same Review path
(same transactions, receipts, snapshots — the publication record says
`auto: true`, and error-level verification findings still leave the bundle
pending for a human). The week's auto-accept exists only behind a loud
checkbox, default off — publish the week without reading it first is a
deliberate act, and reading the chronicle is the point of the feature.
Session effects and project changes are never auto-accepted: they encode
DM rulings, not generation.

## The engines

The studio invokes three sibling projects as headless CLIs and never
absorbs them. Their docs:

- `../dnd-mapping/AGENTS.md` — worldgen, settlemaker city maps, battlemaps.
- `../agentic-simulator/AGENTS.md` — the faction week simulation.
- `../verification/AGENTS.md` — the prose verification ladder (`check.py`).
