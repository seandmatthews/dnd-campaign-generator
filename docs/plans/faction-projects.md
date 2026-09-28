# Faction projects with persistent progress

Status: IMPLEMENTED 2026-09-28 (engine `src/projects.py` + week/scheduler/views/llm/effects wiring, replay schema 3, Studio `project_changes` proposals + Projects panel + session project effects; suites `agentic-simulator/tests/test_projects.py`, `studio/tests/test_projects.py`). Written 2026-09-27 against the then-current working tree. Preserve the existing multi-place work and re-check engine interfaces before implementation.

Related plans: [Session notes to changes](session-notes-to-changes.md), [Place-centered campaign view](place-centered-campaign-view.md).

## Outcome

Weekly faction activity should build toward concrete changes the DM can use: capturing a ferry contract, opening a smuggling route, or assembling a levy. The DM should be able to answer what a faction is trying to finish, how far it has progressed, what slowed it down, and what completion will actually change.

One-sentence rule: **a successful work action advances its project one step, a successful sabotage action removes one step, and reaching the target completes the project and applies its preapproved effects once.**

Start with this rule. Do not add progress rolls, variable-speed bonuses, spending currencies, travel simulation, or a dependency graph in version one.

## Existing foundation

- `agentic-simulator/src/resolve.py` resolves proposals, including multiple contests and inactive defense.
- `tick.py` consolidates an actor's outcomes and applies one mechanical consequence per proposal; replay now embeds resolver input in the current working tree.
- `llm.py` validates faction proposals; `views.py` supplies filtered inputs; `scheduler.py` chooses who acts.
- `effects.py` supports typed resource and faction-field changes, with stable application IDs.
- `goals.py` proposes goal text updates. A goal is an aspiration; a project is a concrete undertaking. Keep both concepts.
- `store.py` normalizes legacy city data and current `places`/`faction.place` identities.
- Studio already stages simulated weeks and forecasts without immediately committing them.

The engine owns project mechanics. Studio owns editing, review, and presentation. The mapping engine remains independent and never reads simulation state.

## First usable scenario

The Wardens start **Buy the ferry concession**, progress 0/4, located at the ferry. On successful work weeks the counter advances. A rival can attempt sabotage only if its view includes that project. At completion, the preapproved effect changes a supported faction field/resource and an event records the concession purchase. A session can also advance or halt the undertaking through a reviewed explicit effect.

Do not equate a completed counter with arbitrary world control. If the system has no territorial-ownership mechanic, “control of the ferry” is a historical/knowledge fact, not a new hidden resolver bonus. The UI distinguishes mechanical effects from descriptive completion text.

## Canonical schema

Store projects at top level in the engine's `state.yaml`. Missing `projects` means an empty list; old campaigns retain old behavior. Do not create a second writable project registry in Studio.

Illustrative version-one record:

```yaml
projects:
  - id: project-ferry-7b03
    schema_version: 1
    faction_id: wardens
    title: Buy the ferry concession
    objective: Secure the ferry concession before the spring auctions.
    place_ids: [ferry-crossing]
    status: active
    progress: 0
    target: 4
    visibility: restricted
    known_to: [wardens]
    requirements:
      coin: low
    completion:
      text: The Wardens acquired the ferry concession.
      effects:
        - type: set_resource
          faction_id: wardens
          resource: legitimacy
          expected_before: low
          after: medium
          reason: The ferry concession gives the Wardens recognized standing.
    created_week: 8
    completed_week: null
```

This is a proposed schema. IDs are generated once and never derived again from a renamed title. Validate unique project IDs, existing owner and place IDs, integer counters (exclude booleans), `0 <= progress <= target`, and target 2–8. Default target is 4. Limit each faction to one active project in version one; paused/completed/abandoned projects may remain in history.

Statuses: `active`, `paused`, `completed`, `abandoned`. Only active projects receive automatic work or sabotage progress. Completed and abandoned projects are terminal; corrections use an explicit reviewed operation, not reopening the same completion identity. Pausing preserves progress. Goals can change without implicitly deleting, retitling, or completing projects.

Requirements use existing resource grades as minimum eligibility, not a consumable balance. For example, `coin: low` means work cannot progress when the owner has `coin: none`. No resource is deducted by the requirement itself. Existing action commitments and resource consequences still apply.

Completion effects initially allow only `set_resource` and `set_field` for the owner. Reject recursive project effects, arbitrary paths, code, transfers from other factions, and automatic disclosure. Generate their stable effect IDs from the project ID plus stored effect slot ID when the project is created. Give each completion effect a stable slot ID in the persisted normalized record, even if the illustrative authoring input omits it.

## Actions and deterministic resolution

Extend normalized proposals with optional fields:

```yaml
intent: work_project        # ordinary | work_project | sabotage_project
project_id: project-ferry-7b03
target: self
action: Negotiate the remaining ferry shares.
resources_committed:
  coin: low
```

Legacy proposals without `intent` normalize to `ordinary`. Work targets the actor's own active project. Sabotage targets another faction's visible active project and normalizes resolver `target` to that project's owner. A model cannot mark a targeted rival project as `self` to obtain an uncontested roll. Both work and sabotage use the existing resolver; there is no second dice roll.

Validation occurs before resolution: project existence, status, ownership, visibility, and requirements are checked against the pre-week state. Validation findings can trigger the existing bounded proposal correction attempt. Do not silently transform an invalid project action into an ordinary successful one.

For actors in multiple contests, use exactly one consolidated outcome for project progress and ordinary resource consequences. Refactor a shared consolidation helper if necessary. Any loss defeats a success at equal worst margin, so a tied defensive win cannot hide a tied attacking loss. Preserve the full roll table for explanation.

Apply a week in this order:

1. Snapshot project state and validate actions/requirements against that snapshot.
2. Resolve all actions using the existing seeded RNG.
3. Consolidate each acting faction's outcome once. Inactive defensive outcomes do not perform project actions.
4. Apply ordinary resource consequences once per actor.
5. Calculate project deltas: successful work +1; successful sabotage -1; failure 0. Aggregate deltas by project, then clamp once to `[0, target]`.
6. Determine completion from final counters. Work and sabotage that cancel at the threshold do not complete a project. Proposal ordering must not change this result.
7. Validate all completion effects against the post-resource state and publish valid completions into the in-memory candidate. Record structured events and explanation metadata.

If completion preconditions no longer hold, keep progress at target and status active with a derived `completion_blocked` finding. Apply none of that project's completion effects. On later ticks recheck completion deterministically, without an LLM call or another work action; sabotage can reduce its counter meanwhile. An explicit DM edit can update completion terms through a reviewed proposal. Evaluate simultaneous completions against one shared post-resource snapshot: overlapping writes to the same faction field/resource block the conflicting completions rather than depending on iteration order.

Completed project effects execute exactly once, using existing applied-effect deduplication. Completing a project with no mechanical effects still emits exactly one completion event. Do not apply a second ordinary resource reward for project completion unless the preapproved effect explicitly provides it.

## Visibility and scheduling

Owners see full project detail. Other factions see only projects marked public or explicitly addressed to them through `known_to`; omit private completion effects and exact requirements from outsider views. For visible rival projects, expose ID, owner, title, place references, status, and a coarse progress label (`starting`, `underway`, `near completion`). Exact counters are DM/owner information in version one.

Do not put hidden project data into the shared prompt, narrator summary, ordinary rumors, or goal-review output supplied to faction agents. The DM's chronicle can include private mechanics, but rumor claims must use a separate public-safe description; secret work must not announce a hidden project merely because an event was created.

Scheduling rule: an eligible active project makes its owner eligible to act even if the ordinary quiet-week gate would hold it. Blocked requirements or a project awaiting completion correction do not create this activation override. Ordinary scheduler reasons still apply. Record “active project” in the activation explanation. Cap remains one proposed action per faction; projects do not introduce extra faction calls in a week.

The prompt recommends work on an active project but may propose an ordinary action instead. No model call is needed to define projects manually. Automatic project invention is deferred.

## DM workflow and Studio integration

Add a Projects section to Simulation, grouped by owner, with progress, place links, latest change, requirements, and completion terms. Click a project to see its history and the dice/action/source behind each movement.

Creating, editing, pausing, or abandoning a project prepares a `project_changes` proposal. Reuse the existing pending slot, accept/discard controls, revision checks, and publication receipts. Preparation shows an exact diff and involves no LLM spend. Editing mechanics after creation is an explicit reviewed operation; never change the rules of a pending simulated week without making that week stale.

Extend `proposals.diff_states` with `projects_added`, `projects_changed`, and completion-effect summaries. Show progress changes in weekly proposals and forecasts. Accepted forecasts must remain impossible; their project progress is hypothetical only.

Extend structured events with stable `event_id`, `project_id`, `place_ids`, progress before/after, source action ID, and completion effect IDs. Old events remain readable. The place view consumes these fields directly and does not infer a historical location from the faction's current home.

Session integration adds typed `set_project_progress` and `set_project_status` effects only after engine support exists. Require exact expected-before values, project ID, reason, and stable application ID. Reaching target invokes the same completion function used by ticks. Do not implement a second completion algorithm in Studio or let LLM text directly set `completed`.

## Engine and API contracts

New `agentic-simulator/src/projects.py` should provide pure functions for validation, visibility, eligibility, action validation, delta aggregation, and completion. It must not import Studio or mapping code.

Proposed standalone CLI:

```text
python src/projects.py --check changes.yaml --state world/state.yaml
python src/projects.py --preview changes.yaml --state world/state.yaml --out preview.yaml
python src/projects.py --apply changes.yaml --state world/state.yaml
```

`--apply` is explicit standalone canon mutation; Studio applies only to a captured temporary state and stages the output. Read-only preview returns normalized changes, findings, and a candidate state. The CLI uses the same validators as weekly execution and session effects.

Proposed Studio routes under `/api/campaign/{name}`:

| Route | Contract |
|---|---|
| `GET /simulation/projects` | DM list and computed requirement/completion findings |
| `GET /simulation/projects/{id}` | Detail and sourced event history |
| `POST /simulation/projects/prepare` | Typed operations plus expected state revision; staged proposal |

Use `create`, `edit`, `pause`, `resume`, and `abandon` operations with operation IDs. Unknown fields fail validation. Editing a completed project's descriptive note may be supported later; version one refuses terminal mutation. Use existing accept/discard routes for `project_changes`.

Replay artifacts must contain project input state, normalized intents, resulting project deltas, and the project-rule version. Extend verification to recompute project mechanics as well as dice outcomes. Bump the appropriate schema/rule version and retain support for reading old replay records; do not claim old records verify mechanics they never stored.

## Implementation phases

1. **Pure rules and schema:** implement validation, progress aggregation, completion, and adversarial fixtures. No model or UI required. Document the one-sentence mechanic and exact execution order.
2. **Standalone use:** implement CLI, state validation, event provenance, and manual operations. Verify legacy campaigns with no projects are byte/behavior compatible except documented serialization differences.
3. **Simulation integration:** update prompt normalization, views, scheduler, tick, replay, rumors, and causal explanations. Test successful work, failure, sabotage, simultaneous actions, and completion blocking.
4. **Studio review:** add project editor, staged changes, proposal diff, weekly/forecast presentation, and place links. Extend session consequences using the shared engine rules.

Likely changes span engine `projects.py`, `store.py`, `llm.py`, `views.py`, `scheduler.py`, `tick.py`, `effects.py`, `events.py`, `why.py`, `rumors.py`, the faction prompt, and Studio proposal/simulation/UI modules. Keep resolver changes limited to shared outcome consolidation and documented normalization needs.

## Acceptance tests and definition of done

- A 0/4 project advances exactly once from one successful action, including when the actor appears in multiple contests.
- Failed work leaves progress unchanged; existing resource loss still occurs when appropriate.
- Reordering simultaneous work and sabotage yields identical progress and completion results.
- An inactive defender winning a contest does not work on its project.
- Missing required coin prevents project eligibility; the prompt never invents a requirement payment.
- Hidden projects cannot be targeted by guessing an ID and do not leak through views or rumors.
- Completion effects fail together on a stale precondition, with a visible finding and no partial application.
- Completion, replay, retry, acceptance after restart, and repeated session effects never apply the same completion twice.
- A forecast can finish a project without changing live state or consuming the pending-week slot.
- Editing project terms after a weekly preview makes acceptance stale.
- A campaign with no projects preserves existing scheduler and resolver behavior.
- Export/restore retains projects, progress, explanations, and completion deduplication.

Run focused engine/project tests, Studio staging tests, then `python run-tests.py`. Use deterministic seeds and mocked model replies; no live spend. Done means the ferry example runs through at least four weeks, one sabotage attempt, one session adjustment, and exactly one reviewed completion, through both UI and CLI.
