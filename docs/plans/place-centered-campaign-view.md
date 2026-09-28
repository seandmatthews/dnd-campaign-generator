# Place-centered campaign view

Status: IMPLEMENTED 2026-09-28 (deterministic slice: `studio/places.py` read model, `studio/routers/places.py`, `studio/static/js/places.js` Places tab, CLI `show|list|link|associate`; suites `studio/tests/test_places.py`). One deliberate shape drift from this document: linking is `POST /places/{place_id}/link` (id in the URL), not `POST /places/link`. The session-editor place picker belongs to the session-notes plan and is not built yet. Written 2026-09-27 against the then-current working tree, which already contains multi-place simulation support. Reuse that work; do not introduce another incompatible `places` schema.

Related plans: [Session notes to changes](session-notes-to-changes.md), [Faction projects](faction-projects.md).

## Outcome

Opening a settlement or site gives the DM one useful page: its lore, maps, residents, faction presence, current projects, recent events, relevant facts, and unresolved proposals. Every item explains why it appears and links to its source.

The main question is: **“The party is going here next. What is here, what changed, and what needs my attention?”** The first version is a deterministic reading/organization feature and needs no LLM key.

Example: opening Ferry Crossing shows its battlemap, the ferrymaster's page, the Wardens' local presence, their concession project at 3/4, the party's last visit, and a pending session consequence clearly labeled as pending. It must not imply that a kingdom-wide event happened at the crossing merely because the Wardens participated in it.

## Existing foundation

- `studio/spine.py`: entity pages, geography, maps, state, and `state_places` normalization.
- Engine `store.py`: `places_of`, `faction_place`, legacy city fallback, and validation of current faction place references.
- Current Studio simulation creation supports extra places and faction home references.
- `studio/links.py`: page index, aliases, backlinks, dangling links, and map companion notes.
- `studio/routers/lore.py`: entity page detail and backlinks.
- `studio/routers/maps.py`: generated artifacts and optional attachment to a named place.
- `studio/briefing.py`: deterministic briefing and current heuristic map lookup.
- `knowledge.py`, `proposals.py`, `forecasts.py`: facts, proposed canon changes, and hypothetical outcomes.

There is already enough information for an initial place page. The missing pieces are a stable cross-source identity, explicit association metadata, and a consolidated query/UI. Do not require migration of every existing note before displaying anything.

## Product scope

Provide a Places navigation entry and contextual **Open place** links from maps, factions, project cards, and session records. A place page has:

1. Identity and context: name, kind, parent, aliases, lore page, and source provenance.
2. Maps: preferred map plus all attached city/site/world references, each labeled by scale.
3. People and organizations: explicit residents, visitors, faction homes, and interests with their distinct relationship labels.
4. Active projects: owner, objective, progress, last change, and requirements/blockers for the DM.
5. Recent activity: events explicitly located here; separate mentions/backlinks below.
6. Knowledge: current facts associated with the place, visibly marked by faction visibility and party-disclosure state.
7. Pending decisions: session/week/project proposals that explicitly affect the place. Forecasts live in a separate hypothetical section.

No generated summary is required. Do not add travel rules, an encounter generator, spatial GIS queries, territorial simulation, or a new player portal. The page is a DM view; existing player-handout export remains the only deliberate sharing path in scope.

## Identity and ownership

Use one campaign-local `place_id` across engine state, lore metadata, session suggestions, project records, and map attachment metadata. Display names and filenames are labels, never identity after a record has an ID.

The registry lives in entity-page frontmatter: a place page with `place_id` owns identity, display name, aliases, parent, and optional geography provenance. Simulation `state.places` remains authoritative for engine-specific place details. It references the same ID. Do not copy every geographic settlement into simulation or assume the simulator is installed when browsing lore.

Proposed frontmatter extensions:

```yaml
---
kind: place
name: Ferry Crossing
place_id: ferry-crossing
aliases: [Old Ferry]
parent_place_id: ruravaqan
geography_ref:
  world_revision: "<existing source-file revision>"
  burg_id: 42
---
The tollhouse stands beside the north landing.
```

`geography_ref` is optional. A river landing, dungeon, district, kingdom, or world can be a place without being an FMG burg. The world revision prevents burg index 42 in a regenerated world from silently becoming the old place. A stale provenance link produces a finding; it does not move the place or attach a new map automatically.

When an existing simulation place already has an explicit ID, use it if the DM links a lore page to that place. For a newly created lore-only place, mint `place-<UUID>` and retain it on renames. Existing slug IDs remain valid. Two explicit records claiming one ID are a conflict, never “first match wins.”

Read old campaigns without writing:

- Expose existing simulated places, including normalized legacy `city`, as provisional place records.
- Expose lore pages with `kind: place` and no ID as legacy records with a source-file locator; do not promise that locator survives a rename.
- Offer candidate joins by unique exact normalized name/alias, but label them as suggested associations. A name match alone does not merge canonical identity.
- A one-time **Link place records** action persists the chosen ID under revision checks, writing page and simulation references together when both need changes.

Promotion of a legacy city must pin its normalized ID before changing names. Renaming a page/name later preserves that ID and does not automatically rewrite an FMG source dump. Keep human wiki-links working through aliases where practical; report remaining dangling links.

## Association contract

Add structured relationships to existing records, not a second editable database of every relationship:

| Source | Explicit association | Meaning |
|---|---|---|
| Faction state | existing `place` | faction home/base |
| Faction state | new `interest_place_ids` | places the faction cares about, not owns |
| Entity frontmatter | new `resides_at`, `present_at` | separate residence and current presence |
| Map attachment metadata | new `place_id` | artifact depicts/attaches to this place |
| State event | new `place_ids` | where that event occurred or explicitly applies |
| Project | `place_ids` from project plan | where the undertaking operates |
| Knowledge fact | new `place_ids` | fact is about these places |
| Session | new `place_ids` and suggestion-level IDs | session context and exact consequences |

Entity `resides_at` and `present_at` are lists of place IDs to avoid ambiguous scalar/list formats. Events/facts/projects use lists as well. Do not rewrite existing free-text `subjects` or wiki-links; structured fields add precise joins alongside them.

Do not infer event location from an actor's current `place`. The faction may have moved, or acted remotely. Store explicit IDs at event creation, including accepted session events. If inference is offered during authoring, show it and require confirmation before persisting it.

Retain general wiki-link references as **Mentions**, with source excerpts. A wiki-link can discuss a place without establishing residence, ownership, presence, or an event location. Suggested name matches and explicit associations must have different relationship labels in API and UI.

Map generation currently uses names and companion notes. Add `place_id` to the Studio-owned attachment metadata (`params.yaml` and companion frontmatter as appropriate), preserving the readable wiki-link. Changing an attachment updates metadata through a reviewed short operation; it does not regenerate or hand-edit the generated SVG, PNG, GeoJSON, or source dump. A saved preference for primary map belongs on the place page as a campaign-relative artifact reference, not in the generator output.

## Query service

Add `studio/places.py` as a deterministic read model. Build it from existing loaders and one reusable campaign index, not one full rescan per card. The service should work without simulation state, geography, or maps; missing optional sections return empty lists and useful messages.

Proposed API shape:

```json
{
  "place": {"id": "ferry-crossing", "name": "Ferry Crossing", "kind": "place"},
  "revision": "<index/input fingerprint>",
  "maps": [],
  "entities": [],
  "factions": [],
  "projects": [],
  "events": [],
  "knowledge": [],
  "pending": [],
  "mentions": [],
  "findings": [],
  "counts": {"events": {"total": 0, "returned": 0, "more": 0}}
}
```

Every returned related item carries a source reference and relationship:

```json
{
  "id": "event-731a",
  "relationship": "occurred_at",
  "canonical": true,
  "source": {"file": "simulation/state.yaml", "record_id": "event-731a"},
  "week": 8,
  "text": "The party burned the reserve warehouse."
}
```

Pending items set `canonical: false` and include proposal ID/status; forecasts additionally use `hypothetical: true`. Never blend them into canon event counts or feed them into ordinary lore prompts. Do not use stale active forecasts to describe current conditions.

Use stable event IDs for new events. For old events, return a clearly read-only locator based on source revision and list index; do not pretend a text hash is a durable mutation identity.

Sort recent events by week descending then stable ID/source order. Default recent window: eight simulation weeks, with an all-history option. Paginate each long section independently, initially 20 items, and return total/more counts. Explicit relationships sort before mentions. Parent/child places appear as navigation; descendant contents are excluded by default, with an explicit include-descendants filter to avoid a world page accidentally showing everything.

Cycle detection is required for parent traversal. Unknown place references create findings with the source record; never drop them silently. A damaged optional source produces a section error and preserves the rest of the page. Invalid primary identity returns a clear 422; an unknown requested ID returns 404.

Initially build per request for correctness. If measurement warrants caching, key by all consumed file revisions and directory membership, including new/deleted entity pages, maps, state, facts, sessions, proposals, and forecasts. Invalidate after mutations and detect external Obsidian edits. Never cache solely by selected place or campaign name.

## APIs, UI, and CLI

Proposed routes under `/api/campaign/{name}`:

| Method/path | Behavior |
|---|---|
| `GET /places` | Search/list explicit and provisional places, parent filters, conflict counts |
| `GET /places/{id}` | Consolidated DM view with bounded sections |
| `GET /places/{id}/events` | Paginated explicit activity with week filters |
| `GET /places/{id}/mentions` | Paginated backlinks/mentions |
| `POST /places/link` | Confirm legacy/source-to-ID associations, with expected revisions |
| `PATCH /places/{id}` | Rename, parent, aliases, preferred map; preserve unrelated frontmatter/body |
| `POST /places/{id}/associations` | Typed association updates to allowlisted source records |

Mutation payloads specify source kind, source record ID, relationship, and expected revision. They do not accept arbitrary file paths or YAML patches. Require campaign containment for all resolved paths and artifact URLs. Store links as data and escape names, frontmatter values, and excerpts in HTML.

All writes use campaign locks, recovery-conflict checks, and the publication helper, with explicit file revisions captured before editing. Preserve page bodies and unknown frontmatter keys. A multi-file identity link is one transaction; duplicate IDs or stale references refuse the entire operation. If linking alters simulation state, existing pending state proposals become stale through ordinary revision checks.

The Places list should distinguish unlinked records and explain what linking enables. A page remains useful before linking: show lore and backlinks while precise event/project joins are unavailable. Empty sections should offer a relevant next action such as **Attach an existing map**, not automatically generate content.

Add `python studio/places.py show <campaign> <place-id> --json` and `--markdown` for the same DM read model. Add explicit `link` and `associate` subcommands using the same mutation service as HTTP. Printed Markdown must identify itself as DM material and label private/pending/hypothetical sections. It is not a player handout.

## Integration with the other two plans

The session editor uses this service's place picker and writes exact `place_ids` on each accepted consequence. Session-level context may suggest a location, but does not silently assign that location to every event.

Projects reference the same IDs and require no place view to operate. When projects are absent, the place page omits or empties the section without errors. When the project feature arrives, its events and progress can be displayed without introducing a new join format.

Facts added by the session workflow carry place IDs in the canonical knowledge store. Engine compilation need only include location metadata when it helps faction reasoning; the DM page reads the store directly. Party-disclosure badges come from the separate disclosure record and never infer party knowledge from `visibility: public`.

## Implementation phases

1. **Identity inventory and read-only slice:** add registry normalization and conflict reporting; show existing simulated places and lore pages, maps, faction homes, and mentions. No eager migration.
2. **Explicit links:** implement place linking, stable IDs, parent validation, map attachment IDs, and entity residence/presence metadata. Use transactions and preserve external edits.
3. **Activity and knowledge:** add event/fact place fields, exact joins, recent/history filters, source navigation, and party-disclosure badges. Keep historical unknown locations unknown.
4. **Projects and sessions:** connect their schemas, pending decisions, and hypothetical forecasts. These sections are optional until those features ship.
5. **Navigation and performance:** contextual links, primary-map preference, CLI Markdown, export/restore validation, and measured caching only if needed.

Likely files: new `studio/places.py`, `studio/routers/places.py`, `studio/static/js/places.js`; updates to entity frontmatter parsing in `spine.py`, links/index reuse, maps attachment metadata, knowledge/event schemas, server route registration, navigation, and CSS. Do not add direct Studio imports to the standalone engines.

## Acceptance tests and definition of done

- Open a lore-only place with no simulation or geography: its page and backlinks work.
- Open a legacy city campaign without writing any files; link it explicitly, rename it, and retain faction/map associations.
- Two places named “New Town” stay distinct; ambiguous aliases never auto-merge them.
- Regenerate a world where a burg ID is reused: existing place identity persists and provenance is flagged stale.
- Move a faction's home: old located events remain at their original place, and unlocated events remain unlocated.
- A note saying “we might visit the ferry” appears under mentions, not residence or canon events.
- Private facts remain visibly private; public but unrevealed facts remain absent from player disclosure.
- Pending session/project/week changes and forecasts remain visibly separate from canonical history.
- External page edits, new/deleted maps, and proposal decisions become visible on refresh; no stale cached joins.
- Cyclic parents, duplicate IDs, missing artifacts, malformed optional pages, and path traversal receive bounded, clear failures.
- Multi-file link publication survives injected crash recovery; retry does not duplicate IDs or associations.
- A fixture with 1,000 entity pages and 5,000 events stays bounded in response size; target under one second for a warm local read, measured before introducing caches.
- Export and restore the campaign; place IDs, source links, map preferences, and relationships remain resolvable without absolute machine paths.

Run place/index/API tests, existing vault/map/knowledge tests, then `python run-tests.py`. Complete the Ferry Crossing example through UI and CLI with one map, resident, faction, event, private fact, and pending change. No LLM calls are needed to verify this feature.

## Suggested delivery order across all three plans

Ship the read-only place slice first so existing data becomes easier to use. Establish stable place references while building the manual session draft/accept workflow. Add LLM session extraction after publication and evidence tests pass. Implement project rules independently with deterministic tests, then connect projects to weeks, sessions, and place pages. Each feature must remain useful when the other two are absent.
