# Campaign Studio: close the play-to-canon loop

Implementation handoff based on a source review on 2026-09-24.

## Instructions for the implementing model

Implement this document in dependency order. Do not merely summarize it or produce another plan. Complete one phase, test it, update the progress checklist, and then continue. If the user assigns only one phase, stop after that phase. Never claim a later phase is finished because you created placeholders for it.

This document contains both observed problems and proposed implementation decisions. The observations describe the code as reviewed; the decisions below are the recommended new behavior. Recheck the current code before editing because another task may have changed it.

Use the smallest implementation that satisfies the stated behavior. Do not add a database, framework, vector store, agent orchestration platform, or cloud service. Do not redesign the mapping engines. Do not spend API credits or modify real campaign data to test your work.

The project root at review time was `C:\Users\matth\Documents\D&D`. All paths below are relative to the project root so this document remains portable.

## The outcome to build

A DM can complete this cycle:

1. Record something that happened during actual play.
2. Review and approve its explicit changes to campaign state.
3. Generate a proposed simulated week using approved facts and faction-specific knowledge.
4. Inspect the proposed state changes and accept or discard the whole week.
5. Produce a concise DM briefing and a separately controlled player handout.

Nothing in a proposed week changes the live campaign until approval. Discarding a proposal leaves the live campaign unchanged. Approval is recoverable after a crash and cannot silently overwrite intervening edits.

## Read first

- Root `README.md`, `campaign-studio-design.md`, `obsidian-integration.md`.
- `studio/README.md`, `studio/API.md`, `studio/tests/conftest.py`.
- Applicable `AGENTS.md` files in every engine you change. Read the additional documentation those files require.
- `agentic-simulator/README.md` for the actual engine scope. `infinite-dm-design.md` is a broader vision, not a claim that everything there exists.

Preserve these existing constraints:

- Campaigns are directories of readable files. Campaign data lives in the separate, nested `campaigns/` Git repository and must not be added to the parent code repository.
- Studio invokes engines through headless CLIs. No Python imports across project boundaries. New engine functionality must remain usable without the web app.
- Bind to localhost. Preserve existing path validation, host checks, and HTML escaping.
- Use list-form subprocess arguments, never shell command strings. The root path contains `&`.
- Preserve existing human-authored files, unknown YAML fields, stable IDs, aliases, and links.
- Structural prose findings remain advisory. Invalid state mutations, unknown IDs, and stale approvals are correctness errors, not prose findings.
- No recursive acquisition of the same campaign lock. Existing campaign locks are not reentrant. Preserve a consistent lock order.
- Keep old campaigns readable. Add versioned formats for new records and an explicit compatibility path for old pending records.

## Observed gaps and source locations

| Gap | Evidence in the reviewed implementation | Consequence |
| --- | --- | --- |
| Week review happens after state changes | `studio/routers/simulation.py`, `_run_week_locked()` copies the new state and chronicles into the campaign before staging timeline entries | Deleting a proposed timeline entry does not veto the simulated event |
| Review deletion only edits the pending file | `studio/routers/review.py`, `post_delete()` | Resources, rumors, clock, and events remain changed |
| Approved lore does not feed faction prompts automatically | `_copy_state_in()` copies only simulation state; `agentic-simulator/src/views.py`, `faction_view()` builds a view from state and rumors | Lore and simulation can disagree despite the design's promised loop |
| Session ingestion records text without applying effects | `agentic-simulator/src/ingest.py`, `apply()` appends events only | A treasury can be destroyed in history while its money remains available |
| Simulation effects are very limited | `agentic-simulator/src/tick.py`, `apply_mechanical()` changes a resource grade and records an event | Success prose can imply asset, leadership, or relationship changes that never occur mechanically |
| Model risk preference determines difficulty | `agentic-simulator/src/resolve.py`, `resolve_action()` maps `risk_acceptance` to DC | A model's willingness to risk failure substitutes for objective difficulty |
| Unknown targets and inactive defenders bypass contests | `agentic-simulator/src/llm.py`, `propose()` warns on unknown targets; `resolve_tick()` contests only against factions with proposals | Typos and scheduling decisions can change opposition incorrectly |
| Application locks cannot coordinate external editors | `studio/spine.py`, `campaign_lock()` uses in-process locks; review requests carry IDs but no content revision | Obsidian edits can make generated drafts or approval screens stale |
| Snapshots are best effort | `studio/canon.py`, `snapshot()` returns `None` for failures and no-op cases | A successful mutation does not guarantee a working undo point |
| Multi-file publication is not transactional | `_run_week_locked()` writes state, chronicles, and pending entries sequentially | A failure after one write can leave a partially published week |
| Briefings and player knowledge are mostly future design | Larger simulator design describes them; the current Studio workflow centers on generation and review | The DM must still turn output into usable session preparation |

Do not describe player knowledge, retcons, or briefings as ideas the owner never considered. They appear in the larger design. The problem is their absence from the working end-to-end workflow.

## Phase 0 — Establish an honest baseline

1. Read `git status` and preserve existing changes.
2. Run `python run-tests.py`. Record actual results.
3. Keep tests in temporary campaign and engine directories; use fake LLM and engine responses.
4. Fix test log isolation before adding substantial new tests. At review time tests wrote to the real `studio/logs/studio.log` and emitted Windows `PermissionError: [WinError 32]` during rollover. Inspect `studio/server.py`, `_setup_logging()`, and test fixtures. Make the log location configurable and arrange for tests to configure it before server import. Close test-owned handlers at teardown. Do not stop a running user server or delete its logs.
5. Distinguish the observed log-file contention from its root cause until reproduced. Do not hide logging errors globally to make tests appear clean.

Acceptance:

- The fast suite completes without writing to real campaign data or rotating the user's active log.
- Test failures and skipped suites remain visible.

Review-time baseline: all seven fast suites passed; simulator had 89 pytest tests, Studio had 214 passing tests and 9 deselected integration tests. There were also direct-run mapping, verification, and JavaScript tests. Two dependency deprecation warnings and the Windows rollover errors appeared. Real-engine integration tests and live LLM calls were not run. These counts are historical, not targets to hardcode.

## Phase 1 — File revisions and recoverable publication

Build this foundation before changing week approval.

### Revision contract

- A revision is a SHA-256 hash of exact file bytes, with an explicit sentinel for an absent file. Do not rely on mtime.
- A generated draft records hashes for the files it read. An approval request also carries the revision of the pending record the user actually viewed.
- Include a directory-membership fingerprint when generation depends on a set of files, such as all entity pages. Creating or deleting a page must invalidate the old input set.
- Compare revisions under the campaign lock immediately before publication. A mismatch returns HTTP 409 with affected relative paths and a refresh/review instruction. Write nothing.
- CLI commands expose equivalent conflict behavior and a nonzero exit status.
- Do not introduce a blind `force=true` escape hatch. A refreshed preview and explicit resubmission are the recovery path.
- Do not claim a hash check prevents every race with an editor that ignores application locks. Minimize the check/write window, preserve before-images, and document that exclusive cross-process editing is not guaranteed.

### Recoverable multi-file publication

Introduce a small Studio-owned publication helper; a proposed new module name is `studio/publication.py`.

- Prepare a manifest containing a transaction ID, operation type, base revisions, intended file paths, before-images, after-images, and status.
- Store transaction payloads outside normal lore/entity/pending globs so readers and generation never mistake recovery data for canon.
- Restrict all target paths to the selected campaign. Reject traversal and unsafe symlink escapes.
- Finish writing the payload and manifest before replacing any live file. Use unique temporary paths, not one globally reused temporary filename.
- Represent created and deleted files explicitly so rollback restores absence as well as content.
- Mark publication complete only when all expected live files match the after-images.
- On startup, inspect unfinished transactions before serving normal campaign operations. If current bytes match recorded before/after states, recover deterministically. If bytes match neither, preserve them and flag a conflict; never overwrite an unrecognized external edit.
- While recovery is unresolved, reads and writes for that campaign must report a clear recovery state instead of presenting a mixed state as canonical. Other campaigns remain usable.
- Repeating a completed approval returns its recorded result without duplicating events or advancing the clock again.
- Readers during publication must use a consistent view or report that publication is in progress. A sequence of atomic file replacements is not itself an atomic multi-file transaction.

### Snapshot reporting

- Add a structured snapshot result distinguishing committed, no change, unavailable, and failed. Preserve compatibility for existing callers while migrating them.
- If publication succeeds but Git fails, report `published; history snapshot failed`, retain recovery material, and offer a snapshot retry. Do not falsely report a rollback or rerun the mutation.
- Make this visible in job/API results and the UI, not just logs.

Tests:

- Stale source, changed pending record, and added/deleted source page each produce a conflict with no writes.
- Inject failure after each publication write; restart recovery reaches a consistent result or clearly blocks on an external-edit conflict.
- Duplicate submission applies once.
- Git unavailable and Git commit failure are visible and distinguishable from no changes.
- A partial transaction cannot be read as a completed canonical week.

## Phase 2 — Generate a week proposal, then approve the whole week

Likely touchpoints: `studio/routers/simulation.py`, `studio/routers/review.py`, `studio/spine.py`, `studio/static/js/sim.js`, `studio/static/js/review.js`, and associated tests.

### Decisions

- One unresolved proposed week per campaign for the first version.
- Whole-week accept/discard only. No selective event veto, because later outcomes and rumors may depend on earlier ones.
- A discarded proposal is not canonical history. Keep an audit receipt without feeding rejected content into future prompts.
- Keep the existing standalone `tick.py --live` direct-advance behavior unless a new explicit proposal flag is used. Studio must run against scratch data and publish only on approval.
- Existing `pending-week-*.yaml` records describe weeks already committed by the old implementation. Label them as legacy timeline-only reviews. Never apply their simulation effects again or pretend deleting them undoes a week.

### Proposed record shape

Use a versioned bundle under `simulation/proposals/<proposal-id>/`. This is a new proposed format, not an existing one. Persist enough information to resume review after a server restart:

```yaml
schema_version: 1
proposal_id: <unique ID>
kind: simulated_week
status: pending
base_week: 3
proposed_week: 4
base_revisions: {}       # relative paths to exact-byte hashes
input_set_revisions: {} # source-directory membership fingerprints
artifacts: {}            # relative paths to staged state/chronicle/etc.
run_record: {}           # seed, engine version, model/config identity; no secrets
```

Do not place full prompts or secrets in ordinary UI logs. Keep any detailed replay inputs in campaign-private files. Store validated proposals as well as the RNG seed: a seed alone cannot replay a fresh nondeterministic LLM response.

### Behavior

1. Capture a consistent base and source revision manifest.
2. Run the engine against scratch state using the existing serialization around shared engine directories, or explicit new CLI path arguments. Preserve standalone behavior.
3. Stage candidate state, chronicle, timeline entries, and a structured before/after diff. Live clock, resources, rumors, events, timeline, and chronicles remain unchanged.
4. Return a persistent proposal ID from the ephemeral job. Review works after restart.
5. Show changed resources, facts, assets, relationships, clock, and events alongside the chronicle. Explain that the world has not advanced yet.
6. Accept validates revisions and publishes the whole bundle through Phase 1. The selected timeline entries for this first version are part of the whole approved bundle, not a second mandatory approval step.
7. Discard changes only proposal status/audit metadata. It must not touch live simulation or lore files.
8. Reject requests to simulate another week while an unresolved proposal exists. Tell the user to accept or discard it.
9. Block publication of a structurally invalid candidate. If all faction calls fail, report generation failure rather than quietly advancing an empty week. A genuinely quiet scheduler-held week remains valid; distinguish these cases.

Acceptance tests:

- After generation, all live canonical files are byte-identical to their pre-run versions.
- Discard preserves those bytes.
- Accept advances exactly one week and publishes all related artifacts once.
- Approving again is idempotent, including after restart.
- A failed engine leaves no approvable partial bundle.
- Changes in Obsidian between generation and approval cause a conflict.
- Legacy timeline-only reviews remain usable and cannot apply effects twice.
- Update old tests that intentionally expected immediate advancement. Do not delete their unrelated coverage.

## Phase 3 — Shared, explicit state effects and session ingestion

Likely touchpoints: `agentic-simulator/src/ingest.py`, `tick.py`, `store.py`, a new engine-local effect validator/applier, Studio simulation/review routes, and tests.

### Minimum scope

Start with a reviewed resource-setting effect and typed fact updates. Extend to assets and relationships only with an explicit schema; do not turn prose into arbitrary dictionary edits.

Example proposed resource effect:

```yaml
id: effect-unique-id
type: set_resource
faction_id: mint
resource: coin
expected_before: high
after: none
reason: The party destroyed the treasury.
source: session:session-08
```

- Validate type, entity IDs, field allowlist, value ranges, and preconditions before any write.
- Use stable effect/event IDs for deduplication. Reapplying the same event must not repeat effects.
- Preserve source provenance and actual in-world date/week. Do not silently date every session event to next week when the DM supplies a date.
- Provide an editable session-input form and CLI path. LLM extraction is optional; manual structured effects must work without an API key.
- Proposed effects appear in the same before/after review as simulated effects.
- Historical text alone must be visibly labeled as having no mechanical effect. Do not infer a resource deletion from arbitrary prose without review.
- Use the same engine-local validation/application code for session effects and simulated effects. Studio calls it through a CLI; do not import it across projects.
- Approved session effects use the publication helper so state and historical records stay consistent.
- The narrator may describe only accepted structured consequences. Unsupported effects remain proposals or explicit unresolved claims.

Acceptance scenario:

1. Fixture faction `mint` starts with `coin: high`.
2. Enter a session event about its destroyed treasury and an explicit `set_resource` effect.
3. Before approval the faction still has high coin.
4. After approval it has no coin and the sourced event exists exactly once.
5. The next faction view exposes the updated resource level.
6. Reimporting or reapproving the event does not duplicate it.
7. Invalid IDs, stale preconditions, and invalid resource values change nothing.

## Phase 4 — Approved facts enter the correct faction views

Likely touchpoints: Studio lore/simulation integration, `agentic-simulator/src/views.py`, `llm.py`, `store.py`, and view tests.

Do not dump the entire vault into every faction prompt. Approval makes a fact canonical; it does not make that fact public.

Use a small explicit fact record with:

- Stable ID and text.
- Source file and approved source revision.
- Subject/entity IDs where applicable.
- Visibility: `dm_only`, `public`, or an explicit list of faction IDs.
- Validity/current status sufficient to supersede a fact without deleting its provenance.

Unclassified existing lore defaults to `dm_only`. No automatic mass publication of existing pages. The DM can select a fact and classify its visibility. If extracting candidate facts with an LLM, stage them for review first.

Studio compiles approved classified facts into an explicit simulator input file or the simulation state's knowledge section through a CLI-compatible contract. Keep editorial prose, simulation state, and knowledge visibility separate in meaning, even if they share a YAML file.

Facts with changed sources become stale and require reconciliation; never silently substitute newly edited text into previously approved public knowledge. Display staleness to the DM.

Faction views include only current public facts and facts explicitly known to that faction. Player knowledge is separate: public in-world information is not automatically information the party has discovered.

Tests:

- An approved public bridge closure reaches faction views.
- A secret known to faction A does not occur anywhere in faction B's serialized prompt.
- Rejected, pending, DM-only, and superseded facts do not leak.
- Source changes produce visible staleness instead of unreviewed knowledge changes.
- A campaign with no knowledge records still works.

## Phase 5 — Resolver correctness before richer simulation

Likely touchpoints: `agentic-simulator/src/resolve.py`, `llm.py`, `consistency.py`, `tick.py`, `scheduler.py`, and their tests.

Do these as separate small changes:

1. **Validate target identity.** Use typed target categories and canonical IDs. Permit supported district/place targets explicitly. Reject or return for correction unknown targets; they must not receive an easier uncontested roll. Cosmetic prose findings remain advisory.
2. **Separate risk preference from difficulty.** For the first version use a documented deterministic default DC for supported unopposed actions, with difficulty overrides owned by trusted rules/DM input. The LLM must not choose DC by declaring low risk. Keep `risk_acceptance` descriptive until it has a separately specified mechanical effect.
3. **Defend while inactive.** A targeted faction gets a deterministic defensive resolution from its state even when the scheduler holds it or its proposal call fails. Do not call an LLM just to defend. A defense result is not a successful offensive action for that faction. Document one simple defense-bonus rule and keep it distinct from offensive resource commitments.
4. **Retain replay inputs.** Save normalized proposals, base-state identity, rule version, and seed so the resolver can replay without calling the model. Add a CLI replay path if one does not already exist.

Tests must establish behavior, not merely mirror the implementation:

- Identical supported actions with different `risk_acceptance` have identical DCs.
- A typo in a target never becomes uncontested success.
- An inactive defender still opposes an attacker.
- Defense alone does not execute or reward an invented offensive action.
- Mutual targeting does not create duplicate contests; test multiple attackers too.
- Replaying fixed normalized inputs and a seed yields the same mechanical result without network calls.

The suggested fixed DC is a conservative implementation default, not a claim of balanced game design. Keep it configurable through trusted rules and document it for later playtesting.

## Phase 6 — Briefings, player disclosure, and review workload

Build a deterministic first version. Do not require another LLM pass to make approved state useful.

### DM briefing

- Select an explicit since-session marker or week range and optional focus entities/place.
- Show the most relevant approved changes, affected NPCs/factions/places, unresolved DM decisions, and links to supporting records/maps.
- Cap the initial display (suggested default: ten items) and show how many more exist. Never silently truncate history.
- Label any proposed hooks or interpretations as proposals. Do not invent clues or discovery opportunities as established facts.
- Provide durable Markdown output and a browser view.

### Player handout

- Use a separate party/player-disclosure record and explicit reveal action.
- Export only disclosed facts. An empty disclosure set produces an empty handout with a helpful explanation, not a fallback to the DM briefing.
- Never include private goals, mechanical roll tables, faction-only knowledge, pending proposals, or links that reveal secret file titles.
- Use a deliberate export path so sharing a handout does not require sharing the campaign directory.

### Review workload

- Show pending counts and group items by generation run/source.
- Let users limit missing-page generation before it runs. Do not recursively expand every new dangling link.
- Support deferring a draft without approving it, discarding it, or having generation recreate it immediately.
- Token logs are useful but are not a spending budget. Add an explicit per-run call/token ceiling before considering monetary estimates; stop between calls with a clear incomplete status when exhausted. Do not label estimates as exact provider charges.

Acceptance:

- A DM can navigate from a briefing item to its approved source and relevant existing map.
- A secret included in DM context is absent from player export, including titles and metadata.
- Briefings use the requested range and do not include rejected or pending events.
- Five-minute preparation is a human playtest goal. Report whether a human tested it; passing unit tests cannot prove it.

## Phase 7 — Backup and restore drill

- Provide a documented local export command/workflow for the entire campaign and a manifest of included file hashes. No automatic upload or cloud dependency.
- State clearly whether the export includes Git history. The shared history lives at `campaigns/.git`, not inside each individual campaign folder; exporting one folder does not include that history.
- Exclude credentials and incidental logs. Do not accidentally package other campaigns in a single-campaign export.
- Restore to a new directory, never over a real campaign by default.
- Validate paths when extracting archives, verify hashes, and open the restored campaign with the app's loaders.
- A same-disk archive is an export, not protection from disk loss. Give instructions for copying it to a user-chosen separate destination without claiming that has occurred.

Acceptance: restore a fixture with entities, links, maps, timeline, state, and proposal metadata to a fresh temporary directory, then successfully load and inspect it. Record exactly which recovery/history guarantees the export provides.

## Test and documentation discipline

- Run focused tests after each phase, then `python run-tests.py` at the end of that phase.
- Run relevant real-engine integration tests for changes to engine invocation when dependencies are present. Report missing prerequisites instead of silently skipping or making paid calls.
- Add failure-injection tests for approval/recovery and privacy tests for views/exports. Avoid superficial tests that assert only that a new helper exists.
- Update `studio/API.md`, `studio/README.md`, `TUTORIAL.md`, and affected engine documentation as behavior changes. Clearly mark design proposals versus implemented behavior.
- Preserve the error-response convention and existing UI escaping.
- Do not make a destructive migration of live campaigns. Use temporary fixtures to exercise legacy compatibility.
- Do not weaken tests to retain an obsolete immediate-commit behavior or change assertions solely to obtain green output. Explain intentional contract changes.

## Integrated completion scenario

Create a temporary test campaign with two factions, a bridge, a treasury, one public fact, and one secret known only to faction A. Use fake engine/model responses.

1. Import a session event with a reviewed treasury resource effect; approve it once.
2. Verify live state, history, and faction views agree.
3. Generate a week using the updated resources and correctly filtered facts.
4. Verify live state is unchanged while the week is pending.
5. Edit an input file externally; approval must conflict without overwriting it.
6. Discard that stale proposal and generate a fresh one from the new base.
7. Inject a failure during publication, restart, and recover without duplicate effects or a mixed canonical state.
8. Confirm a repeated approval returns the original result.
9. Generate a DM briefing and explicitly reveal one fact to the party.
10. Export a player handout; verify the secret and private metadata are absent.
11. Export and restore the campaign into a new temporary directory; verify its contents.

This scenario is the completion gate. A collection of working screens with inconsistent underlying state is not completion.

## Progress checklist

Update this section as work actually finishes. Include test commands/results and any remaining limits in a short phase note.

- [x] Phase 0: baseline and isolated test logging.
  Phase note: baseline before changes — all seven fast suites passed (studio 214 passed / 9 deselected); two deprecation warnings plus logging noise at rollover. Root cause reproduced standalone: a second process holding studio.log open makes RotatingFileHandler.doRollover fail with WinError 32 on the backup rename; the real studio.log sat at 999,977 / 1,000,000 bytes, so every test run pushed it over the cliff while the live server held it. Fix: STUDIO_LOG_FILE env override in server._log_file_path() (absolute or studio-relative; default unchanged), studio/tests/conftest.py points it at a session temp dir before importing server, and a session-autouse fixture closes test-owned RotatingFileHandlers at teardown. After: 215 passed (+1 env-override test), zero logging noise, real studio.log byte-identical (md5) and unrotated after the run. No logging errors are suppressed anywhere.
- [x] Phase 1: revision checks, recoverable publication, visible snapshot status.
  Phase note: new studio/publication.py — sha256 byte revisions with the "absent" sentinel (never mtime), directory-membership fingerprints, transactional publish (stage after-images + manifest in campaign/.studio-transactions/<tx>/ with a .gitignore keeping payloads out of canon snapshots, then apply, then verify before marking complete), boot recovery rolls open transactions forward and parks external-edit conflicts (never overwritten on its own; POST /publication/resolve apply|discard), receipts make repeated approvals return their recorded result (disk-backed, restart-safe). Review approve/delete now: send expected_revision (GET review files[].revision) -> 409 stale review with nothing written; validate generated drafts' base_revisions + input_fingerprints (loregen records them); publish transactionally; report snapshot {"state": committed|no_change|unavailable|failed} in approve/delete/week/create-state responses. canon.snapshot_result() added (snapshot() kept as compatibility wrapper); POST /campaign/{name}/snapshot retries and prunes retained payloads; campaign summary exposes conflict state; review GET adds a conflict note. UI: review.js sends the viewed revision, toasts snapshot failures. 19 new tests (test_publication.py) covering every Phase 1 acceptance row; studio 234 fast + 9 integration pass; intentional contract change: approve/delete responses carry transaction+snapshot (tests updated, coverage kept). CLI note: no approve CLI exists (assemble.py is a one-time importer); conflict behavior lands with the Phase 2 proposal CLI path. Publication writes LF bytes; revision checks hash real disk bytes so mixed line endings are handled honestly.- [x] Phase 2: whole-week proposal/accept/discard workflow and legacy handling.
  Phase note: new studio/proposals.py — proposal bundles at simulation/proposals/<id>/ (proposal.yaml + artifacts/{state.yaml, chronicle/, timeline-entries.yaml, diff.json}) with run_record carrying the week's RNG seed (from meta.rng_seeds) and model identity (no secrets). POST /simulation/week now GENERATES: captures base revisions (state+timeline bytes, chronicle+entities membership fingerprints), copies into the engine home, runs tick --live, stages the bundle; live campaign is byte-identical throughout. Invalid candidates are job errors (engine failure; clock not advancing exactly one week; no chronicle; every faction call failed-or-held with >=1 failure) — a quiet scheduler-held week is a valid proposal. New routes: GET /simulation/proposal (restart-safe bundle read), POST .../proposal/{id}/accept (whole-week transactional publication via publication.py, base-revision validated -> 409 stale proposal with nothing written, idempotent receipt, snapshot reported, chronicle linkify after publish), POST .../proposal/{id}/discard (bundle status only; audit receipt kept). Exactly one pending proposal; week route refuses (409) while one is unresolved or a recovery conflict is parked. Legacy pending-week files labeled in Review GET ("legacy": true + explanatory note) — approving stays timeline-only, never re-applies effects. sim.js leads the tab with the proposal panel (diff table, proposed events/rumors, chronicle preview, Accept/Discard with confirms); run button re-labeled "Propose week". Old tests updated to the new contract (immediate-advance expectations intentionally replaced; copy-in and linkify coverage kept); 12 new/rewritten flow tests. All 7 fast suites green (studio 248 tests). Standalone tick.py --live untouched. CLI conflict behavior now exists implicitly at the API level; the direct-advance CLI path remains tick.py --live by design.
- [x] Phase 3: reviewed session effects and shared effect application.
  Phase note: new agentic-simulator/src/effects.py — the ONE validator/applier for structured effects (v1: set_resource with coin/muscle/legitimacy + none/low/medium/high; set_field with goal/kind/secret allowlist; optional expected_before preconditions; stable effect ids deduped via meta.applied_effects; sourced events carry the effect id and the DM's explicit week — never silently rerolled to next week). History bullets are recorded verbatim with NO mechanical effect; a bullet restating an effect's reason dedups against the effect's audit event (effects apply first). All-or-nothing: one invalid effect refuses the whole batch, nothing written. CLI: python src/effects.py (--check|--apply) session.yaml [--state PATH] (exit 0/1/2); ingest.py --apply now delegates to the same code (reimport-safe via event dedup). Studio: POST /simulation/session stages a session_effects proposal — engine validates via --check against the live state, the after-image is produced by --apply against a THROWAWAY copy; accept dispatches on bundle kind and publishes through publication.py (base-revision validated, idempotent). Manual form works without any API key (LLM extraction deliberately not built — optional per handoff). Session form on the Simulation tab (history lines + effect rows with faction/type/was/now/reason). Engine tests: tests/test_effects.py (13 zero-dep tests incl. the full handoff acceptance scenario); studio tests: test_session_effects.py (4 tests incl. real views.py exposing the updated resource through a temp engine home). Fixture fix: testvale's rumors upgraded to the engine's full rumor shape (id/origin/heard_by/transmissions) — the abbreviated shape predated rumors.heard's contract and 502'd the views engine. All tiers green (simulator 102 tests, studio 250).
- [x] Phase 4: approved facts with faction-specific visibility.
  Phase note: new studio/knowledge.py + routers/knowledge.py — fact records in simulation/knowledge.yaml (stable ids, approved text, source file + approved revision, subjects, explicit visibility dm_only|public|[faction ids], status current|superseded with superseded_by provenance). Visibility must be explicit — unclassified lore is never mass-published; unknown faction ids in a visibility list are refused (422) so a typo cannot hide or leak a fact. Compile: current+non-superseded facts are written into state.yaml's knowledge section via publication.publish (transactional, revision-checked) under the campaign lock; engine views.py facts_for() exposes only public + addressed-to-you facts as facts_you_know in the faction view (and the prompt embeds the view — llm.render_prompt), filtered again by status so a hand-edited state cannot resurrect a superseded fact. Staleness: GET marks facts whose source bytes changed; the compiled state keeps the APPROVED text (never silent substitution); reconcile re-approves after the DM reads the change. Engine test (views facts visibility incl. the no-knowledge campaign), 6 studio tests (public reaches views; faction A's secret absent from B's serialized view; dm_only/superseded never leak; staleness + reconcile; visibility validation). Known gap: classification is API-only for now — no dedicated UI panel yet (the knowledge file is plain YAML and the API is complete); player-knowledge separation is enforced here (public ≠ party-known; disclosure comes in Phase 6). All tiers green.
- [x] Phase 5: target validation, independent difficulty, inactive defense, replay.
  Phase note: resolve.py is now RULE_VERSION 2 with the four changes as separate small units. (1) Target identity: llm._validate types targets (faction|district|world|self) and REFUSES unknown names with a correction message — propose() retries once with it, twice means the faction HOLDS; resolve_tick re-verifies every proposal against the passed state and raises on an unknown target, so a typo can never get an uncontested roll (districts explicitly supported). (2) Difficulty: DEFAULT_DC 12 (module constant, documented as a conservative default to tune through trusted rules after playtesting; resolve_action takes a rules={"default_dc": n} override owned by the DM/rules layer) — risk_acceptance is descriptive only and never moves the DC. (3) Inactive defense: a targeted faction that did not act still contests — d20 + CURRENT muscle bonus + 2 (home ground; distinct from offensive committed-resource bonuses), deterministic from the week seed, no LLM call; the defensive outcome is marked defensive and tick.apply_outcomes applies nothing for it (no invented action, no reward); defenses do not overextend the defender; multiple attackers each fight their own contest. (4) Replay: tick saves world/replay/week-N.yaml (schema 1: rule_version, seed, base_state_sha256, normalized proposals, recorded outcomes) and gains --replay which re-resolves offline (refuses a rule_version mismatch); resolve.set_rng + same proposals reproduce outcomes identically (test); the studio week proposal captures the replay trail as an artifact and records rule_version in run_record. Tests: resolver suite expanded (17 tests incl. risk-independent DCs, typo refusal, inactive defense + determinism, defense-is-not-offense via apply_outcomes, two-attackers-one-held-defender, district targets, seeded replay identity); tick suite gains the save_replay/replay_verify round trip; llm target-kind test. All tiers green (simulator 114, studio 250).
- [x] Phase 6: DM briefing, explicit player disclosure, bounded review workload.
  Phase note: new studio/briefing.py + routes (in routers/knowledge.py). DM briefing: deterministic build from state events + rumors in the requested since_week range (focus filter by faction/place name), refs to the entity page / week chronicle / matching map slug, unresolved decisions included but ALWAYS labeled (pending proposal -> "PROPOSED, NOT CANON"; stale facts + pending review counts -> "needs attention"), capped at 10 items with an explicit omitted count — no silent truncation; JSON + durable Markdown via GET /briefing/markdown (one shared renderer so view and file cannot diverge). Player disclosure: simulation/party-knowledge.yaml; reveal moves ONE current public fact's approved text into the record (dm_only/restricted/superseded facts 422 — no accidental path to the party; idempotent; revocable); the handout contains ONLY revealed text + known-since week — an empty disclosure exports an empty handout WITH an explanation, never a fallback to the DM briefing; no mechanics, no pending proposals, no secret titles or file paths. Review workload: POST /lore/pages/missing gains {"limit": n} (bounding the run before it spends; target selection already existed); deferral is explicit — regeneration writes the next free pending file and never overwrites one awaiting a decision; pending counts grouped per file were already in review GET. Budget: explicit per-run call ceiling in BOTH llm clients (LLM_MAX_CALLS env, default 24; begin_run() per job; BudgetExceeded stops between calls with a clear incomplete status) and an approximate token tally explicitly labeled "estimates, not the provider's bill" in logs and docs. Tests: test_briefing.py (4) covering range/cap, pending-proposal labeling, disclosure round trip + empty handout + secret absence incl. ids/metadata, superseded-fact reveal refusal. Human five-minute-prep playtest NOT performed (automated tests cannot prove it — reported honestly). All tiers green.
- [x] Phase 7: export and tested restoration.
  Phase note: new studio/export_campaign.py (stdlib CLI, importable functions). Export: `python studio/export_campaign.py <campaign> [--out PATH]` — one campaign + manifest.json (schema 1: per-file sha256, created, includes_git_history: false, note, excluded patterns); excludes .studio-transactions recovery payloads, __pycache__/*.pyc/*.tmp/*.bak/*.log/.git*/llm.env* (a dropped credential never rides along); never packages other campaigns. The output and the manifest state plainly that git history (campaigns/.git) is NOT included and that a same-disk archive is an export, not a backup (copy it yourself; nothing uploads). Restore: `--restore <zip> --into DIR` — refuses an existing target (never overwrites by default; no force flag), validates every member path (traversal/absolute/control chars refused), hash-verifies every file IN MEMORY before writing, refuses members absent from the manifest, re-verifies on-disk bytes post-write, then opens the restored campaign with the app's own loaders (require_campaign, campaign.yaml, state, entity pages, chronicles) — a restore that cannot load fails loudly. Tests: test_export.py (5) — manifest/exclusions/hash agreement, restore-into-fresh + loaders, existing-target refusal, tampered-archive refusal with nothing written, traversal-member refusal. All tiers green.
- [ ] Integrated completion scenario passes.

## Required final report from the implementing model

State which phases are complete, what behavior changed, which tests actually ran, what was skipped and why, and any remaining limitations. Include file links. Explicitly distinguish automated tests from human playtesting. Do not say the entire project is finished if any required phase or integrated acceptance criterion remains open.
