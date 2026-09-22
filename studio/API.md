# Campaign Studio — REST + frontend contract (v1)

The single coordination artifact for everyone building studio pieces.
The design doc is `../campaign-studio-design.md` (decisions D1–D7,
iterations M0–M5, guardrails). This file adds exact API shapes on top of it.
If code and this file disagree, fix the code or update this file in the same
change — never let them drift.

## Fixed decisions

- Root: `Documents/D&D/studio/`. Campaigns live at `Documents/D&D/campaigns/<name>/`
  (user data separate from code; `spine.py` resolves the path relative to itself).
- Server: FastAPI, bound `127.0.0.1:8765` (env `STUDIO_PORT` overrides).
  `python studio/server.py` starts uvicorn programmatically and prints the URL.
- Responses are JSON unless noted. Errors: `{"error": "..."}` with a 4xx/5xx status.
- Jobs (D5): long work returns `{"job_id": "..."}` with status 202.
  `GET /api/jobs/{id}` → `{"id","kind","status":"queued|running|done|error",
  "log_tail":[str,...],"result":object|null,"error":str|null}`. UI polls ~1 s.
  The jobs dict is in-process and ephemeral; canon lives on disk only.
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
  `POST .../maps/cities`, `POST .../maps/sites` — take the lock at submit
  time and answer `409 {"error": "another job is already modifying this
  campaign"}` while it is held. Review approve/delete take the same lock
  blocking: they wait for an in-flight job instead of failing.
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

### Maps (B stub → G, M1)
- `GET /api/campaign/{name}/maps` → `{"cities":[{"slug","label","svg","params","geojson"}],"sites":[…]}`;
  file URLs are `/campaign-file/<name>/maps/...`.
- `POST /api/campaign/{name}/maps/cities` `{"burg": str}` → job.
  Takes the campaign lock (**409 busy**, see hardening above); on success
  commits snapshot `campaign/<name>: map city <slug>`.
  Engine: `node scripts/gen-city.mjs --world <path inside campaign world/> --burg "<name>" --out <tmp>`
  with `cwd=dnd-mapping`; copy outputs → `maps/cities/<slug>/`.
  Result `{"slug","svg":url}`. `--world` accepts anything import_fmg.py
  accepts (full JSON export, headless dump, or .map save) — a campaign's
  `world/source.json` qualifies.
- `POST /api/campaign/{name}/maps/sites` `{"kind":"dungeon|cave|camp","name":str,"seed"?:str}` → job.
  Engine chain (cwd=dnd-mapping, list-form args): `python -X utf8
  src/battlemap.py gen-<kind> --seed <seed> --name "<name>"` →
  `... render --dir worlds/sites/<name>` →
  `node scripts/render-scene-png.mjs worlds/sites/<name>` (sharp) →
  `... wrap --dir worlds/sites/<name>`; artifact set
  {scene.json, scene.svg, scene.png, <slug>.uvtt} copied →
  `maps/sites/<slug>/` (+ a provenance `params.yaml` with name/kind/seed).
  Takes the campaign lock (**409 busy**); on success commits snapshot
  `campaign/<name>: map site <slug>`.
  `seed` defaults to crc32(slug) % 10^7 (recorded in params.yaml — every
  scene stays a derived artifact). Result
  `{"slug","label","svg":url,"png":url,"uvtt":url,"scene":url}`.
  A sharp/junction failure fails the job with the documented repair line
  (`CI=true pnpm install --offline` in dnd-mapping/tools/settlemaker) in
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

### Lore (B reads → F, M2)
- `GET /api/campaign/{name}/lore` → `{"timeline":[entries],"entities":{...},
  "style":{"names-shadian.txt":str,"voice.md":str}}`
- `PUT /api/campaign/{name}/lore/entities` `{"text": "<yaml>"}` → validate
  `yaml.safe_load`, write file, `{"ok":true}`; commits snapshot
  `campaign/<name>: update lore entities`.
- `PUT /api/campaign/{name}/lore/style/{file}` `{"text":str}` → write (filename
  constrained to `[A-Za-z0-9._-]`, must exist already); commits snapshot
  `campaign/<name>: update style/<file>`.
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
  "chronicles":[{"week","title","file"}],"views_available":[ids]}`.
  **404 is a normal state for a fresh campaign** (no state.yaml yet — no
  factions exist); the UI renders it as an empty state, not an error.
- `GET /api/campaign/{name}/simulation/views/{faction_id}` → the fog-of-war
  `faction_view` dict as JSON. Engine: `python -X utf8 src/views.py <faction_id>`
  (cwd=agentic-simulator; E adds this CLI, printing YAML).
- `POST /api/campaign/{name}/simulation/week` `{"confirm":true}` → job.
  **422 unless `confirm` is exactly true** (D5: the expensive button).
  Takes the campaign lock (**409 busy** while another mutating job runs).
  Interrupted-run detection: the job writes
  `agentic-simulator/world/.week-run-flag` (JSON: campaign + ISO timestamp)
  before copy-in and removes it in a `finally` after copy-out; if the
  server died mid-week, the next boot restores the engine home's
  `world/state.yaml` from `state.yaml.bak` (when present), deletes the
  flag, and logs "interrupted week run for campaign X — engine home
  restored" (`GET /api/health` reports `week_run_in_progress: true` until
  then). Copy-in: campaign `simulation/state.yaml` → `agentic-simulator/world/state.yaml`.
  Run: `python -X utf8 src/tick.py <live-mode argv>` — read tick.py's docstring
  for the exact incantation; report it. Copy-out: new `state.yaml` + any new
  `chronicle/000N-*.md` → campaign `simulation/`. Result
  `{"week","chronicle":{...},"held":[faction ids],"pending":str,
  "pending_entries":n}` — after a SUCCESSFUL week the server also stages
  `simulation/pending-week-N.yaml` (N = the week just run; see Shared file
  shapes): one proposed timeline entry per NON-WORLD event, purely
  mechanical extraction, no LLM call — and commits snapshot
  `campaign/<name>: week N — <chronicle title>`.
- `GET /api/campaign/{name}/simulation/chronicle/{week}` → `{"week","title","markdown"}`.
- `POST /api/campaign/{name}/simulation/why` `{"week":int,"faction":str}` → sync.
  Engine: `python -X utf8 src/why.py <week> <faction> --json` (E adds the flag).
  Passthrough `{"week","faction","lines":[str]}` — lines render as an indented
  causal chain.

### Review (B stub → H, M4)
- `GET /api/campaign/{name}/review` → `{"items":[{"id","kind":
  "lore-entry|timeline-approval|finding","source","title","body","findings":[…],"file"}]}`
  — the union of `lore/pending-*.yaml`, `simulation/pending-week-*.yaml`, and
  verification `findings.json` if present.
- `POST /api/campaign/{name}/review/approve` `{"file":str,"ids":[str]}` or
  `{"file":str,"all":true}` → append approved entries into `lore/timeline.yaml`
  (entries keep their `source`), remove from pending → `{"approved":n}`.
  Takes the shared campaign lock **blocking** (waits for an in-flight
  week/generation job), then commits snapshot
  `campaign/<name>: approve N pending entries`.
- `POST /api/campaign/{name}/review/delete` same body → `{"deleted":n}`;
  same lock, snapshot `campaign/<name>: delete N pending entries`.

### Search (H)
- `GET /api/campaign/{name}/search?q=<str>` → line-by-line, case-insensitive
  substring grep over the campaign's text files: `lore/*.yaml`,
  `lore/style/*`, `simulation/chronicle/*.md`, `simulation/pending-*.yaml`,
  `world/geography.yaml`, `campaign.yaml`. →
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
- `GET /api/doctor` → H wires to `doctor.py` JSON; B returns 501 until then.
  The `week run` check reports an in-progress/interrupted week
  (`agentic-simulator/world/.week-run-flag`); it is informational, not
  required. `doctor.py --fix` (CLI only, not an API) performs the safe
  repairs: `CI=true pnpm install --offline` in a broken tool tree, and
  `pip install -r studio/requirements.txt`.

### Shared file shapes
Timeline entry: `{"id","when","title","text","source"}` with
`source ∈ {authored, session, sim:week-N}`; ids `t0001`… increasing.
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

## Frontend contract (C, M0)

One page (guardrail 6). `index.html` loads, in order, plain scripts
(no modules, no build): `js/core.js, js/maps.js, js/world.js, js/sim.js, js/review.js`.

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
  handled engine-side), submit → job → poll → refresh. Sites form (kind+name), same.
- **World Building**: geography digest (nations + burgs tables); timeline with
  source badges; entities table; style viewer (textarea, PUT save);
  "Generate backstory" (focus input) → pending → point at Review tab;
  "Name helper" (culture + count) → list.
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
