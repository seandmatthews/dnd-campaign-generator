# The Atlas VTT Seam
## The studio publishes canon into Atlas's vault files; the table writes prose back

**Status:** design — built so far: P0 presets and P2 scenes (companion
repo, tickets 04 and 05 there, 2026-10-11/12 — the scenes publisher falls
back to a `_inbox/` hand-off whenever it can't confirm the installed Atlas
version against its pinned golden file) and P1 fences (core ticket 49).
Verified against `atlas-vtt` at
v0.6.0 (2026-10-09 source read; the repo was cloned for this).
**Serves:** the DM who generates canon in the studio and runs the table in
[Atlas VTT](https://github.com/atlas-vtt/atlas-vtt) with nothing manually
imported — then brings the session's story home through the existing review
queue.
**Inherits:** the spine (campaign-studio-design §1), obsidian-integration
stages 1–4 (the campaign directory *is* the vault), and the global
guardrails (§6: nothing lands unreviewed, CLI equivalence).
**Supersedes:** the table-companion seam (icrpg `DESIGN.md` §4,
session-manifest/session-report) — the companion is frozen at v0 and the
vault-note convention below replaces both artifacts. Also answers
obsidian-integration §6: Atlas is the map plugin that passes the
persistence-in-vault-files requirement that disqualified Fantasy Map; the
§6 investment question is closed for the at-the-table surface. Obsidian-side
world browsing stays native embeds + companion notes.
**Residency amended 2026-10-10:** the core/companion split (see
[`../2026-10-10-next-steps.md`](../2026-10-10-next-steps.md)) moves P0's
preset file + installer, P1's templates, P2, and P4 OUT of this repo into
`Documents\dcg-avtt-integration\` (README there). Core keeps the `system:`
field, the fence convention + structural checks, the session loop, and the
tutorial (P3). The specs, contracts, and milestone order below are
unchanged — only the file homes move, and §3's P-headers read as
companion-repo homes where the ruling says so.

---

## 0. Core Idea

Atlas stores its entire world as files inside the Obsidian vault, and it
watches those files: presets appear live, and an adoption pass indexes
externally-written collection JSON. The campaign directory is already the
vault. So the seam needs **no sync, no API, no new runtime**:

> **The studio writes Atlas's file formats into the vault; Atlas does the
> rest by watching. The table writes prose into the vault; the studio's
> existing session-notes pipeline does the rest by review.**

Direction of truth is one-way in both halves. The studio publishes *into*
`atlas-vtt/` and never reads back out of it. The table produces *notes*,
never canon. Every stage degrades gracefully to the plain-file manual path
(a `.uvtt` dragged in by hand), per the obsidian-integration house rule.

Working rule for the whole seam: **publish, don't sync.** The publisher is
idempotent by stable paths, refuses to overwrite anything that differs, and
treats `atlas-vtt/` as opaque after publication.

---

## 1. The Boundary (who owns what)

| surface | owner | the other side never… |
|---|---|---|
| generation, canon, review, publication | studio | reads `atlas-vtt/` state back |
| `atlas-vtt/` after publication (fog, token positions, HP, widget values, player view) | Atlas | gets those states written by the studio again |
| lore pages + statblock fences, session notes | the vault (DM's hands) | bypass the pending/review machinery |
| `sessions/<id>/` ingestion → proposals | studio (already built) | applies a suggestion without acceptance |

Rulings:

- **R1 — Table state is not canon.** Explored fog, token positions, and HP
  are the session's chalk marks. They ride in the scene files, canon
  snapshots capture them as files, and nothing parses them.
- **R2 — Refuse-on-diff publication.** Stems are slugs (site artifacts are
  already slug-named, obsidian-integration §4). Atlas recovers asset ids
  deterministically from stems (`<type>-recovered-<hash36>`), so
  re-publishing the same slug targets the same asset. If the target exists
  and its bytes differ, the publisher refuses; `--force` writes a sibling
  (`<slug>-v2`). A hand-tuned scene (moved door, tweaked light) is an earned
  artifact — regeneration must not erase it silently.
- **R3 — Review gates by artifact class, not by destination.** Statblock
  fences are prose → pending flow. Presets and scenes are deterministic
  conversions of already-canon artifacts → generated-metadata class, like
  `params.yaml` and companion notes: no review gate.
- **R4 — No Atlas source modifications.** File-format blocker → upstream
  issue first; `dnd-mapping/vendor-patches/` precedent if a fork is ever
  unavoidable. The adoption regexes and the preset folder are the stable
  contract; `library.json`/`collection.json` internals are not mimicked.

---

## 2. Contract Inventory (verified against atlas-vtt 0.6.0 source)

| # | surface | direction | status | notes / risk |
|---|---|---|---|---|
| 1 | `.uvtt` drag-in import | studio → table | **works today, zero code** | walls/doors/lights imported; size caps; lights start OFF when the file says lighting is baked |
| 2 | vault = campaign dir | — | settled (obsidian-int §0/§4) | `atlas-vtt/` lives at vault root naturally; add `campaigns/*/.obsidian/` + decide `atlas-vtt/` snapshot policy |
| 3 | `atlas-vtt/system-presets/<name>.json` watched live | studio → table | verified (`presetFiles.ts`) | schema = in-repo TS types + `presetValidation`; pin a golden fixture |
| 4 | adoption of `atlas-vtt/collections/<id>/{maps,scenes,encounters,players,characters,statblocks}/<stem>.json` | studio → table | verified (`assetAdoption.ts`) | record = `{id?, name, tags, collection, createdAt, modifiedAt, type, …}`; scene record carries `data.mapPath` → the `.atlasmap`; missing ids recovered deterministically from the stem |
| 5 | collection bootstrap (`collection.json` + `library.json`) | ? | **UNVERIFIED** whether a collection folder alone gets adopted without a library entry | fallback costs 20 seconds: create the collection once in the Atlas UI, publisher targets its id forever |
| 6 | creature resolution: Fantasy Statblocks bestiary → note frontmatter → ` ```statblock ` fence | vault → table | verified (`linkedCreature.ts`) | **works without the FS plugin installed**; fences are self-sufficient |
| 7 | note pinning on maps; scene embeds in notes (0.6.0) | vault ↔ table | verified (README/changelog) | companion notes can carry the scene link; session notes editable in Atlas's floating panel |
| 8 | session notes → studio proposals | table → studio | **already built** (`sessions.py`, `session_notes.py`) | evidence-checked suggestions, revision discipline, one `session_changes` proposal on accept |
| 9 | scene file schema (`ATLAS_SCHEMA`/`ATLAS_VERSION`) | — | churn risk: v0.x, lighting experimental | golden-file fixture pinned to a tested release; degrade-to-inbox fallback (§3) |

---

## 3. What We Build

Four publishers and one convention, in the order they pay off. Each has a
CLI (guardrail 1) and a degrade path.

### P0 — System preset publisher (small, deterministic)

`campaign.yaml` grows the Tier-1 `system:` field (icrpg first; the same
mechanism later emits any system's preset). `studio/atlas_presets/icrpg.json`
is a maintained source file, copied verbatim into
`atlas-vtt/system-presets/icrpg.json`.

The preset's *knobs* (values are house-rule content, authored from the
table's own conventions, not invented here — icrpg DESIGN §13): default d20
roll + critical rule; initiative rule; token resources (hearts; up to six);
conditions; widgets — an alarm-clock **timer** (seconds, persisted), a
**counter** for rounds, optionally a progress **clock**; light presets
(torch/lantern); default token vision (darkvision-equivalents if the table
uses them).

CLI: `python studio/publish_atlas.py <campaign> presets`.
Degrade path: none needed — copying a file is the manual path.

### P1 — Statblock fences on entity pages (small, review-gated)

The fence convention (shipped 2026-10-10, ticket 49): a pending
statblocks doc (`lore/pending-statblocks-N.yaml`, `mode: statblocks`,
entries carrying `target` + `fence`) approves into an EXISTING entity
page's body byte-surgically — frontmatter, comments, unknown keys, and
line endings all survive; re-approve replaces the last fence. Entity
kinds are freeform in code (`person`/`place`/`city`/`world`/`nation`,
stubs `unknown` — an earlier draft of this section said `pc`/`npc`,
which exist nowhere); the convention *recommends* `kind: person` pages
and gates on nothing. A fence goes AFTER at least one prose line (a
fence-only body would make the page's `note` become the fence text).
The verification ladder checks fences shape-only (lines parse as
`key: value`, values parse as number or dice, nothing empty) — findings,
never verdicts; field vocabulary is the pack's business. Atlas links
tokens to these notes with the FS plugin absent.

**Dormant by design:** the mechanism ships with no producer — the
"draft statblock" button waits for the companion pack's templates
(system-specific data lives there), and a hand-authored fence in
Obsidian bypasses the pending flow entirely (hand edits are canon).
Until a producer exists, hand fences are inserted by nothing and checked
by nothing.

### P2 — Scene publisher (the main build)

Input: `maps/sites/<slug>/` — `<slug>.png`, `<slug>.uvtt`, companion note.
Output into the vault, under one collection:

```
atlas-vtt/collections/<collectionId>/
  maps/<slug>.png                  image (path is recorded explicitly in the map record)
  maps/<slug>.json                 map record  {type: 'map', mapFilePath, name, tags, …}
  scenes/<slug>.atlasmap           scene content converted from the .uvtt:
                                   grid (cell size from resolution + meters_per_grid),
                                   walls incl. doors, lights, lighting {enabled: true}
  scenes/<slug>.json               scene record {type: 'scene', data: {mapPath}}
```

The conversion is a Python port of the essential half of Atlas's
`uvttToScene.ts` (~160 lines of TS: grid math, wall segments, doors,
lights, coordinate scaling) plus the `newSceneFile()` envelope. Bounded,
testable, no LLM anywhere near it.

Idempotency per R2. CLI: `python studio/publish_atlas.py <campaign> scenes
[--site slug]`; a Maps-tab button per site row. On publish, the companion
note gains one line: the Atlas scene embed/link (0.6.0 feature) — the note
stays the hub between every consumer. That line is **publisher-owned**
(appended at the end of the note body, self-identifying, idempotent, and
re-appended on every scenes run — including a refuse-on-diff no-op, which
still repairs the note after a map re-import rewrote it); the full
ownership rule lives in obsidian-integration §4.

**Degrade path (load-bearing):** a golden-file test commits one real scene
JSON from a pinned Atlas release and structurally diffs the publisher's
output against it (`run-tests.py --all` tier). If Atlas upgrades and the
golden test fails, `publish_atlas scenes` refuses to write `.atlasmap` files
and instead drops the `.uvtt` into `atlas-vtt/_inbox/` with a companion-note
line telling the DM to drag it in — the manual path, preserved loudly, until
the fixture is refreshed.

**Spike (first code task):** whether adoption registers a collection that
exists only as a folder (contract #5). If yes, the publisher bootstraps
collections too; if no, the DM creates the collection once in the UI and the
publisher reads its id from campaign config. Either way the publisher never
writes `library.json`.

### P3 — The session loop (tiny: a convention, not code)

The pipeline exists (`session_notes.py import/extract/prepare/accept`). The
seam's only addition is *where notes come from*: any vault file the DM
writes — during play in Atlas's pinned-note floating panel on the scene, or
after (`table-notes/` in the campaign root is the convention; the
`sessions/` folder itself is studio-managed, never hand-placed).
`import --notes-file <path>` copies the note into the studio-minted
`sessions/session-<id>/` store as revision 1; later edits are appended
revisions, they never rewrite the pinned file. for-new-gms gets an ATLAS
page documenting the loop: **generate → publish → play (pin the session
note) → import → review.** No consequence lands without acceptance; that
machinery already enforces it.

### P4 — Party tokens & encounters (parked)

PCs as Atlas characters/tokens with the hearts resource. One-time manual
setup in the UI is minutes and happens once per campaign — automation value
is low. Only if the party churns (deaths, recruits) does a party publisher
from the briefing roster earn its build. Encounter assembly from prep
(auto-placing NPC tokens on tonight's scene) is explicitly parked: the GM
dragging five tokens costs thirty seconds.

---

## 4. Milestones

### M0 — The night it already works (docs + fixtures only)

**Achieves:** the full loop tonight with zero code: install Atlas → open the
campaign vault (the studio registers it) → drop in the preset → drag a
`.uvtt` → play → pin a session note → `session_notes.py import`.
**Ships:** `docs/for-new-gms/ATLAS.md` (the loop above, with the "you should
see" checkpoints); the ICRPG preset fixture as a companion pack
(`dcg-avtt-integration/packs/icrpg/preset.json` — residency ruling
2026-10-10).
**Verify:** follow ATLAS.md on this machine, start to finish.
**Risks:** nearly none; the preset's rule values are judgment calls.

### M1 — Presets + fences (P0 + P1)

**Achieves:** a fresh campaign comes out of the box in ICRPG mode; NPC pages
become right-click-able creature sources.
**Ships:** `publish_atlas presets`; `system:` field; fence drafting through
pending; structural findings for fences.
**Verify:** new sample campaign → preset file lands; approve one fence →
Atlas links a token to the page (manual check, one time).
**Risks:** preset schema drift (golden fixture); fence format vs FS parser
quirks (one bad fence is visible immediately in Atlas).

### M2 — Scene publisher (P2)

**Achieves:** click a site row (or run the CLI) → the battlemap exists in
Atlas with grid, walls, doors, and lights wired; regenerate → refuse-on-diff.
**Ships:** the `uvttToScene` port + envelope writer + records writer;
golden-file test in the `--all` tier; `_inbox/` degrade path; companion-note
embed line; the collection spike settled first.
**Verify:** publish Ruravaqan's harbour quay → open in Atlas → walls block
sight, doors open, lights glow; republish after a params change → refusal
with a diff pointer.
**Risks:** schema drift (the degrade path is the answer); image-path
conventions (records carry explicit paths — spike confirms the accepted
location); the walls-crowding heuristic refusing dense generated maps
(surface the refusal text in the studio UI).

### M3 — The session loop, documented and polished (P3)

**Achieves:** the story comes home as a habit: note during play, import
after, review before canon.
**Ships:** ATLAS.md session section; optionally a Sessions-tab "import
notes" button over the existing CLI.
**Verify:** a real session's note becomes a `session_changes` proposal;
acceptance publishes; the chronicle linkifies the names.
**Risks:** none new — the machinery is live and reviewed.

### M4 — Party & encounters (parked until party churn demands it)

**Achieves:** roster changes propagate to the table without hand-editing
tokens.
**Ships:** party publisher from the briefing roster (character + token
records); encounter assembly only if the thirty seconds ever hurt.
**Risks:** token record shape is the least-documented surface — spike
before building, same golden-file discipline.

---

## 5. Non-Goals

- Reading `atlas-vtt/` table state into the spine. Fog, positions, HP are
  chalk (R1).
- Any live channel: no websockets into Obsidian, no reveal-push, no remote
  play. Atlas is offline by design; the studio is localhost prep-time.
- Mimicking `library.json` / `collection.json` internals (R4). The adoption
  regexes and the watched presets folder are the contract.
- An LLM in the publisher. It is conversion code: deterministic, tested,
  boring on purpose.
- Replacing Obsidian-side map browsing (native embeds, companion notes,
  optional Leaflet sugar stay). Atlas is the at-the-table surface only.
- Un-freezing the table companion. Its phone experience stays preserved at
  v0; if the table ever wants phones back, that is a new decision, not this
  seam's job.

## 6. Open Decisions

| question | recommendation |
|---|---|
| one collection vs per-place collections | one `Campaign` collection, tags for places (adoption cares about the id; fewer moving parts) |
| scene overwrite policy details | refuse + diff pointer; `--force` writes `<slug>-v2` sibling (R2) |
| fence vs frontmatter-creature for statblocks | fence in the body — richer, and the studio's frontmatter parser never sees it |
| `atlas-vtt/` in canon snapshots | yes, as files — tuned scenes are earned artifacts; `.obsidian/` stays ignored |
| who authors the ICRPG preset values | the DM's house rules (icrpg DESIGN §13 list), not this repo's invention |
| does M3 get a Sessions-tab button | only if the CLI feels heavy after two real sessions |
