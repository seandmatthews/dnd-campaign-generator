# Obsidian Integration (design)

Status: **stages 1–4 built** (2026-09-23: entity pages canonical + lazy
migration, link index with backlinks/dangling/collision findings surfaced in
the review queue and doctor, maps joined the graph via companion notes and
the attach-to-place field — `studio/links.py` is the index; then stage 4:
the missing-pages job writes dangling links into `pending-pages-N.yaml`,
approval stubs `new_names` as pages, and week chronicles are linkified on
copy-out). Stage 5 (interactive maps) remains a proposal.
Companion to [`campaign-studio-design.md`](campaign-studio-design.md) (the spine is
§1; the guardrails are §6). Every stage below degrades gracefully to plain files
the studio owns with or without Obsidian.

## 0. Core idea: the campaign directory is the vault

The spine is already "one campaign = one directory of files, no database"
(design §1). Obsidian's model is the same model: a folder of markdown with
wiki-links. So there is nothing to export or sync — Obsidian opens
`campaign-data/campaigns/<name>/` directly as a vault (campaigns live under
the git-ignored campaign-data root, ticket 35), and the lore becomes
first-class markdown inside the spine. If a vault was opened at the old
pre-ticket-35 path, re-open it once at the new one after
`python studio/doctor.py --migrate-data-root`; the obsidian:// URIs the app
builds derive from `campaigns_root()` and follow the move on their own.

Division of labor (hold this boundary; it is what keeps us from rebuilding
Obsidian in the browser tab):

- **Obsidian (the DM's surface):** reading, graph view, backlinks, hand-editing,
  creating new dangling links by typing `[[Something New]]`.
- **Studio (the authority surface):** the link index, validation, generation,
  review. Nothing lands unreviewed; generation is fed or refused (design §6).

One structural fact shapes everything else: **Obsidian's graph is
note-centric.** Images and SVGs are embeddable attachments but second-class
citizens — no links, no properties, no graph nodes of their own. The universal
pattern is therefore a **companion note** for every non-markdown artifact: the
note carries the frontmatter and the links; the file rides inside it. Maps join
the graph through their notes.

## 1. Lore pages (the notebook.ai-shaped layer)

Today entities are rows in a flat `lore/entities.yaml`
(`id/kind/name/aliases/note`) with no relationships and no per-entity depth.
Places are already in there: the shipped `ruravaqan` sample carries `kind:
place` districts, a `kind: city`, `kind: nation` and `kind: world`, plus the
factions mirrored as entities — the migration gives the place layer pages
instead of inventing one. That is what makes §4's map links cheap.
The change: **one entity = one markdown file** in `lore/entities/`.

```
campaign-data/campaigns/<name>/
  lore/
    entities/
      House Ferrand.md
      Ruravaqan.md
      Shadia.md
    timeline.yaml          (unchanged)
    style/                 (unchanged)
    pending-pages-N.md     (staged drafts — same pattern as pending-entries-N.yaml)
```

```markdown
---
id: mint
kind: organization      # freeform, like today's "banking house" — not a schema
name: House Ferrand
aliases: [House Ferrand, the Mint, Ferrand]
parent: "[[Ruravaqan]]"
seated-in: "[[the Mint quarter]]"
---

A banking house wrapped around the Doge's customs revenue.

## Wants
Seize the customs revenue as repayment; foreclose on the harbor
if the estate defaults.

## Appearances
- [[0004-the-coffers-stay-shut|week 4 — the coffers stay shut]]
```

- **Migration is mechanical:** frontmatter carries today's exact fields;
  `spine.load_entities()` becomes a parser over `lore/entities/*.md` returning
  the same dicts, so loregen's whole-file feeding and the hallucinated-name
  check keep working unchanged.
- **Links are the relationship model.** Typed relations live in frontmatter
  (`parent:`, `seated-in:`); incidental ones live in prose. `kind` stays
  freeform — the notebook.ai taxonomy (creature, deity, race, town, landmark,
  technology…) is a suggestion list in the editor, not a constraint.

**Open decision — filenames.** Obsidian resolves links by filename, so this
matters more than it looks:

- *Display names* (`House Ferrand.md`) — recommended. Typing `[[House Fer`
  autocompletes while hand-editing; links read cleanly. These names never
  become URL segments (routes go through the resolver index, like map slugs).
- *Slugs* (`house-ferrand.md`) — matches repo kebab-case discipline, but
  hand-written links need aliases (`[[house-ferrand|House Ferrand]]`), taxing
  exactly the surface we are optimizing for the human. The repo's `&`-in-path
  scars (pre-rename, gone 2026-10-04) came from shell plumbing, not
  filenames; nothing here goes through a shell.

**Open decision — `entities.yaml` fate.** Either migrate once and delete, or
keep the loader tolerant of a legacy file. Recommendation: migrate once,
remove on the same canon snapshot.

## 2. The link index

One pass over the vault's `*.md` builds:

- a **resolver** — name / alias / filename → page (the same table the
  simulator's per-campaign aliases and loregen's name check already lean on);
- a **backlink index**.

The index powers three things, all advisory:

1. **Page view + backlinks in the World Building tab** — the entities table becomes
   clickable pages, "mentioned in" computed server-side, so Obsidian need not
   be open.
2. **Validation findings** — dangling links and name/alias collisions surface
   in the review queue (findings, never verdicts). A `doctor.py` check reports
   the same on demand.
3. **Search** — the existing grep route adds `lore/entities/*.md` to its file
   set.

Scope, settled: the index scans `*.md` only, and `timeline.yaml` stays out of
the graph on purpose — the chronicles, linkified on write (§3), carry the
story-side links. Timeline rows remain plain canon.

## 3. Generation on the graph

Two integrations turn the links from passive notes into an engine, both riding
existing guardrails:

- **Dangling links become fed-or-refused generation prompts.** A dangling
  `[[Something New]]` is a generation target whose grounding is already
  present: the prompt is fed the linking pages (backlink context is exactly
  what the ethos demands), and the draft lands in `pending-pages-N.md` for
  review. Nothing lands unreviewed.
- **Approved entries auto-stub new names.** When lore entries whose
  `new_names` passed the hallucinated-name check are approved, the approver
  creates stub pages (kind `unknown`, backlinks already in place). The graph
  grows with the story instead of lagging it.

Plus one small change with outsized effect: **linkify chronicles on write.**
The chronicle writer knows the alias table; linkify the first occurrence of
each known name per entry, and every entity page's backlinks answer "where did
this faction show up in the story?" — free, in Obsidian, with zero new UI.

## 4. Maps and images

Maps are already durable spine files. Obsidian's native behavior is the
durable baseline:

- **Embeds**: `![[ruravaqan.svg|600]]` and `![[scene.png]]` render inline; our
  SVGs are static vector exports (no scripts), which is what Obsidian
  sanitizes against — fine. Recent Obsidian versions add native
  click-to-zoom and fullscreen image viewing in core.
- **The regeneration win**: embeds are references *by filename*, not copies.
  When the studio regenerates `world/map.svg` or a city SVG in place, every
  note embedding it updates. No import step, no sync, no staleness.
- **Canvas** (core plugin): pin a battlemap next to entity notes and the
  chronicle entry for the week it was fought.
- **Dataview/Bases**: a campaign map index generated from companion-note
  frontmatter.

Each artifact has a natural consumer; the companion note is the hub between
them:

| artifact | Obsidian-facing render | other consumer |
|---|---|---|
| `world/map.svg` | embeds natively, vector stays crisp | studio worldmap |
| city `<slug>.svg` + `<slug>.geojson` | the SVG | GeoJSON stays the structured source the studio reads |
| site `scene.png` + `<slug>.uvtt` | the PNG | UVTT is Foundry-facing; the note records which file to drop into the VTT. Atlas VTT is the second consumer (the at-the-table surface — [`atlas-seam.md`](atlas-seam.md)): same files, and the note's embed line (below) points at the published scene |

**Companion notes are generated, not hand-made.** Every city and site
generation emits the `<slug>.md` alongside the artifact: the embed, the
seed/params summary, links to the entities and chronicle weeks it involves,
and a `place:` frontmatter link saying where the map is. Companion notes land
with their artifact the way `params.yaml` does today — generated metadata,
not reviewed prose.

**Publisher-owned lines (the embed-line convention, ticket 50):** a
companion note may carry appended lines owned by outside publishers — the
first is the Atlas-scene embed/link the (companion-repo) scene publisher
adds when it publishes the site. The ownership rule is over bytes:

- A publisher's line is appended at the **end of the note body**, is
  **self-identifying** (recognizable without parsing the rest of the
  note), and a publisher deduplicates only its own line — so the scene
  publisher and any later one (party tokens, P4) coexist without risking
  each other's lines.
- Appends **preserve the note's existing line-ending style** (notes are
  CRLF on disk here).
- **Body-preserving writers must leave publisher lines untouched** — the
  attach-to-place write already complies (frontmatter only, body
  byte-for-byte, `places.py`).
- **`_companion_note` is the grandfathered exception:** it rewrites the
  whole note on map re-import (`hand edits do not survive`), and that is
  accepted — the line is publisher-owned and the publisher re-appends it.
  The heal is the publisher's *required* behavior: on every `scenes` run
  for a site — including a refuse-on-diff no-op, which still repairs the
  note — the publisher re-appends its line when missing. The companion
  README pins this before its scene publisher writes its first line.
- Any **new** in-core note writer must either preserve foreign lines or
  document its healing story the same way.

The DM's half is the **attach-to-place field** on the Maps tab: an optional
picker on the *Map a burg* and *Battlemap scenes* forms. Mapping a burg
defaults the link to the burg's page (auto-stubbed, kind `place`, if it
doesn't exist yet); a site defaults to no place — pick an existing place page
or type a new name, which lands as a dangling link and becomes exactly the
§3 generation prompt it looks like. The field writes nothing but the
wiki-link into the companion note's frontmatter.

**Site basename collision (load-bearing, not cosmetic):** cities already
name files uniquely per directory (`ruravaqan/ruravaqan.svg`), but every
site directory reuses the same basenames — `scene.png`, `scene.svg`,
`params.yaml`. Obsidian resolves embeds by filename, so `![[scene.png]]` is
ambiguous between sites. Renaming site artifacts to `<slug>.png` etc. is a
prerequisite for companion-note embeds (stage 3, §7), not a later nicety:
new generations emit slugged names, and existing site directories get a
one-time mechanical rename. The same argument settles the vault boundary:
**one vault per campaign** (`map.svg`, `params.yaml` recur in every
campaign; a vault spanning the campaigns root would collide). If plugins get
installed, `.obsidian/` must stay out of canon snapshots — the campaigns
workspace's own `.gitignore` carries a depth-agnostic `.obsidian/` line
(any depth, so both a campaign-root vault and a lore-folder vault are
covered), and the studio ensures it on every snapshot (ticket 47;
`studio/canon.py` `WORKSPACE_IGNORE`). An ignore does not untrack: files
committed before the line landed need `git rm -r --cached` once.

## 5. Plugin survey (September 2026)

| plugin | state | fit |
|---|---|---|
| **Leaflet** ([javalent/obsidian-leaflet](https://github.com/javalent/obsidian-leaflet)) | maintenance mode, kept working | image maps with pan/zoom, markers linking to notes, distance/scale, overlays; config is a markdown code block in the note — data lives in the vault. The fallback that already satisfies files-first. |
| **Fantasy Map** ([sotirangelo/obsidian-fantasy-map](https://github.com/sotirangelo/obsidian-fantasy-map)) | young, solo, ~7 stars | markers, polygon regions, layers, main-note sidebars, scale calibration, and **hierarchical maps** (world → region → city → battlemap drill-down) — precisely our hierarchy. See §6 for the blocker. |
| **Map View** (esm) | active | real-world GIS (OSM tiles, lat/long frontmatter). Wrong model for fictional coordinates; skipped. |
| **Excalidraw** (vslinko) | active | annotate over embedded maps (arrows, circles, text); the drawing is itself a vault file. Tactical session notes, not a map viewer. |
| native zoomable images | core ([changelog](https://obsidian.md/changelog)) | click-to-zoom, fullscreen; covers big battlemap PNG inspection without plugins |

Layering: **native embeds + companion notes are the contract** — zero
dependencies, fully durable. **Leaflet code blocks are optional sugar** the
studio can emit inside companion notes (plugin absent → one inert code block
next to a working embed). Fantasy Map is the candidate worth investing in,
below.

## 6. Fantasy Map: the investment verdict

Question on the table: could we "throw tokens at it" (agent-driven
development) until it meets our use cases? Answer: yes, and it is unusually
token-feasible — but the first spend is architectural, not incremental polish.

What the source shows (checked 2026-09-23):

- Small and agent-friendly: ~45 TS/Svelte files, none over 15 KB; valibot
  schemas for every data shape (machine-checkable contracts); MIT; the
  maintainer is already AI-assisted (`.claude/hooks/` in the tree).
- The feature model is literally **GeoJSON FeatureCollections** — markers and
  regions are GeoJSON features carrying note links, tags, relations. Our city
  pipeline already emits GeoJSON per city, so "studio generates the map file"
  is nearly free once the storage layer allows it.
- **The blocker:** persistence is `data.json` inside
  `.obsidian/plugins/` (Obsidian `saveData()`). Maps, markers, regions, and
  the hierarchy live in app-internal storage, not vault files. That breaks
  both hard requirements: the studio cannot generate what the plugin reads
  (the spine would be mimicking a private format we don't control — quietly a
  database again), and canon snapshots cannot see edits (one opaque blob,
  diff-hostile; and `.obsidian/` inside the campaign dir would pollute
  snapshots absent a `.gitignore`).

Investment order:

1. **A test harness first** — the repo has no tests, only lint/release CI.
   Vitest with mocked Obsidian APIs for the pure logic (schemas, persistence,
   geometry). Agents improving anything before this are improving blind.
2. **The structural PR: move persistence to vault files** — a GeoJSON file
   (or a markdown note with a GeoJSON block) per map as the source of truth,
   plus a one-time importer for existing `data.json` users. The plugin's own
   types already model everything as GeoJSON; this is a storage migration,
   not a redesign.
3. **The wishlist** — SVG image support (today PNG/JPEG/WebP), GeoJSON file
   import, whatever else the checklist surfaces.

Route: try upstreaming the persistence PR; plan on the fork. Repo precedent:
`dnd-mapping/vendor-patches/fmg/` already maintains tracked copies of a
patched third-party tool — owning a patched plugin copy is established
practice here.

Honest caveat about the method: tokens buy the mechanical 90% (persistence,
formats, tests, SVG). The last 10% — does the drill-down navigate well, is
the sidebar useful at the table — needs the DM's eyes in Obsidian; that part
is judgment, not grinding.

## 7. Build order

1. **Pages become canonical** — parser in spine, one-time migration of
   `entities.yaml`, page view + backlinks in the World Building tab. No behavior change
   elsewhere.
2. **Linking** — resolver + backlink index, dangling-link/collision findings,
   doctor check, search extension.
3. **Maps join the graph** — the place-linking entry door: slug the site
   artifacts (§4), emit companion notes on city/site generation with the
   default `place:` link, add the attach-to-place field to the Maps tab
   forms. Stages 1–3 together are the smallest useful version: entity pages,
   the "everything about this place" panel, and maps inside it.
4. **Generation on the graph** — write-the-missing-pages job, auto-stub on
   approval, chronicle linkifying.
5. *(optional, later)* **Interactive maps** — the Fantasy Map route above, or
   Leaflet blocks in companion notes if that route stalls.

Each stage stands alone; stage 5 never blocks 1–4, and 1–4 never require
Obsidian to be installed at all.

## 8. Open decisions

- Entity filenames: display names (recommended) vs slugs (§1).
- `entities.yaml`: migrate-and-delete vs tolerant legacy loader (§1).
- Whether interactive maps justify the plugin investment at all — decided by
  demand after stages 1–4, not now (§6–7).
- Settled 2026-09-23: `timeline.yaml` stays out of the link graph — the
  chronicles carry the story-side links (§2).
