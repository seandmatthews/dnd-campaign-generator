# Campaign Studio — REST + frontend contract (v1)

The single coordination artifact for everyone building studio pieces.
The design doc is `../docs/design/campaign-studio-design.md` (decisions D1–D7,
iterations M0–M5, guardrails). This file adds exact API shapes on top of it.
If code and this file disagree, fix the code or update this file in the same
change — never let them drift.

## Fixed decisions

- Root: the studio checkout's `studio/` directory (path-agnostic —
  `spine.py` resolves everything relative to itself). All runtime data
  lives under `<checkout>/campaign-data/` (git-ignored; `$CAMPAIGN_DATA_ROOT`
  overrides): campaigns at `campaign-data/campaigns/<name>/`, the shared
  engine home at `campaign-data/engine-home/`, chronicle output at
  `campaign-data/chronicle/`, battlemap scratch at `campaign-data/maps/`,
  logs at `campaign-data/logs/` (user data separate from code).
- Server: FastAPI, bound `127.0.0.1:8765` (env `STUDIO_PORT` overrides).
  Boot with `python studio/run.py` (prints the URL, opens a browser);
  `python studio/server.py` is the bare uvicorn equivalent.
- Responses are JSON unless noted. Errors: `{"error": "..."}` with a 4xx/5xx
  status — including unhandled exceptions (a catch-all handler maps them to
  `{"error": "internal error: <type>"}`, 500).
- Jobs (D5): long work returns `{"job_id": "..."}` with status 202.
  `GET /api/jobs/{id}` → `{"id","kind","campaign","status":"queued|running|
  done|error|cancelled","log_tail":[str,...],"result":object|null,
  "error":str|null,"started":ts}` — plus `"position": N` while the job sits
  in a campaign queue. UI polls ~1 s and gives up after 31 minutes (the job
  itself may still be running server-side). The jobs dict is in-process:
  finished records beyond the newest ~200 are dropped, and a finished job
  keeps only its log tail.
- Campaign job queue (ticket 02): a mutating job submitted while the
  campaign already has one queued/running answers 202 with
  `{"job_id", "queued": true, "queue_position": N}` (N = how many jobs must
  finish first; `queued: false, queue_position: 0` = nothing ahead — though
  an externally held lock, e.g. a CLI process, is reported as position 1)
  and a per-campaign FIFO worker drains them strictly in order. The confirm
  gates do not move: `confirm: true` is given at submit time, so a queued
  week already carries the DM's spend approval. Queue routes:
  `GET /api/campaign/{name}/jobs` → `{"campaign","running":job|null,
  "queued":[job...],"running_estimate":{median_s,low_s,high_s,samples,
  roster}|null}` (the strip the UI polls); `POST /api/jobs/{id}/cancel`
  dequeues a QUEUED job → 200 with the record (status `cancelled`); a
  running/finished job is `409 {"error": "job ... is not queued — ..."}`,
  an unknown id 404.
- Auto-accept (ticket 02): `POST .../lore/entries`,
  `POST .../lore/pages/missing`, and `POST .../simulation/goal-review`
  accept an opt-in `"auto_accept": bool` (default false): the job then
  approves its own staged bundle through the same approve/accept path the
  UI uses, and the publication carries `auto: true` in the transaction
  manifest, in the receipt, and as "(auto-accepted)" in the snapshot
  message. Error-level verification findings block the auto-accept and
  leave the bundle pending, as today; the job result says
  `"auto_accept": "approved"|"blocked"|"error"`.
  `POST .../simulation/week` takes `"auto_accept"` as a StrictBool default
  OFF (publish the week without reading it — loud checkbox only).
  `session_effects` / `project_changes` are never auto-accepted (they are
  synchronous routes encoding DM rulings).
- Wait estimates (ticket 02): finished job durations roll into
  `<campaign>/.studio-jobs/history.json` (git-ignored, beside the
  transactions dir; median of the last ~6 runs, roster-scaled for
  weeks/forecasts, clamped). A 409 that cannot be queued — the shared
  engine home busy with ANOTHER campaign's run (a same-campaign
  week/forecast joins the campaign queue instead, ticket 24), or a short
  interactive writer racing a job — appends
  `" Blocked by: <what is running> — typically <low>-<high> (an estimate
  from past runs; the job log is the source of truth)"` to its error, or
  admits "no duration history yet" when nothing has finished. Estimates
  are labeled as estimates; the job log remains the source of truth.
- Engine invocation (D3): subprocess with **list-form args, never `shell=True`**
  (the parent path contained `&` until the 2026-10-04 rename). Pattern: `[sys.executable, "-X", "utf8",
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
- Per-campaign mutation lock (hardening, amended by ticket 02): one
  mutating job at a time per campaign. The long jobs — `POST
  .../simulation/week`, `POST .../world`, `POST .../maps/cities`,
  `POST .../maps/sites`, `POST .../lore/entries`, `POST .../maps/world-map`
  — no longer fast-fail while the lock is held: they QUEUE (see the
  campaign job queue above) and the per-campaign worker takes the lock when
  the job's turn comes. Every SHORT interactive writer — review
  approve/delete, proposal accept/discard, `POST .../simulation/session`,
  knowledge writes (record/supersede/reconcile/reveal/revoke), publication
  resolve, snapshot retry, sandbox create — takes the same lock
  non-blocking: under contention it answers `409 {"error": "<action> cannot
  run right now: another job is already modifying this campaign Blocked by:
  <what is running> — typically <how long, an estimate> — retry when it
  finishes. Nothing was changed."}` instead of pinning the HTTP request
  behind a 30-minute week (a browser timeout on the way would turn the
  would-be success into an error).
- Shared engine home (hardening): the simulator's
  engine-home `state.yaml` (`campaign-data/engine-home/`) is ONE scratch
  file across campaigns.
  A second, process-wide lock (`engines.ENGINE_LOCK`) serializes every
  copy-in → engine-run → copy-out sequence: the week job holds it for its
  whole run; views and why read the campaign's own canon via `--state` and
  take no lock. The lock records its HOLDER campaign (ticket 24): the
  submit-time probes in week/forecast 409 only when the holder is a
  DIFFERENT campaign (or an unnamed one) — a same-campaign holder means the
  request queues behind its own run, which is what the per-campaign queue
  is for. Lock order: campaign lock outside, engine lock inside. The
  doctor's simulator probe is skipped while a week is in flight (run flag OR
  engine lock held).
- Canon snapshots (hardening): after every successful canon mutation the
  studio commits `campaign-data/campaigns/<name>` to the enclosing git repo (`git add -A`
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
| `assemble.py` | A (wave 1) | builds `campaign-data/campaigns/ruravaqan/` from engine artifacts |
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
  Engine chain (cwd=dnd-mapping, list-form args; every subprocess gets
  `CAMPAIGN_DATA_ROOT` in its env): `python -X utf8
  src/battlemap.py gen-<kind> --seed <seed> --name "<name>"` →
  `... render --dir <campaign-data>/maps/sites/<name>` →
  `node scripts/render-scene-png.mjs <same dir>` (sharp) →
  `... wrap --dir <same dir>`; the chain runs in the data root's
  maps/sites home; artifact set
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
- Files: `GET /campaign-file/{name}/{path}` — StaticFiles mount on the
  campaigns root (`campaign-data/campaigns/`),
  read-only intent, 404 outside the campaign tree.

### World generation (G, M1)
- `POST /api/campaign/{name}/world` `{"seed": str, "force"?: bool}` → job.
  **409 if `world/geography.yaml` already exists and `force` is not true** —
  and the gate is RE-CHECKED inside the queued job under the campaign lock
  (ticket 24): a create-only job that finds a world on disk (one landed
  while it sat in the queue) refuses as a job error with the same message,
  nothing run or spent; a forced job still replaces.
  Takes the campaign lock (**409 busy**, distinct from the force gate).
  On success commits snapshot `campaign/<name>: generate world (seed <seed>)`.
  422 unless seed matches `[A-Za-z0-9_-]{1,64}` (it becomes `FMG_SEED` and a
  filename). Job: headless FMG — `node node_modules/vitest/vitest.mjs run
  src/generators/headless-export.test.ts` with `FMG_SEED=<seed>`/`FMG_OUT=<tmp>`,
  cwd=dnd-mapping/tools/Fantasy-Map-Generator — then copy the dump →
  `world/source.json`, run `python -X utf8 src/import_fmg.py <dump>`
  (cwd=dnd-mapping; it drops candidate yamls under the data root's
  `scratch/map-candidates/` per its documented D4
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
- `GET /api/campaign/{name}/lore/path` → `{"path":str,"obsidian_uri":str}` —
  the resolved absolute lore folder and the `obsidian://open?path=<url-encoded
  folder>` deep link built from it (the browser cannot know the absolute
  campaign path). A read-only pointer for the DM's outside editor; there is
  no write route here and none is planned (the no-in-app-editor ruling).
- `GET /api/campaign/{name}/lore/pages/missing` → `{"targets":[{"target":str,
  "sources":[campaign-relative files]}]}` — the dangling wiki-links grouped
  by name, most-linked first. Empty list = nothing to write. (Declared
  before the `{page}` route: "missing" is not a page stem.)
- `POST /api/campaign/{name}/lore/pages/missing` `{"targets"?: [str]}` → job
  (fed-or-refused, vault stage 4): each dangling link is a generation target
  whose grounding is the pages that link it — those pages are fed whole, and
  names appearing in them count as grounded (a draft repeating its own
  grounding is not a hallucination; 2026-09-25). Total validation failure
  reports the findings in the job error and log instead of swallowing them.
  Asset embeds (`![[scene.png]]`) are file references, not page links: they
  are never dangling targets. **422-equivalent refusal (job error) when
  there are no dangling links** or
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
- `POST /api/campaign/{name}/simulation/design` → premise-to-sandbox
  ideation (one metered LLM pass, sync like `lore/name`). Body:
  `{"premise":str, "places"?:[str]}`. **Fed-or-refused**: 422 when
  `world/geography.yaml` is missing AND no places were named (generation
  grounded in nothing does not run). The reply is validated against the
  existing schemas — the form's premise rule (one line ≤600),
  `spine.validate_faction_docs` for the 2-5 factions, and the ENGINE's
  project normalizer (`src/projects.py --check` against a candidate state
  in a temp dir) for suggested projects; a failing reply is re-asked ONCE
  with the validator's words, then **502** carrying them. Returns
  `{"design":{"premise","factions":[form-shaped {name,kind,goal,resources,
  place?}],"projects":[{faction,faction_id,title,objective,target}],"secrets":
  [{faction,text}]},"notes":[...],"usage":{calls,tokens,limit}}`. The design
  is PREFILL material only: nothing is staged, `simulation/state.yaml` is
  never touched — Create (`POST .../simulation/state`) stays the accept on
  the unchanged free path. Secrets are proposed DM-only knowledge-fact
  drafts and never part of the faction docs or any state payload. CLI
  equivalent: `python studio/simdesign.py <campaign> --design --premise
  "..." [--place NAME]... [--out FILE]` (same service; exit 0/1/2).
- `POST /api/campaign/{name}/simulation/week` `{"confirm":true}` → job.
  **422 unless `confirm` is exactly true** (D5: the expensive button).
  **409 when a pending proposal exists** (accept or discard it first) or a
  recovery conflict is parked. Takes the campaign lock (**409 busy** while
  another mutating job runs). Interrupted-run detection: the job writes
  `campaign-data/engine-home/.week-run-flag` (JSON: campaign + ISO timestamp)
  before copy-in and removes it in a `finally`; if the server died mid-run,
  the next boot restores the engine home's `state.yaml` from
  `state.yaml.bak` (when present), deletes the flag, and logs "interrupted
  week run for campaign X — engine home restored". Copy-in: campaign
  `simulation/state.yaml` → `campaign-data/engine-home/state.yaml`.
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
  (`{"proposal":null}` when none): `{"proposal":{"proposal_id","kind",
  "status","base_week","proposed_week","created","run_record",
  "base_revisions","input_set_revisions (advisory: what the run READ, not enforced at accept)"},"diff":{clock,resources,fields,
  events_added,rumors_added,timeline_entries},"entries":[...],
  "updates":[...],"findings":[...],"chronicles":[{file,title,markdown}]}`.
  `kind` is `simulated_week` | `session_effects` | `goal_updates` |
  `project_changes`;
  `updates` is the goal-update list on a goal_updates bundle,
  `changes` the normalized operation list on a project_changes bundle, and
  `findings` the validation findings (updates the engine cut and why —
  also persisted on zero-update runs); `diff.fields` carries
  `{faction:{goal|kind|secret:{before,after}}}` for any set_field change
  and `diff.projects_added`/`diff.projects_changed` every project movement
  (progress steps, status flips, term edits, completion blocks).
  A damaged bundle answers 422 naming the bundle (discard still works) —
  not a raw 500. Reads disk, so review survives a restart.
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
  "field":"goal|kind|secret","after":str,"reason":str,...}]}` → 200
  (synchronous — the engine validation runs in-request),
  stages a `session_effects` proposal. Events are HISTORY ONLY (recorded
  verbatim, no mechanical effect); effects are the reviewed state changes,
  validated by the engine's own code via CLI (`src/effects.py --check`,
  list-form args) — 422 with the engine's refusal list when invalid.
  Project effects (`set_project_progress` with an integer `after` clamped
  to `0..target`, `set_project_status` with `active|paused|abandoned`) key
  on `project_id` instead of `faction_id`; `completed` is unreachable by
  text — reaching target runs the same completion pass a weekly tick uses.
  Effect ids are content-derived when omitted, so re-submitting the same
  session cannot mint a second identity. The after-image is produced by
  applying to a throwaway copy of the state; the campaign is untouched
  until the accept route publishes it. Accept dispatches on the bundle's
  `kind`: a session_effects publish writes `simulation/state.yaml` only.
- `POST /api/campaign/{name}/simulation/goal-review` → 202 job. The sim
  proposes faction goal updates grounded in the ledger (recorded weeks,
  rumors, canon deadlines — engine `src/goals.py --state`, one bounded
  LLM pass; per-update findings are dropped and reported). Proposed
  updates ride the same reviewed effect path a DM-recorded session takes
  (`src/effects.py --check`/`--apply` with `set_field goal`), then stage
  as a **`goal_updates` proposal**. 409 while any proposal is pending; a
  state edit landing mid-run aborts with nothing staged (the after-image
  would not match canon). Zero proposed updates is a done job with
  `"staged":false` plus the findings — no empty bundle. Result (staged)
  `{"proposal_id","status":"pending","kind":"goal_updates","proposed":n,
  "findings":[...],"staged":true,"diff":{...}}`. Accept applies each goal
  as one sourced audit event (source `goal-review:week-N`) and writes
  `simulation/state.yaml` only.
- `GET /api/campaign/{name}/simulation/projects` → the engine's own DM
  report (`src/projects.py --report`): every project normalized with
  computed requirement findings, progress label, and any persisted
  `completion_blocked` finding. 502 when the engine fails.
- `GET /api/campaign/{name}/simulation/projects/{project_id}` → one
  project's detail plus its sourced event history (every event carrying
  its `project_id`, week-ordered). 404 unknown id.
- `POST /api/campaign/{name}/simulation/projects/prepare`
  `{"operations":[{"op":"create|edit|pause|resume|abandon","reason":str,
  "project"?:{...},"project_id"?:str,"set"?:{...}}]}` → 200 (synchronous,
  no LLM), stages a **`project_changes` proposal**. The engine validates
  the whole batch against a captured copy (`src/projects.py --preview`;
  rc 1 = refused whole, 422 with its findings — no partial edits) and
  produces the after-image. 409 while any proposal is pending or the
  state changed mid-prepare (same revision gate as sessions). Accept
  writes `simulation/state.yaml` only; completing edits run the same
  deterministic completion pass as weekly ticks.
- `POST /api/campaign/{name}/simulation/forecast` `{"confirm":true,
  "weeks":2|3}` → 202 job. `confirm` must be exactly `true` (422 otherwise
  — same expensive-button gate as a week). **Forecast: propose-and-discard, never canon.**
  Runs the REAL engine forward N weeks on scratch data (copy-in, then
  `src/tick.py --live` N times under the engine lock; the same
  interrupted-run flag/backup recovery as a week). Per projected week the
  clock must advance exactly one, a chronicle must be written, and "every
  faction call failed" aborts — an outage is not a forecast. The result
  stages a bundle under `simulation/forecasts/<id>/` (git-ignored: a
  projection is not canon and not undo-trail material) and never occupies
  the pending-proposal slot — a real week can be generated while a
  forecast is on the table, and there is no accept. 409 while a pending
  proposal exists (the forecast reads the canon that proposal would
  change), while another forecast is active, or the engine home is busy
  with ANOTHER campaign's run (this campaign's own week/forecast holding
  it means the forecast queues behind it, ticket 24).
  Result `{"forecast_id","status":"active","base_week","weeks":[{week,
  title,markdown,diff}]}`. `GET .../forecast` answers 422 naming a damaged
  bundle (discard still works) instead of a raw 500. Two queued engine
  jobs (a week behind a forecast) share the run flag safely: each clears
  only its own marker, so a crash during the second run still boots into a
  restore.
- `GET /api/campaign/{name}/simulation/forecast` → `{"forecast":null}` or
  `{"forecast":{forecast_id,status,base_week,created,run_record},
  "weeks":[{week,title,markdown,diff}]}` (reads disk — survives a
  restart; also surfaced as `active_forecast` in `GET .../simulation`).
- `POST /api/campaign/{name}/simulation/forecast/{fid}/discard` → flips
  the bundle's status to `discarded`; live files were never touched and
  the bundle stays as a receipt. Discarded projections are never fed
  into future prompts.
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

### Sessions (session-notes-to-changes — reviewed campaign changes from play notes)
- Layout: `sessions/<session-id>/session.yaml` + immutable
  `notes/0001.md` revisions + `drafts/<draft-id>.yaml` +
  `decisions/<proposal-id>.yaml`. DM material — outside every lore
  generation glob; included in the private campaign export. Ids are
  server-generated (`session-`/`draft-`/`suggestion-` + hex); an
  extraction retry mints a new draft, never a new session; suggestions
  get ids when the draft is saved, never per edit.
- `POST /api/campaign/{name}/sessions`
  `{"title":str,"notes":str,"event_week"?:int,"place_ids"?:[str],
  "future_reason"?:str}` → 201. `event_week` defaults to the CURRENT
  simulation week; a FUTURE week is 422 unless `future_reason` explains
  it (session records never advance the clock). Notes > 30,000 chars are
  refused (split the session). 422 on unknown place ids.
- `GET /api/campaign/{name}/sessions` → `{"sessions":[metadata]}`.
- `GET /api/campaign/{name}/sessions/{id}` → `{"session","notes":[
  {file,revision,created,chars,text}],"drafts":[full drafts],
  "decisions":[decision records]}`. 404 unknown id.
- `POST /api/campaign/{name}/sessions/{id}/notes`
  `{"notes":str,"expected_revision":int}` → 201 appends an immutable
  revision; 409 on a stale expected_revision (editing never rewrites
  evidence behind an existing proposal).
- `PUT /api/campaign/{name}/sessions/{id}/drafts/{draft_id}`
  `{"suggestions":[…],"expected_revision":int}` → the saved draft.
  Suggestion ids are preserved (edits never re-mint identity); unknown
  ids are 422; a stale expected_revision is 409 (another editor or a
  finished extraction). Server-owned fields (`status`, `proposal`,
  `accepted`) cannot be edited through this route. Suggestions with no
  id (or `id: null`) are minted server-side on save — the response's
  `suggestions` list carries them in the same order they were sent, so
  a client correlates by position (the Sessions tab's prepare does
  exactly this: `Studio.prepareSelection`).
- `POST /api/campaign/{name}/sessions/{id}/drafts`
  `{"suggestions":[…]}` → 201, the new MANUAL draft — the "start a
  manual draft" action; manual drafting needs no API key.
- `POST /api/campaign/{name}/sessions/{id}/drafts/{draft_id}/suggestions`
  `{"suggestions":[…],"expected_revision"?:int}` → 201, the updated
  draft. Adds NEW suggestions to an existing draft (the "add suggestion"
  action); existing ids are never touched.
- `POST /api/campaign/{name}/sessions/{id}/extract` `{"confirm":true}`
  → 202 job; `confirm` must be exactly true (the metered-spend gate).
  One bounded LLM pass (at most two completion attempts; transport
  retries metered) maps the notes to SEMANTIC suggestions; the adapter
  resolves names against the captured roster (ambiguous names stay
  unresolved — never fuzzy-matched into mechanical targets), validates
  every evidence quotation against the cited notes revision, and saves a
  NEW draft. A malformed response is a job error; the notes stay intact
  and no fake "no changes" draft is written. Notes > 30,000 chars are
  refused before any spend.
- `POST /api/campaign/{name}/sessions/{id}/prepare`
  `{"selected":[suggestion ids],"draft_id"?:str,
  "expected_draft_revision"?:int}` → 200 (synchronous; two bounded
  engine subprocess calls, no LLM). Stages ONE **`session_changes`
  proposal**: state effects through the engine CLI on a captured copy,
  knowledge/disclosure after-images in memory, the final knowledge
  document compiled into the POST-EFFECT state, and the decision record —
  all captured against exact base revisions. 422 when a selected
  suggestion has validation findings (unresolved mapping, invented
  quotation, unconfirmed grade/visibility), when two selected changes hit
  the same faction field/resource (consolidate them), or when a
  disclosure's fact is not selected; 409 when any proposal is already
  pending (return to it first), the draft changed since review, or the
  state changed mid-prepare. Nothing in canon changes here.
- Accept/discard ride the EXISTING
  `/simulation/proposal/{pid}/accept|discard` endpoints. `session_changes`
  acceptance publishes state + knowledge + disclosure + the session
  decision record through ONE `publication.publish` call (one receipt,
  recover together; stable idempotency key campaign+session+proposal;
  applied-effect ids derive from session/suggestion ids so retries
  deduplicate). Both responses carry `draft_update`:
  `"ok" | "failed"` — best-effort draft bookkeeping (freezing accepted
  suggestions on accept, reopening prepared ones on discard) that NEVER
  fails the canon answer; `"failed"` means the DM should replay the
  accept (idempotent) or re-prepare by hand. Discard reopens the
  prepared suggestions. Already
  accepted suggestions can never be re-prepared (corrections = new
  suggestions).
- Manual drafting without an LLM: build a draft through the service
  (`sessions.save_draft`), then review/prepare exactly as above — no API
  key anywhere in the path.
- CLI: `python studio/session_notes.py import|list|show|extract|prepare|
  accept|discard …` — `extract` requires `--confirm-spend`; accept/discard
  call the same service layer as HTTP. Standalone `agentic-simulator`
  ingestion (`src/ingest.py`, `src/effects.py`) is untouched.

### Review (B stub → H, M4; vault stage 4; phase-1 publication)
- `GET /api/campaign/{name}/review` → `{"items":[{"id","kind":
  "lore-entry|lore-page|timeline-approval|finding","source","title","body",
  "findings":[…],"file"}]}` — the union of `lore/pending-*.yaml`,
  `simulation/pending-week-*.yaml`, and verification `findings.json` if
  present, PLUS the vault's own advisory findings (`source: "links"`):
  dangling wiki-links and name/alias collisions across entity pages,
  chronicles, and companion notes — capped at 20 per GET, with a note when
  more exist. Asset-file targets (`![[scene.png]]`) are file references,
  never dangling; companion notes are pipeline-named after their slug and
  never collide with an entity page of the same name (the entity page wins
  resolution). Pending entries carrying a `page` payload surface as
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

### Knowledge (Phase 4 — approved facts with faction visibility)
- Facts live in `simulation/knowledge.yaml` (schema_version 1); the visible
  slice compiles into `simulation/state.yaml`'s `knowledge` section, which
  the engine's `views.py` reads (`facts_you_know` in the faction view — and
  the faction prompt embeds the view). Visibility is EXPLICIT: `dm_only`
  (the default for anything unclassified — no automatic mass publication
  of lore, ever), `public` (any faction; public in the world is still not
  automatically what the party has discovered), or a list of faction ids.
- `GET /api/campaign/{name}/knowledge` → `{"facts":[{id,text,visibility,
  status,source_file,source_revision,subjects,stale,...}]}` — `stale` is
  true when the source file's current bytes no longer match the approved
  revision. The compiled state always carries the APPROVED text; a changed
  source never silently substitutes new wording.
- `POST /api/campaign/{name}/knowledge` `{text, visibility, source_file?,
  subjects?, week?, id?}` → upsert + compile (transactional,
  revision-checked). `week` is optional provenance (e.g. the week whose
  chronicle a rumor-fact came from — the "Tell the party" prefill on the
  Simulation tab parks `{text, visibility, source_file, week}` for this
  form; recording and revealing stay separate decisions).
  422 on unknown faction ids in the visibility list (a typo must not hide
  or leak a fact). 409 while a publication conflict is parked. Lands as a
  history snapshot like every canon change — `snapshot` in the response
  (`supersede`, `reconcile`, `reveal` and `revoke` snapshot the same way).
- `POST .../knowledge/{id}/supersede` same body → approves the corrected
  fact and flips the old one to `superseded` (provenance kept, never
  compiled again) + compile.
- `POST .../knowledge/{id}/reconcile` → the DM re-approves a changed
  source file after reading it: the recorded revision catches up, the
  approved text is unchanged.

### Briefing + player disclosure (Phase 6 — deterministic, no LLM)
- `GET /api/campaign/{name}/briefing?since_week=N&focus=A,B` → the DM
  briefing: approved changes after week N (events, rumors), affected
  factions/places with refs to the entity page / week chronicle / matching
  map, and unresolved decisions — a pending proposal is included but always
  labeled `status: "pending"` (PROPOSED, NOT CANON), stale knowledge facts
  and pending review counts as `needs_attention`. Capped: `shown` items
  (default cap 10) plus an honest `omitted` count — never a silent
  truncation. `decisions` is the UNCAPPED pending/needs_attention list
  (ticket 14: the cap sorts week-less decision items last, so readers
  must not stop at `items`). `markdown` rides in the payload, and a
  `quests` section (overdue timers first, active quests intersecting the
  covered range, the hooked count as one line — see Quests below) renders
  even when the week range itself is empty. `since_week` must be >= 0.
- `GET /api/campaign/{name}/briefing/markdown` → the same briefing as
  durable Markdown (PlainTextResponse) for save/print.
- `POST /api/campaign/{name}/prep?since_week=N` → next session prep
  (tickets/07): compiles the briefing's events (top of the cap, with the
  omitted count stated on the page), the freshest week's rumors (top 3),
  the quests briefing section (overdue first), the public facts not yet
  revealed, and the open decisions (read from the briefing's UNCAPPED
  `decisions`, ticket 14) into
  ONE vault page, `lore/prep/<YYYY-MM-DD>-next-session.md`, frontmatter
  `kind: prep` / `date` / `covers_weeks`. Every item links back to its
  source of record (chronicles as relative links, entity pages as
  wiki-links, fact source files as relative links). A compile, not a
  generation — deterministic, no LLM pass, no budget; the only gate is the
  campaign lock (409 while a job holds it), and the write lands as a
  history snapshot like every canon change. Regeneration NEVER overwrites:
  the same date's re-runs take the next free `-2`, `-3` … name (the
  pending-lore-files pattern). Returns `{file, snapshot}`.
- `GET /api/campaign/{name}/prep/markdown?since_week=N` → the same compile
  as text (PlainTextResponse), for printing without Obsidian. Reads only.
  CLI equivalent: `python studio/prep.py <campaign> [--since-week N]
  [--date YYYY-MM-DD] [--stdout]`.
- `POST /api/campaign/{name}/knowledge/{id}/reveal` `{"week"?:int}` → moves
  one CURRENT PUBLIC fact into the party-disclosure record
  (`simulation/party-knowledge.yaml`). Only public facts reveal: 422
  otherwise — faction-restricted knowledge has no accidental path to the
  party. Idempotent. `POST .../knowledge/{id}/revoke` undoes a reveal.
- `GET /api/campaign/{name}/disclosure` → the record.
- `GET /api/campaign/{name}/disclosure/handout` → the shareable handout
  (PlainTextResponse): ONLY the revealed facts' text (+ known-since week).
  An empty disclosure set produces an empty handout WITH an explanation —
  never a fallback to the DM briefing. No mechanics, no pending proposals,
  no secret titles or file paths.

### Places (place-centered campaign view — deterministic, no LLM)
- One campaign-local `place_id` joins lore pages (a `kind: place` page with
  `place_id:` frontmatter OWNS identity), simulation places
  (`state.places` / the normalized legacy `city`), map attachments
  (companion-note `place_id:`), entity residence/presence
  (`resides_at` / `present_at`), facts (`place_ids`), events
  (`place_ids` on the event record), factions (`place`,
  `interest_place_ids`), and projects (`place_ids`). Old campaigns read
  WITHOUT any migration; a lore page and a sim place that share only a
  normalized name appear as a SUGGESTED join — never merged until the
  Link action writes the id (page body and unknown frontmatter keys are
  preserved; renames keep the id and fold the old name into aliases).
- `GET /api/campaign/{name}/places` → `{places:[{id,name,explicit,
  provisional,association,lore_file,sim_id,maps,conflict}],
  suggested:[...], conflicts:[...], findings:[...]}` — the Places
  navigation entry.
- `GET /api/campaign/{name}/places/{place_id}` → the consolidated DM view:
  `{place, parent, children, maps, entities, factions, projects, events,
  knowledge, pending, forecasts, mentions, findings, counts, revision}`.
  Unknown id → 404; an id two lore pages both claim → 422 (a conflict,
  never first-match-wins). Events are ONLY those whose records carry the
  place id (never inferred from an actor's current place); old events
  without ids stay unlocated and carry a read-only locator
  `{index, state_revision}`, not a fake stable id. Facts carry their
  visibility badge and the party-disclosure state from
  `party-knowledge.yaml` — `public` in-world is not party-known. Pending
  proposal items and active-forecast items are `canonical: false`
  (forecasts also `hypothetical: true`) and never count as canon.
  `?window=all` spans all history; recent defaults to 8 sim weeks;
  `?include_descendants=true` pulls in child places (cycle-safe).
- `GET .../places/{id}/events` and `GET .../places/{id}/mentions` → the
  paginated slices behind the page (`?limit=&offset=`, max 200).
- `POST .../places/{place_id}/link` `{page?, expected_page_revision?}` →
  the one-time Link action: persists the id onto the owning lore page
  (minting `place-<uuid>` for lore-only records; adopting the simulation's
  id when one exists, so no state rewrite). Stale revision → 409.
- `PATCH .../places/{place_id}` `{name?, parent_place_id?, aliases?,
  preferred_map?, expected_page_revision?}` → rename / re-parent /
  re-alias / set the preferred map on the page frontmatter. `preferred_map`
  must be an existing campaign-relative path under `maps/`. Cyclic
  parents are refused.
- `POST .../places/{place_id}/associations` `{source_kind, source_id,
  relationship, add?, expected_revision?}` → one typed update to an
  ALLOWLISTED source record: `entity` page stem → `resides_at` /
  `present_at`; `event` locator index → `occurred_at`; `fact` id →
  `about`; `faction` id → `interest`; `map` slug → `depicts` (companion
  note + params.yaml, never the generated artifacts). Unknown place ids
  are refused at write time.
- CLI (same service): `python studio/places.py show|list|link|associate …`
  — `show <campaign> <id> --markdown` prints DM material that labels
  private / pending / hypothetical sections; it is not a player handout.

### Quests (first-class entities on lore pages — deterministic, no LLM)
- A quest IS a lore page: `lore/entities/<name>.md` with `kind: quest`
  frontmatter. Contract: `status:` hooked | active | resolved | abandoned
  (default `hooked`; an unknown value degrades to `hooked` with an
  advisory finding), plus optional `given_by`, `places` / `factions`
  (page names, matched through the links index: a page's `place_id`
  owns the id; a unique simulated-place/faction name match only widens
  the briefing's intersection), `due` (a week number or an ISO date —
  never anything else; `level` is free text and is never parsed). Extra
  frontmatter keys are ignored, never errors; non-quest pages are
  invisible to the read model. Reads are disk-fresh — hand-edits in
  Obsidian are equally valid, there is no cache to invalidate.
- `GET /api/campaign/{name}/quests` → `{quests:[{page,file,name,status,
  given_by,places,place_ids,factions,faction_ids,due_week,due_date,
  overdue,level,note,body,revision}], counts:{status:n}, findings:[…]}`
  — the World Building panel's Quests card (no new tab): grouped by
  status with counts, overdue rows loud, a row opens the existing
  lore-page view.
- `GET .../quests/{page}` → one quest + its backlinks (`{page}` is the
  filename stem). Unknown page → 404.
- `PATCH .../quests/{page}` `{status, expected_page_revision?}` → the
  reviewed short write: campaign lock or 409, publication-path revision
  checks (a stale revision → 409 with nothing written), transactional,
  body and unknown frontmatter keys preserved, history snapshot
  committed (`snapshot` in the response). Same status again → `noop`.
- The DM briefing (`GET .../briefing`) gains a **quests section**:
  overdue timers (due in the past, week or date, while status is not
  `resolved`) listed FIRST, then `active` quests whose places/factions
  intersect the factions/places changed in the covered week range, then
  the hooked count as one honest line ("N hooks not shown").
- CLI (same service): `python studio/quests.py <campaign> list|show|set …`
  — `set` takes the campaign lock and commits the snapshot like the
  route.

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
  `campaign-data/engine-home/.week-run-flag` exists (a week job is running,
  or a crashed one has not been restored by the boot hook yet).
- `GET /api/doctor` → the full `doctor.py` JSON (key diagnosis, tool-tree
  health, the `week run` check reports an in-progress/interrupted week
  (`campaign-data/engine-home/.week-run-flag`); the `data root` check names
  any legacy (pre-campaign-data) dirs with the doctor migration command
  (`doctor.py --migrate-data-root`, CLI only — nothing migrates itself);
  the `vault links` check counts
  dangling wiki-links and name/alias collisions across campaigns — advisory,
  never required. `doctor.py --fix` (CLI only, not an API) performs the safe
  repairs: `npm install` in a broken tool tree (FMG's adds
  `--ignore-scripts` — see D7), and
  `pip install -r studio/requirements.txt`.
- `GET /api/doctor?quick=1` → same shape, but the LLM key check is
  presence-only (no live probe, no tokens, no network wait) — what the
  page's setup strip polls on boot.
- `GET /tutorial` → `docs/for-new-gms/TUTORIAL.md` as `text/plain` (the help
  modal links here); 404 with a pointer to docs/for-new-gms/ if the file is
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
(no modules, no build): `js/esc.js, js/logtail.js, js/core.js, js/handoff.js,
js/maps.js, js/world.js, js/sim.js, js/knowledge.js, js/review.js`
(`esc.js`/`logtail.js`/`handoff.js` are UMD and
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
  "Propose week N" behind a `Studio.confirmRun` credits warning with live
  log tail — the run stages a PROPOSAL (state + chronicle + diff) shown in
  a review panel with **Accept** / **Discard**; only Accept publishes
  (idempotent, receipt-backed). The tab also renders the session-effects
  recorder and `Studio.snapshotBanner()` when a history snapshot failed.
- **Knowledge**: approved facts (visibility pills dm_only/public/
  faction-list, stale + superseded badges) with record / supersede /
  reconcile / reveal-to-party / revoke actions; the deterministic DM
  briefing (since-week + focus controls, capped item list, Markdown link);
  the party-disclosure record with the handout link (GET
  `/disclosure/handout`, plain text, ONLY revealed facts).
- **Review**: items with kind badge, provenance, findings inline,
  approve/delete buttons; renders `Studio.snapshotBanner()` too — a failed
  history snapshot is recovered with its one-click "Retry snapshot" button
  (POST `/api/campaign/{name}/snapshot`), never by hand-crafted HTTP.

Header (all tabs): a search box. Enter fires
`GET /api/campaign/{name}/search?q=…` for the selected campaign and
renders the hits in a dismissible panel (kind badge, file:line, the
matched line with the match highlighted, optional heading context); Esc,
the X button, or a campaign switch closes it.

Style: one consistent tabletop look (parchment-and-ink or tasteful dark —
pick one), system font stack, **no external network resources** (no CDN
fonts/scripts; localhost app must work offline). Never distort SVGs.
