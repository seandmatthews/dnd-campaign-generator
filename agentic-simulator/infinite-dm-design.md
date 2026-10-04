# Infinite DM
## A Multi-Agent World Simulation Engine for Tabletop RPG Campaigns

**Status:** Design concept / MVP specification  
**Primary use case:** Persistent tabletop RPG worlds that continue evolving between sessions  
**Initial target:** D&D campaigns, especially faction-heavy sandbox campaigns such as *Tomb of Annihilation*  
**Model strategy:** Use inexpensive, high-volume LLM inference (for example GLM-5.3-Flash) for many small agent decisions, while keeping world truth and conflict resolution deterministic and auditable.

---

## 0. Relationship to the Existing Prototype

This document generalizes a working prototype that already lives in this
directory (`agentic-simulator/`). Reconciliation notes, so the spec and
the code do not drift apart:

- **Phase 1 is largely complete.** Five factions, a weekly tick, LLM-driven
  proposals (GLM-5.3-Flash via `src/llm.py`), a dice resolver
  (`src/resolve.py`), fog-of-war views (`src/views.py`), chronicle review
  (`chronicle/`). Week 3 of the city of Ruravaqan ran end-to-end on real
  LLM agents (chronicle `0003-a-funeral-priced-in-arrears.md`) — that run
  satisfies the §55 test spec almost exactly.
- **Rules the prototype has already tested that §9 should adopt:**
  *contested actions* (targeting an actor that acted the same week → opposed
  rolls, ties to the defender), *overextension* (−2 per contest beyond the
  first, applied to all of that actor's contests — "besieged becomes
  beleaguered"), and *commit clamping* (an agent cannot commit resources it
  does not have; the validator grades down rather than rejecting).
- **Deliberate deviations from this spec, held until the spec earns them:**
  YAML state files instead of SQLite (§26) — reviewable diffs beat schema
  migrations at current scale; weekly ticks instead of 6-hour ticks (§19) —
  faction-scale decisions do not decompose into hours; categorical
  information fidelity (eyewitness / reliable rumor / hearsay) instead of
  float confidence (§6) — numeric confidence is false precision for
  LLM-derived beliefs.
- **Known failure modes the prototype has observed that §31 should target:**
  proposals hallucinating facts about past events (needs a
  state-consistency check *before* resolution, not only post-hoc audits),
  and proactivity bias (agents propose action every tick; the scheduler
  needs an explicit "do nothing" bias).


---

## 1. Core Idea

Infinite DM is not an AI Dungeon Master that invents the game on behalf of the human DM.

It is a **world-state simulation engine**.

The human DM remains in control of canon, tone, pacing, encounter design, and what appears at the table. The system's job is to answer a narrower question:

> Given what every faction and NPC currently knows, wants, believes, and can actually do, what happens in the world while the player characters are busy elsewhere?

Each important NPC or faction is represented by an agent with:

- goals,
- beliefs,
- knowledge,
- relationships,
- resources,
- plans,
- memories,
- personality constraints,
- geographic location,
- and limited ability to act.

Agents never directly mutate the campaign world.

They propose **intentions**.

A separate world-resolution layer determines what actually happens.

This separation is the central architectural rule:

> **Agents choose actions. The world engine decides outcomes.**

---

## 2. Design Goals

Infinite DM should make campaign worlds feel alive without making them incoherent or uncontrollable.

The system should:

1. Advance factions and NPC plans between sessions.
2. Preserve imperfect information and misunderstandings.
3. Allow NPCs to make mistakes.
4. Prevent agents from becoming omniscient.
5. Keep generated consequences causally traceable.
6. Separate private world truth from what players can discover.
7. Allow the DM to approve, reject, edit, or rewind generated events.
8. Prefer structured world changes over generated prose.
9. Use large quantities of inexpensive inference where redundancy improves quality.
10. Keep the human DM as the ultimate authority over canon.

---

## 3. Non-Goals

The first version should **not** attempt to:

- replace the DM,
- generate complete sessions automatically,
- improvise every NPC conversation,
- simulate every civilian in the world,
- run combat through freeform LLM narration,
- let agents directly edit the database,
- maintain one giant unstructured campaign prompt,
- or require a complicated vector database before the basic simulation works.

The system should be useful even if it consists only of:

- a CLI,
- a relational database,
- YAML/JSON agent definitions,
- structured LLM outputs,
- and a Markdown briefing generator.

---

# 4. High-Level Architecture

```text
                         CURRENT WORLD STATE
                                 |
                   +-------------+-------------+
                   |                           |
             Global Events               Agent Scheduler
                                               |
            +----------------+-----------------+----------------+
            |                |                 |                |
            v                v                 v                v
       NPC Agents       Faction Agents    Villain Agents    Divine Agents
            |                |                 |                |
            +----------------+-----------------+----------------+
                                               |
                                               v
                                      PROPOSED INTENTIONS
                                               |
                                               v
                                       WORLD RESOLUTION
                                rules / conflicts / travel /
                                 resources / probability
                                               |
                                               v
                                        CANONICAL EVENTS
                                               |
                                  +------------+------------+
                                  |                         |
                                  v                         v
                           Updated World State        Knowledge Propagation
                                  |                         |
                                  +------------+------------+
                                               |
                                               v
                                        DM SESSION BRIEF
```

---

# 5. Core Principle: Epistemology

The simulation becomes interesting when characters **do not know the truth**.

Every agent should operate on its own subjective view of the world.

For example, Ras Nsi might know:

```yaml
agent: ras_nsi

goals:
  - preserve control over the yuan-ti
  - understand the Soulmonger
  - eliminate threats to Omu

knows:
  - adventurers have been asking about Omu
  - undead patrol 17 disappeared
  - Acererak is involved in something beneath the city

believes:
  - the adventurers may work for the Red Wizards
  - Fenthaza is becoming disloyal

does_not_know:
  - the party's exact location
  - Withers is Jergal
  - what happened in private player conversations

resources:
  yuan_ti_scouts: 14
  undead_patrols: 6
  political_influence: high
```

This agent should never receive the canonical `world_state`.

It should receive only the subset of information it has legitimately learned.

That means agents can:

- misunderstand events,
- act on false rumors,
- underestimate enemies,
- overestimate threats,
- deceive one another,
- discover contradictions,
- and revise beliefs over time.

Those errors create useful emergent play.

---

# 6. Canonical World State vs. Agent Beliefs

The system should distinguish at least three layers of truth.

## 6.1 Canonical Truth

What actually happened.

Example:

```yaml
event:
  id: evt_1042
  type: patrol_destroyed
  location: eastern_omu
  occurred_at: 1492-07-18T14:30
  actors:
    - undead_patrol_17
    - player_party
  outcome:
    undead_patrol_17: destroyed
```

## 6.2 Observed Information

What a character directly witnessed or reliably learned.

```yaml
observation:
  agent: salida
  event: evt_1042
  confidence: 0.95
  source: eyewitness
```

## 6.3 Belief

What the agent currently thinks happened.

```yaml
belief:
  agent: ras_nsi
  claim: "Red Wizard mercenaries destroyed patrol 17."
  confidence: 0.64
  derived_from:
    - rumor_221
    - report_78
```

These layers should never be collapsed into a single field.

---

# 7. Agent Model

Each simulated actor should have a structured definition.

```yaml
id: ras_nsi
name: Ras Nsi
type: npc

location: fane_of_the_night_serpent

traits:
  calculating: 0.88
  paranoid: 0.81
  patient: 0.70
  cruel: 0.82
  impulsive: 0.23

goals:
  - id: remain_in_power
    importance: 95
  - id: understand_soulmonger
    importance: 90
  - id: neutralize_external_threats
    importance: 78

relationships:
  fenthaza:
    trust: -45
    fear: 18
    respect: 61
    suspicion: 89

resources:
  yuan_ti_scouts: 14
  undead_patrols: 6
  spies: 4

constraints:
  - cannot_leave_omu_without_major_reason
  - avoids_personal_risk
```

An agent should have enough structure to constrain the model without forcing every behavioral detail into code.

---

# 8. Intentions, Not Narrative

Agents should not write fiction describing successful outcomes.

They should emit structured action proposals.

Example:

```json
{
  "actor": "ras_nsi",
  "intent": "investigate",
  "target": "unknown_adventurers",
  "method": "send_scouts",
  "resources": {
    "yuan_ti_scouts": 3
  },
  "reason": "Possible Red Wizard incursion",
  "priority": 72,
  "visibility": "covert"
}
```

The agent may propose the action.

It may not declare:

```text
My scouts successfully capture the adventurers.
```

That distinction protects world consistency.

---

# 9. World Resolver

The resolver converts proposed intentions into canonical events.

It should evaluate:

- actor resources,
- geography,
- travel time,
- competing intentions,
- known environmental hazards,
- existing commitments,
- probability,
- faction conflicts,
- rules established by the DM,
- and optional game mechanics.

For example:

```text
Ras Nsi proposes:
    Send 3 scouts toward the reported adventurer location.

Resolver checks:
    Scouts available?       yes
    Information accurate?   partially
    Distance?               12 miles
    Jungle hazard roll?     one casualty
    Target still present?   no

Canonical result:
    Two scouts reach the area.
    They find evidence of the party's passage.
    They begin tracking southwest.
```

The agent proposed an investigation.

The resolver determined the actual consequences.

---

# 10. Resolution Philosophy

Whenever possible, objective systems should resolve outcomes.

Examples:

- travel time -> deterministic geography rules,
- resource consumption -> database arithmetic,
- combat abstraction -> game mechanics,
- random encounters -> seeded RNG,
- social influence -> rule-based modifiers plus optional LLM judgment,
- scheduling conflicts -> deterministic timeline,
- impossible actions -> rejected automatically.

LLMs should primarily answer questions such as:

> What would this character try to do?

rather than:

> What objectively happens?

---

# 11. Multi-Sample Decision Making

Cheap inference makes it possible to sample the same agent multiple times.

Instead of asking:

> What does Ras Nsi do?

once, run several independent samples:

```text
Ras Nsi #1 -> send scouts
Ras Nsi #2 -> interrogate prisoners
Ras Nsi #3 -> focus on Fenthaza
Ras Nsi #4 -> contact the Red Wizards
Ras Nsi #5 -> send scouts
```

A separate decision-selection pass receives the candidates and evaluates which is most consistent with:

- personality,
- goals,
- current beliefs,
- available resources,
- previous behavior,
- risk tolerance,
- and current pressure.

This is effectively cheap Monte Carlo character reasoning.

---

# 12. Agent Sampling Profiles

Sampling can itself encode personality.

A highly predictable bureaucrat might use:

```yaml
decision_profile:
  samples: 3
  temperature: 0.3
```

An unstable goblin warlord might use:

```yaml
decision_profile:
  samples: 12
  temperature: 1.4
```

An exceptionally important mastermind might use many samples plus a stronger selection pass.

```yaml
decision_profile:
  samples: 30
  temperature: 0.8
  selector: high_reasoning
```

This is an intentionally token-hungry system.

---

# 13. Rumors as First-Class Objects

Information propagation should be simulated explicitly.

Example:

```yaml
id: rumor_221

claim:
  text: "The heroes possess a Netherese artifact."
  canonical_truth: false

origin:
  agent: xandala
  timestamp: 1492-07-17

spread:
  - kir_sabal
  - port_nyanzaru

heard_by:
  - wakanga_otamu
  - red_wizard_agent_3

distortion:
  current_version: "The adventurers stole a Netherese relic."
```

Rumors can mutate as they spread.

A rumor should track:

- origin,
- message,
- truth status,
- source reliability,
- transmission chain,
- distortion,
- recipients,
- timestamps,
- confidence.

---

# 14. Information Travel

News should take time to move.

Example campaign rules:

```text
Omu -> Kir Sabal                  2-5 days
Omu -> Port Nyanzaru            10-20 days
Port Nyanzaru -> Baldur's Gate   several weeks
Sending spell                    minutes
Divination magic                 special resolution
```

This produces strategically meaningful communication.

Sending a messenger becomes an action.

Intercepting a messenger matters.

Teleportation and divination become genuinely powerful.

---

# 15. Knowledge Propagation Pipeline

After canonical events are resolved:

```text
Canonical Event
      |
      v
Who directly observed it?
      |
      v
Create observations
      |
      v
Who reports it?
      |
      v
Create messages / rumors
      |
      v
Travel / delay / interception
      |
      v
Recipients update beliefs
```

Belief updating may be deterministic in simple cases and LLM-assisted for ambiguous ones.

---

# 16. Relationships

Relationships should be directional.

Ras Nsi's opinion of Fenthaza is not the same thing as Fenthaza's opinion of Ras Nsi.

Example:

```yaml
ras_nsi_to_fenthaza:
  trust: -45
  fear: 18
  respect: 61
  suspicion: 89

fenthaza_to_ras_nsi:
  trust: -80
  fear: 72
  respect: 20
  ambition: 94
```

Relationship dimensions should be campaign-configurable.

Possible dimensions include:

- trust,
- fear,
- affection,
- loyalty,
- respect,
- resentment,
- suspicion,
- debt,
- obligation,
- attraction,
- ideological alignment.

---

# 17. Faction Agents

Not every actor needs to be an individual person.

A faction agent might represent:

- Flaming Fist,
- Red Wizards,
- Harpers,
- yuan-ti leadership,
- merchant princes,
- pirate crews,
- goblin tribes,
- religious orders,
- criminal organizations.

Faction agents should operate at a coarser granularity than NPC agents.

Example intention:

```json
{
  "actor": "red_wizards_chult",
  "intent": "explore",
  "target": "secondary_omu_entrance",
  "resources": {
    "mages": 2,
    "guards": 5
  },
  "priority": 66
}
```

Important faction leaders can still exist as individual agents whose goals conflict with the faction aggregate.

---

# 18. Gods, Villains, and High-Level Actors

Extremely powerful actors should not automatically act every simulation tick.

Their action frequency should depend on:

- attention,
- motivation,
- domain,
- constraints,
- cosmic consequences,
- and narrative importance.

For example, Withers/Jergal might mostly observe.

```yaml
activation:
  normal_frequency: very_low
  triggers:
    - temporal_anomaly
    - soul_magic_disruption
    - divine_interference
```

This makes intervention meaningful.

---

# 19. Simulation Clock

The simulation operates on campaign time.

Example:

```bash
dm simulate --hours 8
dm simulate --days 4
dm simulate --until "1492-07-22 08:00"
```

The engine advances through discrete ticks.

A simple MVP might use:

```text
1 tick = 6 campaign hours
```

Later versions can use variable-resolution simulation:

```text
active region:       1 hour
nearby factions:     6 hours
distant factions:   24 hours
inactive world:      several days
```

This avoids wasting inference on irrelevant actors.

---

# 20. Agent Scheduler

Not every NPC needs to think every tick.

The scheduler determines which agents are activated.

Possible activation score:

```text
activation =
    importance
  + proximity_to_players
  + unresolved_goal_pressure
  + recent_information
  + active_conflict
  + DM_priority
  - dormancy
```

Only agents above a threshold receive an LLM call.

This keeps the simulation scalable.

---

# 21. Event System

Every meaningful world change should become an immutable event.

Example:

```yaml
id: evt_1048
timestamp: 1492-07-19T02:00
type: scout_team_departed

actors:
  - ras_nsi
  - yuan_ti_scout_team_3

location:
  from: fane_of_the_night_serpent
  toward: eastern_omu

causes:
  - evt_1042
  - belief_803

effects:
  yuan_ti_scouts_available: -3
```

State can then be derived from event history.

This enables:

- auditability,
- replay,
- branching timelines,
- rollback,
- debugging,
- causal explanation.

---

# 22. Causality Inspector

A crucial DM feature is the ability to ask:

> Why are yuan-ti scouts here?

The system should produce:

```text
Session 8
Players asked Salida about Omu
        |
        v
Salida secretly reported this
        |
        v
Ras Nsi learned strangers were approaching
        |
        v
Ras Nsi believed they might be Red Wizard agents
        |
        v
He dispatched three scouts
        |
        v
One scout died during travel
        |
        v
Two scouts reached the area
        |
        v
They are now tracking the party
```

Every generated consequence should be traceable to prior facts, beliefs, decisions, and resolver outcomes.

If it cannot explain why something happened, the system should treat that as a warning sign.

---

# 23. Rewind and Retcon

The event model allows a DM to change canon.

Example:

> Actually, Salida never heard that conversation.

The system can:

1. invalidate the observation,
2. identify downstream dependent events,
3. rewind to that point,
4. re-run affected simulations,
5. show the changed timeline.

This should eventually support branching worlds:

```text
main
 |
 +-- timeline/a
 |
 +-- timeline/b
```

Essentially: Git for campaign history.

---

# 24. Player Knowledge

The system should track what the player characters know separately from world truth.

For each generated event, the DM brief can classify information as:

```text
Known to players
Discoverable now
Discoverable with investigation
Rumor only
Secret
Impossible for players to know yet
```

This prevents the briefing system from accidentally turning DM knowledge into player-facing exposition.

---

# 25. Session Brief Generator

The primary output of the simulation should be concise.

Example:

```markdown
# World Update: Four Days Passed

## Major Changes

- Ras Nsi learned that adventurers matching the party's description
  entered the valley.
- Three yuan-ti scouts were dispatched toward Omu.
- One scout died in the jungle.
- The remaining two are now tracking the party.
- A Flaming Fist expedition lost three soldiers crossing the Aldani Basin.
- Xandala reached Kir Sabal and began spreading a false story about the party.
- A Red Wizard expedition discovered a secondary entrance into Omu.

## Player-Discoverable Developments

- Signs of yuan-ti scouts.
- Abandoned Flaming Fist supplies.
- Rumors about Xandala's accusation.

## Hidden Developments

- The Red Wizards' secondary entrance.
- Ras Nsi suspects the party works for the Red Wizards.
- Withers has noticed the party's temporal disturbances.

## Potential Session Hooks

- The scouts may observe the party before engaging.
- A survivor from the Flaming Fist expedition could appear nearby.
- A messenger carrying Xandala's rumor could cross the party's route.
```

The system should generate **prep material**, not a novel.

---

# 26. Proposed Data Model

A relational database such as SQLite or PostgreSQL is sufficient.

Core tables:

```text
agents
agent_traits
agent_goals
agent_resources

locations
location_connections

relationships

events
event_causes
event_effects

observations
beliefs

rumors
rumor_transmissions

intentions

simulation_ticks

messages

player_knowledge
```

---

# 27. Suggested Database Sketch

## agents

```text
id
name
type
location_id
importance
status
created_at
```

## goals

```text
id
agent_id
description
importance
status
created_at
```

## beliefs

```text
id
agent_id
claim
confidence
source_type
source_id
created_at
last_updated_at
```

## relationships

```text
source_agent_id
target_agent_id
dimension
value
updated_at
```

## events

```text
id
event_type
timestamp
location_id
payload_json
created_by
canonical
```

## intentions

```text
id
agent_id
tick_id
intent_type
target_id
payload_json
priority
selected
resolution_event_id
```

---

# 28. Prompt Construction

Each agent prompt should be generated from the database.

It should contain only relevant context.

Example structure:

```text
SYSTEM:
You are deciding what Ras Nsi attempts during the next six hours.

You do not control outcomes.
You may only propose actions.
You know only the information listed below.

IDENTITY:
...

PERSONALITY:
...

GOALS:
...

CURRENT BELIEFS:
...

KNOWN EVENTS:
...

RELATIONSHIPS:
...

AVAILABLE RESOURCES:
...

CURRENT LOCATION:
...

RECENT ACTIONS:
...

OUTPUT:
Return exactly one structured intention.
```

Do not give every agent the entire campaign history.

---

# 29. Memory Strategy

Long-term memory should mostly live in structured storage, not model context.

The model receives:

- current goals,
- important relationships,
- recent events,
- selected long-term memories,
- unresolved plans,
- relevant beliefs.

Long transcripts should be summarized into persistent memories.

Example:

```yaml
memory:
  agent: salida
  importance: 82
  summary: >
    The party spared Salida after discovering her deception.
    She believes they may still be useful but fears they no longer trust her.
```

---

# 30. Model Roles

Different model calls can perform different jobs.

## Worker Model

Cheap, fast model.

Used for:

- NPC decision samples,
- faction decisions,
- rumor distortion,
- belief interpretation,
- minor planning.

## Selector Model

Chooses among sampled intentions.

Can use the same cheap model with a more constrained prompt.

## Resolver Assistant

Used only where deterministic resolution is insufficient.

It suggests interpretations but should not directly commit state.

## Briefing Model

Turns canonical event data into readable DM prep.

## Continuity Auditor

Looks for contradictions such as:

- dead NPC taking actions,
- impossible travel,
- resource duplication,
- knowledge appearing without a source,
- inconsistent timelines,
- forgotten major goals.

---

# 31. Continuity Agent

After each simulation batch, run an audit.

Example checks:

```text
- Did any actor use knowledge they never received?
- Did anyone move faster than allowed?
- Did a dead or imprisoned actor act?
- Were resources spent twice?
- Did mutually exclusive events both become canonical?
- Did a faction ignore a critical goal without explanation?
- Did a secret become globally known without a transmission path?
```

This is one of the best places to spend excess tokens.

Run multiple auditors independently.

---

# 32. Deterministic Validation

LLM outputs should pass validation before resolution.

Example:

```python
assert actor.exists
assert actor.is_active
assert resources_requested <= resources_available
assert target.exists
assert intent_type in allowed_intents
assert output_matches_schema
```

Invalid intentions are rejected or repaired.

The database remains authoritative.

---

# 33. Structured Output Schema

Possible intention schema:

```json
{
  "actor_id": "string",
  "intent_type": "string",
  "target_id": "string | null",
  "location_id": "string | null",
  "priority": 0,
  "resources": {},
  "reason": "string",
  "visibility": "public | private | covert",
  "expected_duration_hours": 0,
  "preconditions": [],
  "fallback": "string | null"
}
```

The agent should not be allowed to invent arbitrary world-state fields.

---

# 34. Campaign Rules Engine

The DM should be able to define world rules.

Example:

```yaml
rules:
  travel:
    jungle_miles_per_day: 10
    river_miles_per_day: 20

  communication:
    mundane_message_speed_miles_per_day: 20

  resurrection:
    enabled: false

  divination:
    detect_temporal_anomaly:
      difficulty: high
```

These rules constrain the resolver.

---

# 35. Geography

Locations should form a graph.

Example:

```text
Port Nyanzaru
    |
    +-- River Soshenstar
    |
    +-- Fort Beluarian
    |
    +-- Jungle Interior
            |
            +-- Kir Sabal
            |
            +-- Omu
                    |
                    +-- Fane of the Night Serpent
```

Edges can contain:

```yaml
distance_miles: 34
terrain: jungle
hazard: high
visibility: low
```

Agents move across the graph rather than teleporting via prose.

---

# 36. Resource Simulation

Resources give faction decisions consequences.

Examples:

```text
soldiers
gold
food
spell slots
ships
scouts
political favors
informants
prisoners
artifacts
ritual materials
```

A faction that repeatedly sends patrols should eventually run low on available patrols.

This creates actual strategy.

---

# 37. Plans

Agents may maintain multi-step plans.

Example:

```yaml
plan:
  id: overthrow_ras_nsi

  owner: fenthaza

  objective:
    become_leader_of_fane

  steps:
    - recruit_dissatisfied_priests
    - weaken_ras_nsi_authority
    - obtain_external_support
    - trigger_public_crisis
    - seize_control

  current_step:
    recruit_dissatisfied_priests
```

The model decides whether to advance, revise, postpone, or abandon plans.

---

# 38. Secrets

Secrets should explicitly record who knows them.

Example:

```yaml
secret:
  id: withers_is_jergal

  truth:
    "Withers is Jergal."

  known_by:
    - withers
    - acererek

  suspected_by:
    - mystic_npc_1
```

Agents should only receive secrets they know or suspect.

---

# 39. DM Overrides

Every generated event should support:

```text
approve
reject
edit
lock
retcon
rerun
```

A locked event becomes immutable canon unless the DM explicitly unlocks it.

---

# 40. Human Control Modes

Possible simulation modes:

## Conservative

Only highly likely actions become canon.

Good for serious campaigns.

## Generative

More unexpected developments are allowed.

Good for sandbox play.

## Chaos

Higher sampling temperatures and more aggressive action selection.

Good for intentionally unpredictable worlds.

## Manual Approval

Nothing becomes canonical until the DM approves it.

Ideal for early versions.

---

# 41. CLI MVP

The first interface can be extremely simple.

```bash
dm init campaign.yaml

dm agent add ras_nsi.yaml
dm agent list

dm world status

dm simulate --days 4

dm brief

dm events list

dm why evt_1048

dm approve evt_1048
dm reject evt_1048

dm rewind evt_1042

dm agent inspect ras_nsi
```

No web UI is required initially.

---

# 42. Example Simulation

Starting state:

```text
Day 1

Party:
    traveling toward Omu

Salida:
    knows party destination

Ras Nsi:
    does not know party location

Xandala:
    traveling toward Kir Sabal
```

### Tick 1

Salida proposes:

```text
Secretly send information about the party toward Ras Nsi.
```

Resolver:

```text
Message successfully dispatched.
Travel time: 2 days.
```

### Tick 2

Xandala proposes:

```text
Tell Kir Sabal leaders that the party stole an artifact.
```

Resolver:

```text
Rumor begins spreading locally.
```

### Tick 8

Salida's message reaches a yuan-ti intermediary.

### Tick 9

Ras Nsi receives distorted information:

```text
Armed outsiders seeking Omu may be connected to Red Wizards.
```

His belief state changes.

### Tick 10

Five Ras Nsi decision samples are generated.

Two recommend scouts.

One recommends capturing a local guide.

One recommends contacting Red Wizards.

One recommends doing nothing.

Selector chooses:

```text
Send three scouts.
```

### Resolver

One scout dies during jungle travel.

Two continue.

### Briefing

The DM receives:

```text
- Xandala has damaged the party's reputation at Kir Sabal.
- Ras Nsi now knows outsiders are approaching.
- Two surviving yuan-ti scouts are moving toward the party's route.
```

The world changed without requiring scripted events from the DM.

---

# 43. Token-Spending Strategy

The system should deliberately spend cheap inference on redundancy.

Good places to spend tokens:

- multiple independent NPC decisions,
- multiple continuity audits,
- alternative interpretations of ambiguous events,
- rumor transformations,
- adversarial review of planned actions,
- faction-planning trees,
- session briefing summaries,
- contradiction detection.

Bad places to spend tokens:

- endless NPC-to-NPC freeform conversations,
- prose describing routine actions,
- unconstrained brainstorming,
- repeatedly re-summarizing the entire campaign.

The useful metric is not:

> How many tokens did we save?

It is:

> How much credible world behavior did we obtain per dollar?

---

# 44. Suggested Repository Structure

```text
infinite-dm/
|
+-- campaigns/
|   +-- tomb-of-annihilation/
|       +-- campaign.yaml
|       +-- rules.yaml
|       +-- agents/
|       +-- locations/
|       +-- secrets/
|
+-- src/
|   +-- agents/
|   +-- scheduler/
|   +-- resolver/
|   +-- knowledge/
|   +-- simulation/
|   +-- briefing/
|   +-- storage/
|
+-- schemas/
|   +-- intention.json
|   +-- event.json
|   +-- agent.json
|
+-- prompts/
|   +-- npc_decision.md
|   +-- faction_decision.md
|   +-- intention_selector.md
|   +-- continuity_audit.md
|   +-- briefing.md
|
+-- tests/
|
+-- cli/
|
+-- data/
    +-- campaign.sqlite
```

---

# 45. Recommended MVP Stack

A straightforward implementation could use:

```text
Language:      Python
Database:      SQLite initially
Validation:    Pydantic
CLI:           Typer
LLM calls:     provider abstraction
Config:        YAML
Migrations:    Alembic if/when PostgreSQL is introduced
Testing:       pytest
```

A later web interface could use any preferred frontend stack.

The important part is keeping the simulation engine independent of the UI.

---

# 46. Model Abstraction

Do not couple the engine directly to one model provider.

Use something like:

```python
class ModelProvider:
    def generate_structured(
        self,
        messages,
        schema,
        temperature,
    ):
        ...
```

Then support:

```text
GLM
OpenAI
Anthropic
local models
other OpenAI-compatible APIs
```

This makes model benchmarking easy.

---

# 47. Simulation Pseudocode

```python
def simulate_tick(world, tick):
    active_agents = scheduler.select_agents(world, tick)

    intentions = []

    for agent in active_agents:
        context = knowledge.build_agent_context(agent, world)

        candidates = [
            model.propose_intention(context)
            for _ in range(agent.sample_count)
        ]

        selected = selector.choose(agent, candidates, context)

        intentions.append(selected)

    validated = validator.validate(intentions, world)

    results = resolver.resolve(validated, world)

    events = event_store.commit(results)

    knowledge.propagate(events, world)

    continuity.audit(world, events)

    return events
```

---

# 48. Simulation Safety Invariants

The engine should enforce invariants such as:

```text
Agents cannot know undiscovered information.
Agents cannot spend unavailable resources.
Agents cannot occupy two locations simultaneously.
Dead actors cannot take ordinary actions.
Travel requires time.
Messages require transmission.
Events must have causes.
State changes require events.
Only the resolver commits canonical changes.
DM-locked canon cannot be overwritten.
```

These rules matter more than clever prompting.

---

# 49. Campaign Import

A future importer could accept:

```text
session notes
NPC notes
faction notes
locations
calendar
player decisions
published adventure material
homebrew lore
```

An extraction pipeline could propose:

```text
agents
goals
relationships
known facts
secrets
unresolved plots
locations
resources
```

The DM then approves the extracted structure.

---

# 50. Session Note Ingestion

After each session:

```bash
dm ingest session-08.md
```

The system extracts proposed canonical events:

```text
Party reached Omu.
Party killed undead patrol 17.
Salida overheard discussion of the Soulmonger.
Party found a Netherese device.
```

The DM approves or edits them.

Once approved, they become canonical input for future simulations.

---

# 51. Player-Facing Possibilities

The world engine can eventually power player-visible systems without exposing secrets.

Examples:

- newspapers,
- tavern rumors,
- letters,
- faction messages,
- bounty boards,
- intercepted correspondence,
- political maps,
- changing settlement descriptions.

These outputs should be generated strictly from information the relevant fictional source could know.

---

# 52. Long-Term Emergent Behavior

With enough campaign time, the system could generate:

- faction alliances,
- betrayals,
- shortages,
- wars,
- investigations,
- territorial expansion,
- propaganda,
- assassinations,
- religious schisms,
- trade disruption,
- magical research,
- criminal enterprises,
- rival adventuring parties.

The DM does not need to pre-script these outcomes.

They emerge from goals plus constraints.

---

# 53. Why This Works Well With Cheap Models

Most world-simulation decisions do not require the most capable model available.

The question is usually:

> Given these ten facts, three goals, five relationships, and two recent events, what would this actor plausibly attempt next?

That makes the workload ideal for:

- inexpensive inference,
- parallel calls,
- sampling,
- voting,
- critique,
- repeated auditing.

Expensive reasoning can be reserved for rare conflicts or major campaign moments.

---

# 54. Development Roadmap

## Phase 1 — Tiny Prototype

Support:

- SQLite world database,
- five agents,
- goals,
- beliefs,
- resources,
- structured intentions,
- manual resolver,
- Markdown briefing.

Test with one week of campaign time.

## Phase 2 — Automatic Resolution

Add:

- travel,
- resource accounting,
- message transmission,
- probabilistic outcomes,
- event causality.

## Phase 3 — Knowledge Simulation

Add:

- observations,
- beliefs,
- rumors,
- confidence,
- misinformation,
- information travel.

This is where the system becomes substantially more interesting.

## Phase 4 — Multi-Agent Sampling

Add:

- multiple decision samples,
- intention selector,
- agent-specific sampling profiles,
- adversarial reviewers.

## Phase 5 — Continuity System

Add:

- contradiction detection,
- causal inspector,
- retcons,
- rewind and replay.

## Phase 6 — DM UX

Add:

- web dashboard,
- timeline,
- faction overview,
- relationship graph,
- rumor map,
- approval queue,
- simulation controls.

---

# 55. First Real Campaign Test

A good first test would use approximately:

```text
5 important NPCs
3 factions
8-12 locations
20 known facts
5 secrets
10 relationships
1 week of campaign time
```

For example:

```text
Ras Nsi
Fenthaza
Salida
Xandala
Withers

Yuan-ti
Red Wizards
Flaming Fist
```

Run the world for seven days.

Then examine:

1. Did anything interesting happen?
2. Did anything impossible happen?
3. Did anyone gain knowledge magically?
4. Can every event explain its cause?
5. Did factions consume resources sensibly?
6. Did the resulting hooks feel usable at the table?
7. Did the DM brief remain concise?

That experiment would tell us more than building a sophisticated UI first.

---

# 56. The Product Vision

The eventual experience should feel like this:

```bash
$ dm simulate --days 4

Simulating 4 campaign days...

Agents activated:       18
Intentions sampled:    124
Canonical events:       31
Rumors transmitted:     14
Beliefs changed:         27
Continuity warnings:      1

World state advanced to 1492-07-22 08:00.

$ dm brief
```

And the output is not hundreds of pages of generated fiction.

It is:

```text
Here are the seven things that changed.

Here are the four things the players can discover.

Here are the three secrets still moving behind the scenes.

Here are two situations likely to collide with the party next session.

And here is why every one of them happened.
```

That is the core promise of Infinite DM.

---

# 57. Guiding Principle

The strongest version of this system is not the one with the most autonomous agents.

It is the one where generated behavior remains:

- plausible,
- constrained,
- causally understandable,
- editable,
- and useful to the human DM.

The LLM supplies imagination.

The simulation engine supplies reality.

The DM decides canon.
