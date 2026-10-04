# Session notes to reviewed campaign changes

Status: implemented 2026-09-29 (sessions store, session_changes proposals, bounded LLM extraction, Sessions tab, CLI) — except one item: the session-editor place picker (the server accepts place_ids; the create form does not yet expose them). Written 2026-09-27; re-check interfaces before coding the remainder.


## Outcome

After a session, the DM pastes rough notes, reviews a small set of proposed consequences beside their supporting passages, and publishes the chosen changes once. The next simulation uses those consequences, and the player handout contains only information deliberately disclosed to the party.

The feature succeeds when the DM can finish this workflow without translating notes into YAML or editing the same fact in multiple files.

Example input:

> The party burned the Mint's warehouse at the east quay. Its reserve coin was destroyed. They learned that Captain Rell takes bribes, but promised to keep it quiet. The Wardens now want to close the ferry.

Expected draft: a historical event at the quay; a suggested Mint resource change whose exact grade requires DM confirmation; a private knowledge fact with unresolved audience; and a suggested Wardens goal change. Learning a secret does **not** automatically make it public or eligible for the existing public-fact handout. No consequence is applied merely because the extractor returned it.

## Existing foundation

- `studio/routers/simulation.py`: `SessionBody`, `post_session`, `_effect_id`, proposal acceptance. The existing manual form stages events and typed effects against a captured state; preserve it.
- `agentic-simulator/src/ingest.py`: deterministic bullet extraction and standalone application.
- `agentic-simulator/src/effects.py`: allowlisted `set_resource` and `set_field` effects, validation, application, stable effect deduplication.
- `studio/proposals.py`: persisted proposal bundles and state diffs; currently assumes a state after-image and one pending proposal per campaign.
- `studio/publication.py`: revision checks, multi-file publication, recovery, and receipts.
- `studio/knowledge.py`: approved fact store and compiled faction knowledge.
- `studio/briefing.py`: party disclosure copies approved public fact text; its current restriction remains in force.
- `studio/llm_client.py`, `jobs.py`: provider configuration, metering, asynchronous jobs.

Today these pieces do not offer one source-linked review of notes, state effects, knowledge, and disclosure. Add orchestration around them; do not duplicate their validators or implement another publication mechanism.

## Scope and boundaries

Version one includes immutable source notes, optional LLM extraction, manual drafting, editable suggestions, evidence links, an explicit selection step, deterministic preview, and atomic acceptance of one selected batch.

Do not include audio transcription, automatic NPC creation, automatic rewriting of lore pages, or autonomous acceptance. A faction goal change uses the existing `set_field` mechanism. Project progress becomes another supported suggestion only after the project plan's validator exists.

Session records do not advance the simulation clock. The form defaults the event week to the current simulation week and displays it prominently. Future-dated events require an explicit override and explanation. Keep the standalone ingest CLI's existing default compatible unless a separately documented migration changes it.

## User workflow

1. Open a Sessions panel from Simulation. Enter a title, event week, optional places, and notes. Saving requires no API key.
2. Save the notes as an immutable revision. Editing later creates a new revision; it never changes evidence behind an existing proposal.
3. Choose **Extract changes** and confirm the metered run, or add suggestions manually.
4. Review cards grouped as history, resources, faction fields, knowledge, and party disclosure. Each card shows source quotation, affected subject, current value, proposed value, and validation findings.
5. Resolve unknown entities and places using existing records. Ambiguous names stay unresolved; they cannot become mechanical targets through fuzzy matching.
6. Select, edit, or exclude individual suggestions. Mechanical grades and knowledge visibility require explicit confirmation. Show excluded suggestions in the session audit record.
7. Choose **Prepare selected changes**. This produces a persisted, read-only proposal and a combined diff. Nothing in canon changes.
8. Accept the batch or discard it. Acceptance publishes state, knowledge, disclosure, and the session receipt together. Show snapshot failure separately from successful publication.

A raw draft may coexist with a pending simulated week. Preparing a publishable session proposal must use the existing single-pending-proposal rule. Offer to return to the existing proposal; do not silently discard it or rebase the session.

## Files and identity

Proposed campaign layout:

```text
sessions/<session-id>/session.yaml
sessions/<session-id>/notes/0001.md
sessions/<session-id>/drafts/<draft-id>.yaml
sessions/<session-id>/decisions/<proposal-id>.yaml
simulation/proposals/<proposal-id>/...
```

These paths are new. Raw notes and drafts are DM material, not canonical world facts. Exclude them from general lore generation, faction prompts, and player exports. Include them in the normal private campaign export and campaign snapshot, subject to existing credential/noise exclusions.

Use server-generated UUID-based session and suggestion IDs. An extraction retry creates a new draft ID; it does not mint a new session. Give suggestions IDs when the draft is saved, not whenever text is edited. An already accepted suggestion cannot be reapplied after changing its wording: corrections create new suggestions with `corrects` provenance.

Illustrative draft schema (new contract):

```yaml
schema_version: 1
session_id: session-9fa410c2
draft_id: draft-b09e7da1
notes_revision: "sha256:<digest>"
event_week: 8
base_revisions:
  simulation/state.yaml: "sha256:<digest>"
suggestions:
  - id: suggestion-a3100abc
    kind: resource_change
    selected: false
    faction_id: mint
    resource: coin
    expected_before: high
    after: low
    requires_confirmation: [resource_grade]
    reason: The reserve warehouse was burned.
    place_ids: [east-quay]
    evidence:
      - notes_path: notes/0001.md
        line_start: 1
        line_end: 1
        quote: "Its reserve coin was destroyed."
    validation: []
```

Revision strings above are illustrative; use the repository's existing revision representation, not a second hashing format. Resolve note paths strictly beneath this session directory. Line numbers are one-based and refer to the preserved notes revision. Validate that the quotation occurs within the cited range. Missing or invented quotations produce an unresolved suggestion, never fabricated evidence.

Suggestion variants:

| Kind | Required reviewed content | Canonical destination |
|---|---|---|
| `history_event` | actor, text, week, optional place IDs | state events |
| `resource_change` | faction, resource, exact before/after, reason | existing resource effect |
| `field_change` | faction, allowlisted field, before/after, reason | existing field effect |
| `knowledge_fact` | text, subjects, explicit visibility | knowledge store and compiled state |
| `party_disclosure` | existing fact ID or selected new-fact suggestion ID | disclosure store |

Default proposed knowledge visibility to `dm_only`. Creating a public fact and revealing it are separate checked selections. A disclosure that depends on an excluded fact is invalid. Only current public facts may be revealed in this version; party discovery of a private secret remains a private session note until a separate design expands disclosure policy.

Two selected changes to the same faction field/resource are a conflict, not an implicit sequence. Ask the DM to consolidate them into one final change. Knowledge supersession should use an explicit `supersedes_fact_id`; never infer replacement from text similarity.

## Extraction and grounding

Capture the state, known entities, place identities, and relevant approved facts before dispatch. Hash the exact bytes used in the prompt. Do not hash newly read files after the model returns and claim they were its input.

Use a bounded prompt containing the source notes, roster IDs, place IDs, current resource grades, allowed effect types, and relevant knowledge. Treat notes as quoted data. Model instructions must not authorize writes, provider changes, or further tool calls.

Initial limits: 30,000 characters of notes per extraction, 30 suggestions, and at most two completion attempts within the run's configured provider-call ceiling. These are implementation defaults, not billing estimates. Reject oversize input with guidance to split it; do not silently truncate the notes. Meter transport retries as actual attempts.

The model proposes semantic mappings, never trusted IDs, preconditions, or executable effects. The adapter resolves IDs, supplies current values from the captured snapshot, validates evidence, and then delegates effect validation to the engine. A malformed response leaves the notes intact and the manual editor available. Do not return “no changes” for a failed extraction.

## Publication contract

Introduce proposal kind `session_changes`; keep old `session_effects` bundles readable and acceptable. Do not simply add the new kind to a state-only allowlist: it can write several stores.

Preparation must:

1. Capture exact source bytes/revisions under the campaign lock and check for an existing pending proposal.
2. Compute selected effects against a temporary state through the engine CLI, leaving the campaign unchanged.
3. Build knowledge and disclosure after-images in memory. Refactor pure document transformations out of their existing save functions so orchestration does not call several independent publications.
4. Compile the final knowledge document into the **post-effect** state. Starting again from the old state would lose resource/goal changes.
5. Construct one proposal containing every after-image, selected suggestion IDs, evidence, notes revision, expected base revisions, and the combined diff.
6. Reacquire the lock, recheck revisions and the pending slot, then stage. A changed base returns 409 without a partial proposal.

Acceptance validates all base revisions, including knowledge/disclosure absence where relevant, and publishes all after-images plus the session decision record through one `publication.publish` call. Its stable idempotency key derives from campaign, session, and immutable proposal ID. Applied-effect IDs derive from session/suggestion IDs and persist across retries.

Draft selection is the only partial-approval boundary in version one. A prepared batch is accepted as a whole. Excluded suggestions stay in the draft for later preparation against a newly captured base. Already accepted suggestions are disabled; do not carry obsolete timeline/state revisions into the remaining selection.

After a crash, canon and the session acceptance receipt must recover together. Reconstruct UI status from the receipt if proposal status bookkeeping did not finish. A revision conflict offers **Rebuild preview**; it must display changed preconditions and require a new review, not auto-accept the rebuilt result.

## API and CLI additions

All routes below are proposed, under `/api/campaign/{name}`:

| Method and path | Behavior |
|---|---|
| `POST /sessions` | Save notes revision and session metadata; 201 |
| `GET /sessions`, `GET /sessions/{id}` | List sessions and load notes/drafts/decisions |
| `POST /sessions/{id}/notes` | Append immutable notes revision using expected session revision |
| `POST /sessions/{id}/extract` | Explicit confirmation; 202 job; persist extraction output |
| `PUT /sessions/{id}/drafts/{draft}` | Validate edits and expected draft revision; 409 on stale editor |
| `POST /sessions/{id}/prepare` | Selected IDs and expected draft revision; stage proposal |

Use existing proposal accept/discard endpoints after extending their dispatch. Preserve the existing manual session endpoint as a compatibility path. Expensive extraction is a job; acceptance is a short locked publication. Preparation may use a job if engine subprocess latency warrants it; document its status code and polling contract consistently.

Add `studio/session_notes.py` with CLI commands `import`, `extract`, `prepare`, `accept`, and `discard`, taking a campaign and explicit session/proposal IDs. `extract` requires `--confirm-spend`; `accept` uses the same service layer as HTTP. Support manual draft files without an LLM. Preserve standalone `ingest.py` and `effects.py` independently of Studio.

## Implementation phases

1. **Storage and manual review:** implement session store, source revisions, typed draft validation, and a no-LLM editor. Add a session fixture with ambiguous names and an unsupported claimed consequence.
2. **Deterministic preparation:** refactor pure knowledge/disclosure transforms; extend proposal serialization, diff, and acceptance for the combined transaction. Ship manual end-to-end use before extraction.
3. **Bounded extraction:** add grounded prompt/response adapter, jobs, metering, evidence validation, and unresolved mappings.
4. **Product integration:** add Sessions entry point, review cards, stale-preview recovery, session receipts, and links into the place view. Update API docs and tutorial with one complete session.

Likely files: new `studio/sessions.py`, `studio/session_notes.py`, `studio/routers/sessions.py`, `studio/static/js/sessions.js`; changes to server route registration, `proposals.py`, `knowledge.py`, `briefing.py`, simulation acceptance, and shared review UI. Engine effects need only additive event provenance/place metadata until projects arrive.

## Acceptance tests and definition of done

- Paste the example notes, resolve identities, select a resource change and goal change, accept, and observe both in the next engine view.
- Save and manually prepare a session with no API key; no network request occurs.
- Stage a batch: state, knowledge, and disclosure bytes remain unchanged before acceptance.
- Accept the same proposal twice and after restart: each effect, fact, disclosure, and receipt appears once.
- Inject failure between each publication write; restart yields complete recovery or an explicit conflict, never silently accepted partial state.
- Edit knowledge/state externally after preview: acceptance is 409 with no writes. Rebuild preview shows the changed values.
- Exclude a private fact but select its dependent disclosure: preparation refuses with the dependency named.
- Accept one selected subset, then prepare previously excluded suggestions: own prior publication does not permanently stale the session.
- Rename an entity, duplicate a name, invent a quote, or return malformed model JSON/YAML: no wrong target is applied.
- Exceed request/size limits: no hidden chunking or extra spend; notes remain recoverable.
- Old `session_effects` proposals and standalone ingestion tests still pass.

Run targeted session/publication/knowledge tests, then `python run-tests.py`. Add one mocked-provider cross-module scenario that uses the real effects subprocess. No live API credits are required. Completion means the example can be accomplished through both UI and CLI and exported/restored with evidence and receipts intact.
