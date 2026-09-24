# Campaign Studio — REST + frontend contract (v1)

The single coordination artifact for everyone building studio pieces.
The design doc is `../campaign-studio-design.md` (decisions D1–D7,
iterations M0–M5, guardrails). This file adds exact API shapes on top of it.
If code and this file disagree, fix the code or update this file in the same
change — never let them drift.

## Fixed decisions

- Root: the studio checkout's `studio/` directory (path-agnostic —
  `spine.py` resolves everything relative to itself). Campaigns live at
  `<checkout>/campaigns/<name>/` (user data separate from code).
- Server: FastAPI, bound `127.0.0.1:8765` (env `STUDIO_PORT` overrides).
  Boot with `python studio/run.py` (prints the URL, opens a browser);
  `python studio/server.py` is the bare uvicorn equivalent.
- Responses are JSON unless noted. Errors: `{"error": "..."}` with a 4xx/5xx
  status — including unhandled exceptions (a catch-all handler maps them to
  `{"error": "internal error: <type>"}`, 500).
- Jobs (D5): long work returns `{"job_id": "..."}` with status 202.
  `GET /api/jobs/{id}` → `{"id","kind","status":"queued|running|done|error",
  "log_tail":[str,...],"result":object|null,"error":str|null,"started":ts}`.
  UI polls ~1 s and gives up after 31 minutes (the job itself may still be
  running server-side). The jobs dict is in-process: finished records beyond
  the newest ~200 are dropped, and a finished job keeps only its log tail.
- Engine invocation (D3): subprocess with **list-form args, never `shell=True`**
  (the parent path contains `&`). Pattern: `[sys.executable, "-X", "utf8",
  "src/thing.py", ...]` with `cwd=<engine root>`. Node engines:
  `["node", "scripts/thing.mjs", ...]` with `cwd=dnd-mapping`. Capture stdout
  lines into the job's `log_tail`.
- Copy-in/copy-out (D4): the studio never refactors an engine's fixed paths.
  It copies campaign artifacts into the engine's home, invokes, copies results
  back into the campaign.
- Host allowlist (hardening): every route answers
  `403 {"error": "unknown host header"}` unless the request's Host is
  `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` — port = the
  bound port; the bare no-port forms are accepted too (DNS-rebinding
  protection). `STUDIO_EXTRA_HOSTS` (comma-separated) extends the list.
- Per-campaign mutation lock (hardening): one mutating job at a time per
  campaign. The long jobs — `POST .../simulation/week`, `POST .../world`,
  `POST .../maps/cities`, `POST .../maps/sites`, `POST .../lore/entries`,
  `POST .../maps/world-map` — take the lock at submit time and answer
  `409 {"error": "another job is already modifying this campaign"}` while it
  is held. Review approve/delete take the same lock blocking: they wait for
  an in-flight job instead of failing.
- Shared engine home (hardening): the simulator's
  `agentic-simulator/world/state.yaml` is ONE scratch file across campaigns.
  A second, process-wide lock (`engines.ENGINE_LOCK`) serializes every
  copy-in → engine-run → copy-out sequence: the week job holds it for its
  whole run; `GET .../simulation/views/{id}` and `POST .../simulation/why`
  hold it around their copy-in + subprocess. Lock order: campaign lock
  outside, engine lock inside.
- Canon snapshots (hardening): after every successful canon mutation the
  studio commits `campaigns/<name>` to the enclosing git repo (`git add -A`
  + `git commit --only` the campaign path, list-form args) with a
  `campaign/<name>: ...` message, ≤72 chars. Outside a git repo, or on any
  VCS failure, it is a logged no-op — the DM is never blocked on snapshots.

## Server layout and file ownership

| file | owner | notes |
|---|---|---|
| `server.py`, `spine.py`, `jobs.py`, `engines.py`, `canon.py`, `llm_client.py` | B (wave 1) | app, spine IO, job runner, subprocess wrappers, git snapshots, LLM client |
| `routers/campaigns.py`, `routers/system.py`, `routers/maps.py`, `routers/simulation.py` | B | wave-2 agents may extend, not restructure |
| `routers/lore.py` | F (wave 2) | B ships reads + 501 generation |
| `routers/review.py` | H (wave 3) | B ships 501 stub |
| `static/index.html`, `style.css`, `js/core.js` | C (wave 1) | page shell + `Studio` namespace |
| `static/js/world.js` | F (wave 2) | C ships complete-against-contract version |
| `static/js/maps.js`, `static/js/sim.js` | G (wave 2) | C ships ditto |
| `static/js/review.js` | H (wave 3) | C ships ditto |
| `assemble.py` | A (wave 1) | builds `campaigns/ruravaqan/` from engine artifacts |
| `doctor.py`, `run.py`, `start.bat`, `README.md` | H (wave 3) | boot experience |
| `tests/…` | B (+ F/G/H add their own files) | pytest + fastapi TestClient |

Wave-2/3 owners may replace their files wholesale; keep the route paths and
shapes in this document true.

## Routes

### Campaigns (B, M0)
- `GET /api/campaigns` → `{"campaigns":[{"name","title","created","has":{"world","lore","simulation","maps"}}]}`
- `POST /api/campaigns` `{"name": str, "title"?: str}` → 201 with the summary of a
  brand-new EMPTY campaign (directory, campaign.yaml, section dirs; title
  defaults to the name). The name must be a lowercase slug (a-z/0-9/hyphens,
  1-40 chars, no leading/trailing/double hyphens) — it becomes the directory
  and URL segment; 422 otherwise, 409 if the campaign exists. This is the one
  writer that starts from nothing: the world itself arrives via `POST .../world`.
- `GET /api/campaign/{name}` → summary:
  `{"name","title","world":{"nations":n,"burgs":[{"name","nation","population","x","y","port"}]},
  "lore":{"timeline_entries":n,"entities":n,"style_files":[str]},
  "simulation":{"clock":{"week","season"},"factions":[{"id","name","kind","resources"}],
  "rumors":n,"pending":n},"maps":{"cities":[str],"sites":[str]}}`
- `GET /api/campaign/{name}/geography` → full `world/geography.yaml` as JSON
  (World tab logistics grounding; burg pickers read this).

### Maps (B stub → G, M1; vault stage 3)
- `GET /api/campaign/{name}/maps` → `{"cities":[{"slug","label","svg","params","geojson"}],"sites":[…]}`;
  file URLs are `/campaign-file/<name>/maps/...`. The GET first performs the
  lazy site-slug migration: legacy `scene.png/svg/json` basenames are renamed
  to `<slug>.<ext>` (Obsidian resolves embeds by filename), snapshot
  `campaign/<name>: slug site artifact basenames (...)`.
- `POST /api/campaign/{name}/maps/cities` `{"burg": str, "place"?: str}` → job.
  `place` defaults to the burg. Takes the campaign lock (**409 busy**, see
  hardening above); on success copies outputs → `maps/cities/<slug>/`, writes
  the companion note `<slug>.md` (embed + `place:` wiki-link + provenance),
  stubs the place's entity page (`kind: place`) when no page resolves it, and
  commits snapshot `campaign/<name>: map city <slug>`.
  Engine: `node scripts/gen-city.mjs --world <path inside campaign world/> --burg "<name>" --out <tmp>`
  with `cwd=dnd-mapping`; copy outputs → `maps/cities/<slug>/`.
  Result `{"slug","svg":url}`. `--world` accepts anything import_fmg.py
  accepts (full JSON export, headless dump, or .map save) — a campaign's
  `world/source.json` qualifies.
- `POST /api/campaign/{name}/maps/sites` `{"kind":"dungeon|cave|camp","name":str,"seed"?:str,"place"?:str}` → job.
  Engine chain (cwd=dnd-mapping, list-form args): `python -X utf8
  src/battlemap.py gen-<kind> --seed <seed> --name "<name>"` →
  `... render --dir worlds/sites/<name>` →
  `node scripts/render-scene-png.mjs worlds/sites/<name>` (sharp) →
  `... wrap --dir worlds/sites/<name>`; artifact set
  {<slug>.json, <slug>.svg, <slug>.png, <slug>.uvtt} copied →
  `maps/sites/<slug>/` (+ a provenance `params.yaml` and the companion note
  `<slug>.md`; an optional `place` becomes the note's `place:` wiki-link —
  a name with no page stays dangling, which the review queue reports).
  Takes the campaign lock (**409 busy**); on success commits snapshot
  `campaign/<name>: map site <slug>`.
  `seed` defaults to crc32(slug) % 10^7 (recorded in params.yaml — every
  scene stays a derived artifact). Result
  `{"slug","label","svg":url,"png":url,"uvtt":url,"scene":url}`.
  A sharp/junction failure fails the job with the documented repair line
  (plain `npm install` in dnd-mapping/tools/settlemaker) in
  the error text (D7).
- Files: `GET /campaign-file/{name}/{path}` — StaticFiles mount on `campaigns/`,
  read-only intent, 404 outside the campaign tree.

### World generation (G, M1)
- `POST /api/campaign/{name}/world` `{"seed": str, "force"?: bool}` → job.
  **409 if `world/geography.yaml` already exists and `force` is not true.**
  Takes the campaign lock (**409 busy**, distinct from the force gate).
  On success commits snapshot `campaign/<name>: generate world (seed <seed>)`.
  422 unless seed matches `[A-Za-z0-9_-]{1,64}` (it becomes `FMG_SEED` and a
  filename). Job: headless FMG — `node node_modules/vitest/vitest.mjs run
  src/generators/headless-export.test.ts` with `FMG_SEED=<seed>`/`FMG_OUT=<tmp>`,
  cwd=dnd-mapping/tools/Fantasy-Map-Generator — then copy the dump →
  `world/source.json`, run `python -X utf8 src/import_fmg.py <dump>`
  (cwd=dnd-mapping; it writes `worlds/candidates/` per its documented D4
  behavior), copy the produced candidate yaml → `world/geography.yaml`, and
  render `world/map.svg` from the dump (studio/worldmap.py — read-only;
  headless FMG exports no svg, and regenerating in a browser would be a
  different world). Result `{"nations":n,"settlements":n,"map":url-or-null}`.
  Engine output streams into the job log.
- `POST /api/campaign/{name}/maps/world-map` → job. Renders
  `world/map.svg` from the **existing** `world/source.*` WITHOUT
  regenerating geography: `.map` saves yield the embedded FMG art
  (mode `fmg-embedded`); JSON dumps yield a schematic political atlas drawn
  from the dump's own polygons (mode `schematic-render`). 422 if the
  campaign has no `world/source.*`. Result `{"svg":url,"mode":str}`.

### Lore (B reads → F, M2; vault stages 1–3)
- `GET /api/campaign/{name}/lore` → `{"timeline":[entries],
  "entities":[entity pages],"style":{"names-shadian.txt":str,"voice.md":str}}`.
  Entities are the canonical vault pages (`lore/entities/*.md`) as
  `{id?,kind,name,aliases,note,body?,file}` — one markdown page per entity,
  frontmatter carrying the fields the old `entities.yaml` had. A legacy
  `entities.yaml` still reads; the first open of the campaign (summary or
  lore GET) migrates it to pages and deletes it, under the campaign lock,
  with snapshot `campaign/<name>: migrate entities.yaml to N entity pages`.
- `GET /api/campaign/{name}/lore/pages/missing` → `{"targets":[{"target":str,
  "sources":[campaign-relative files]}]}` — the dangling wiki-links grouped
  by name, most-linked first. Empty list = nothing to write. (Declared
  before the `{page}` route: "missing" is not a page stem.)
- `POST /api/campaign/{name}/lore/pages/missing` `{"targets"?: [str]}` → job
  (fed-or-refused, vault stage 4): each dangling link is a generation target
  whose grounding is the pages that link it — those pages are fed whole.
  **422-equivalent refusal (job error) when there are no dangling links** or
  none of the requested targets is one. Drafts stage to
  `lore/pending-pages-N.yaml` (entry shape below) and the run attaches
  structural + canon-audit findings; commits snapshot
  `campaign/<name>: stage N pending lore pages`.
  Result `{"pending":str,"pages":n,"findings":[…]}`.
- `GET /api/campaign/{name}/lore/pages/{page}` → one entity page plus its
  computed backlinks: `{id?,kind,name,aliases,note,body,file,
  "backlinks":[{"source_file","source_kind":"entity|chronicle|map",
  "source_name","line","target"}]}`. `{page}` is the filename stem
  (display name, URL-encoded). 404 for an unknown page; 422 for a
  non-page-shaped name. Backlink sources: entity pages, chronicles, and
  the maps' companion notes.
- `PUT /api/campaign/{name}/lore/entities` — RETIRED with `entities.yaml`
  (405): pages are hand-edited in Obsidian, or created by migration/stubbing,
  and a form over the wrong file would be a lie.
- `PUT /api/campaign/{name}/lore/style/{file}` `{"text":str}` → write (filename
  constrained to `[A-Za-z0-9._-]`, must exist already); commits snapshot
  `campaign/<name>: update style/<file>`.
- `POST /api/campaign/{name}/lore/style/{file}` `{"text":str}` → create (201)
  a style file that does not exist yet — the staging path for name seed
  lists from the UI. **409 if it exists** (edit via PUT instead). Name
  lists (`names-*.txt`) must carry ≥3 non-comment lines or **422** (an
  empty seed list would send the LLM in with nothing in-voice). Commits
  snapshot `campaign/<name>: stage style/<file>`.
- `POST /api/campaign/{name}/lore/entries` `{"focus"?:str}` → job.
  **Refuses 422 if `world/geography.yaml` is missing** (fed-or-refused,
  guardrail 4). LLM prompt constrained to geography + timeline + entities
  (whole-file loads, no retrieval); output staged to
  `lore/pending-entries-N.yaml`; structural findings from
  `verification/check.py` attached; after staging, commits snapshot
  `campaign/<name>: stage N pending lore entries`.
  Result `{"pending":str,"entries":n,"findings":[…]}`.
- `POST /api/campaign/{name}/lore/name` `{"culture":str,"count":int,"hint"?:str}`
  → sync `{"names":[str]}` (name list + voice.md in prompt; soft guarantee —
  the UI says "in-voice, not guaranteed").

### Simulation (B reads/week → G, M3)
- `GET /api/campaign/{name}/simulation` → `{"clock":{"week","season"},
  "premise":str,"factions":[full dicts],"events":[…],"rumors":[…],
  "chronicles":[{"week","title","file"}],"views_available":[ids],
  "pending_proposal":id|null}`.
  **404 is a normal state for a fresh campaign** (no state.yaml yet — no
  factions exist); the UI renders it as the create-a-simulation form, not
  an error.
- `POST /api/campaign/{name}/simulation/state` → 201, author
  `simulation/state.yaml` from the UI (the fresh-campaign path; no LLM).
  Body: `{"premise":str, "season"?:str="early spring",
  "city"?:{"name":str,"population"?:int},
  "factions":[2-5 × {"name":str,"kind"?:str,"goal":str,
  "resources"?:{"coin|muscle|legitimacy":"none|low|medium|high"}}],
  "force"?:StrictBool=false}`. Faction ids are derived from names
  (underscore slugs — tick.py's HOLDS log lines parse `[a-z_]+`);
  missing resources default to `low`; ids must be unique and `world` is
  reserved. **409 when state.yaml exists without `force`** (replacing a
  sandbox rewrites its history). Clock starts at week 0: tick.py
  increments before running, so the first live week is week 1. Commits
  snapshot `campaign/<name>: simulation created|replaced`.
  Returns `{"ok":true,"week":0,"factions":[ids]}`.
- `GET /api/campaign/{name}/simulation/views/{faction_id}` → the fog-of-war
  `faction_view` dict as JSON. Engine: `python -X utf8 src/views.py <faction_id>`
  (cwd=agentic-simulator; E adds this CLI, printing YAML).
- `POST /api/campaign/{name}/simulation/week` `{"confirm":true}` → job.
  **422 unless `confirm` is exactly true** (D5: the expensive button).
  **409 when a pending proposal exists** (accept or discard it first) or a
  recovery conflict is parked. Takes the campaign lock (**409 busy** while
  another mutating job runs). Interrupted-run detection: the job writes
  `agentic-simulator/world/.week-run-flag` (JSON: campaign + ISO timestamp)
  before copy-in and removes it in a `finally`; if the server died mid-run,
  the next boot restores the engine home's `world/state.yaml` from
  `state.yaml.bak` (when present), deletes the flag, and logs "interrupted
  week run for campaign X — engine home restored". Copy-in: campaign
  `simulation/state.yaml` → `agentic-simulator/world/state.yaml`.
  Run: `python -X utf8 src/tick.py --live`. **Copy-out does NOT touch the
  campaign**: the engine's output is staged as a proposal bundle under
  `simulation/proposals/<id>/` (proposed state.yaml, chronicle files,
  timeline entries, structured diff.json, run_record with the week's RNG
  seed and model identity — no secrets). Result
  `{"proposal_id","status":"pending","week","base_week","chronicle":{...},
  "held":[...],"timeline_entries":n,"diff":{...}}`. Invalid candidates are
  job errors, never bundles: engine failure, clock not advancing exactly
  one week, no chronicle written, or every faction call failed. A genuinely
  quiet scheduler-held week is a valid proposal.
- `GET /api/campaign/{name}/simulation/proposal` → the pending bundle
  (`{"proposal":null}` when none): `{"proposal":{"proposal_id","status",
  "base_week","proposed_week","created","run_record","base_revisions",
  "input_set_revisions"},"diff":{clock,resources,events_added,
  rumors_added,timeline_entries},"entries":[...],"chronicles":[{file,title,
  markdown}]}`. Reads disk, so review survives a restart.
- `POST /api/campaign/{name}/simulation/proposal/{pid}/accept` → publishes
  the WHOLE week (state, chronicles, timeline entries) as one transactional
  publication validated against the base revisions recorded at generation
  time — an external edit since generation is a 409 (discard and
  regenerate; nothing written). Advances the clock exactly once; repeating
  the accept returns the recorded result (`"idempotent":true`). Commits
  snapshot `campaign/<name>: week N — <title>`. Chronical linkify runs
  after publication (advisory decoration, as before).
- `POST /api/campaign/{name}/simulation/proposal/{pid}/discard` → flips the
  bundle's status to `discarded`; live files were never touched. The bundle
  stays on disk as an audit receipt; rejected content is never fed into
  future prompts.
- `POST /api/campaign/{name}/simulation/session` `{"source":str,
  "week"?:int, "events"?:[{"actor":"world"|faction,"text":str}],
  "effects"?:[{"type":"set_resource","faction_id","resource":
  "coin|muscle|legitimacy","after":"none|low|medium|high","reason":str,
  "expected_before"?:str,"id"?:str} | {"type":"set_field","faction_id",
  "field":"goal|kind|secret","after":str,"reason":str,...}]}` → 202,
  stages a `session_effects` proposal. Events are HISTORY ONLY (recorded
  verbatim, no mechanical effect); effects are the reviewed state changes,
  validated by the engine's own code via CLI (`src/effects.py --check`,
  list-form args) — 422 with the engine's refusal list when invalid.
  Effect ids are content-derived when omitted, so re-submitting the same
  session cannot mint a second identity. The after-image is produced by
  applying to a throwaway copy of the state; the campaign is untouched
  until the accept route publishes it. Accept dispatches on the bundle's
  `kind`: a session_effects publish writes `simulation/state.yaml` only.
- Legacy `simulation/pending-week-*.yaml` files (pre-proposal
  implementations) remain reviewable as **timeline-only** reviews: the
  weeks they describe were already committed by the old flow — approving
  writes timeline entries only (never re-applies effects), deleting one
  does not undo a week. Review GET marks them `"legacy": true`.
- `GET /api/campaign/{name}/simulation/chronicle/{week}` → `{"week","title","markdown"}`.
- `POST /api/campaign/{name}/simulation/why` `{"week":int,"faction":str}` → sync.
  Engine: `python -X utf8 src/why.py <week> <faction> --json` (E adds the flag).
  Passthrough `{"week","faction","lines":[str]}` — lines render as an indented
  causal chain.

### Review (B stub → H, M4; vault stage 4; phase-1 publication)
- `GET /api/campaign/{name}/review` → `{"items":[{"id","kind":
  "lore-entry|lore-page|timeline-approval|finding","source","title","body",
  "findings":[…],"file"}]}` — the union of `lore/pending-*.yaml`,
  `simulation/pending-week-*.yaml`, and verification `findings.json` if
  present, PLUS the vault's own advisory findings (`source: "links"`):
  dangling wiki-links and name/alias collisions across entity pages,
  chronicles, and companion notes — capped at 20 per GET, with a note when
  more exist. Pending entries carrying a `page` payload surface as
  `kind: "lore-page"` (the missing-page drafts). Each `files[]` row carries
  `revision` (sha256 of the file's exact bytes) — send it back as
  `expected_revision` so a change since the view is refused (409) with
  nothing written.
- `POST /api/campaign/{name}/review/approve` `{"file":str,"ids":[str],
  "expected_revision"?:str}` or `{"file":str,"all":true,
  "expected_revision"?:str}` → entries append into `lore/timeline.yaml`
  (entries keep their `source`); page drafts write into
  `lore/entities/<Title>.md` (create-only — an existing page wins and the
  draft is discarded, counted in `pages_skipped`); approved entries'
  validated `new_names` are stubbed as pages (`kind: unknown`) so the graph
  grows with the story → `{"approved":n,"entries":n,"pages":n,
  "pages_skipped":n,"stubs":n,"transaction":id,"snapshot":{...}}`. The
  whole approval (timeline + pending rewrite + pages + stubs) is ONE
  transactional publication (studio/publication.py): staged first, applied,
  verified; a crash leaves an open transaction that boot recovery rolls
  forward, or parks as a conflict (409 on later publications) when a file
  was edited externally. Generated drafts carry `base_revisions` /
  `input_fingerprints` (what the generator read); a stale source or a
  changed entity-pages membership refuses the approval (409, regenerate).
  Repeating the same request returns the recorded result with
  `"idempotent": true` instead of applying twice. Takes the shared campaign
  lock **blocking** (waits for an in-flight week/generation job), then
  commits snapshot `campaign/<name>: approve N pending entries
  (+K page(s)/stub page(s))`.
- `POST /api/campaign/{name}/review/delete` same body → `{"deleted":n,
  "transaction":id,"snapshot":{...}}`; same lock, same transactional
  publication and receipts.
- `snapshot` in approve/delete/week results is
  `{"state":"committed|no_change|unavailable|failed","commit","detail"}` —
  `committed` is the only state with an undo point; `unavailable` (git
  missing) and `failed` (git ran and failed) retain the publication payload
  for a retry.
- `POST /api/campaign/{name}/snapshot` → retries the history snapshot and
  prunes retained publication payloads on success.
- `POST /api/campaign/{name}/publication/resolve`
  `{"transaction":str,"resolution":"apply"|"discard"}` → resolves a
  conflict-parked transaction: `apply` forces the staged outcome over the
  external edit (an explicit DM choice — recovery never does this alone),
  `discard` abandons the transaction and keeps the live bytes. 409 while
  the transaction is not conflict-parked.

### Search (H)
- `GET /api/campaign/{name}/search?q=<str>` → line-by-line, case-insensitive
  substring grep over the campaign's text files: `lore/*.yaml`,
  `lore/entities/*.md`, `lore/style/*`, `simulation/chronicle/*.md`,
  `simulation/pending-*.yaml`, `world/geography.yaml`, `campaign.yaml`. →
  `{"q": str, "results": [{"file": campaign-relative posix path,
  "kind": "chronicle|timeline|entities|style|geography|pending|campaign",
  "line": n, "text": str (trimmed to ~200 chars), "context": str}]}`
  — `context` is the previous non-empty line when it looks like a
  title/heading (markdown `#` heading, a YAML key ending in `:`, or a
  `- id:` line), else `""`. Capped at 50 results; `q` missing, empty, or
  whitespace-only → 422; unknown campaign → 404. Lives in
  `routers/system.py`.

### System (B)
- `GET /api/health` → `{"ok":true,"version":"0.1.0",
  "week_run_in_progress":bool}` — the flag is true while
  `agentic-simulator/world/.week-run-flag` exists (a week job is running,
  or a crashed one has not been restored by the boot hook yet).
- `GET /api/doctor` → the full `doctor.py` JSON (key diagnosis, tool-tree
  health, the `week run` check reports an in-progress/interrupted week
  (`agentic-simulator/world/.week-run-flag`); the `vault links` check counts
  dangling wiki-links and name/alias collisions across campaigns — advisory,
  never required. `doctor.py --fix` (CLI only, not an API) performs the safe
  repairs: `npm install` in a broken tool tree (FMG's adds
  `--ignore-scripts` — see D7), and
  `pip install -r studio/requirements.txt`.
- `GET /api/doctor?quick=1` → same shape, but the LLM key check is
  presence-only (no live probe, no tokens, no network wait) — what the
  page's setup strip polls on boot.
- `GET /tutorial` → the repo-root `TUTORIAL.md` as `text/plain` (the help
  modal links here); 404 with a pointer to the repo root if the file is
  missing.

### Shared file shapes
Timeline entry: `{"id","when","title","text","source"}` with
`source ∈ {authored, session, sim:week-N}`; ids `t0001`… increasing.
Entity page `lore/entities/<Display Name>.md`: YAML frontmatter
(`id?/kind/name/aliases`) + markdown body; the first body paragraph is the
`note` the loaders return. Wiki-links (`[[Name]]`, `[[target|display]]`)
are the relationship model — dangling ones are findings, never errors.
Pending lore file `lore/pending-entries-N.yaml`:
`{"entries":[entry,…],"created":"ISO-8601","focus":str}`.
Sim timeline proposal `simulation/pending-week-N.yaml` (staged
automatically after a successful week job — the DM edits/approves via
Review; ids `pw<N>-<k>`):
`{"created":"ISO-8601","week":N,"entries":[
  {"id":"pw<N>-<k>","when":"week N","title":<first 4-7 words of the event
  text, cut at punctuation>,"text":<full event text>,"source":"sim:week-N"},
  …]}` — one entry per NON-WORLD event of the week; re-staging a week
overwrites the file (it is derived; approved entries live in the timeline).
Pending page draft `lore/pending-pages-N.yaml` (staged by the missing-pages
job): `{"created":"ISO-8601","mode":"missing-pages","entries":[
  {"id":"page-N","title":<the missing name>,"text":<body 1-3 sentences>,
   "source":"authored","page":{"kind":str,"aliases":[str]},"new_names":[str]},
  …]}` — approving an entry writes `lore/entities/<Title>.md`; deleting
discards it.
Chronicle linkifying (vault stage 4): after a week copies its new
chronicles into the campaign, the FIRST occurrence of each known entity
name/alias per file is wrapped in a wiki-link (headings and roll tables are
left alone, and nothing inside an existing link is touched). The studio's
markdown renderer shows the display text; Obsidian gets the real links.

## Frontend contract (C, M0)

One page (guardrail 6). `index.html` loads, in order, plain scripts
(no modules, no build): `js/esc.js, js/logtail.js, js/core.js, js/maps.js,
js/world.js, js/sim.js, js/review.js` (`esc.js`/`logtail.js` are UMD and
double as CommonJS modules for the Node suites `studio/tests/js/`).

`js/core.js` defines `window.Studio`:
- `Studio.api(path, opts)` → fetch JSON; throws `{status, error}` on !ok.
- `Studio.pollJob(id, onLog)` → Promise<result>; 1 s interval, shows `log_tail`.
- `Studio.md(text)` → minimal safe markdown→HTML (escape first; headings,
  bold/italics, tables, hr, paragraphs — chronicles have tables).
- `Studio.el(html)` → element from template string.
- `Studio.confirmRun(message)` → Promise<bool> modal (expensive buttons).
- `Studio.toast(msg, kind)`.
Tab files register `Studio.tabs.<name> = async (container) => {...}`; core.js
handles nav clicks and calls the active tab's renderer.

Tab behaviors (build complete against this contract; a generation button may
hit a 501 until wave 2 — render the error inline, never console-only):
- **Maps**: world map (`world/map.svg`) hero; cities grid (SVG via `<img>`).
  "Map a burg" form: burg dropdown (from geography; port flag shown, harbour
  handled engine-side), an "attach to place" input defaulting to the burg,
  submit → job → poll → refresh (companion note + place stub server-side).
  Sites form (kind + name + optional place), same.
- **World Building**: geography digest (nations + burgs tables); timeline with
  source badges; entities table (page-backed rows click through to a page
  view with computed backlinks); style viewer (textarea, PUT save);
  "Generate backstory" (focus input) → pending → point at Review tab;
  "Name helper" (culture + count) → list; "Write the missing pages" card
  (dangling-link pills → confirm → job → pending page drafts).
- **Agent Simulation**: clock + premise; faction cards (resource chips,
  disposition, secret behind `<details>`); rumor board (claim, fidelity badge
  fresh/reliable/hearsay/dead, born week, heard-by count; a trailing
  ` — succeeded|failed…` ledger suffix is stripped from the claim for
  DISPLAY only — hover the claim to see the full recorded text, and the
  raw state keeps it); events list;
  chronicle reader (week picker → `Studio.md`); views inspector (faction
  picker → YAML `<pre>`); why inspector (week + faction → indented lines);
  "Run week N" behind `Studio.confirmRun("This runs a real week — it spends
  API credits and advances the canon clock.")` with live log tail.
- **Review**: items with kind badge, provenance, findings inline,
  approve/delete buttons.

Header (all tabs): a search box. Enter fires
`GET /api/campaign/{name}/search?q=…` for the selected campaign and
renders the hits in a dismissible panel (kind badge, file:line, the
matched line with the match highlighted, optional heading context); Esc,
the X button, or a campaign switch closes it.

Style: one consistent tabletop look (parchment-and-ink or tasteful dark —
pick one), system font stack, **no external network resources** (no CDN
fonts/scripts; localhost app must work offline). Never distort SVGs.
