# 03 — Premise-to-sandbox storyline generation

**Status:** completed — implemented 2026-10-02; code-verified and archived 2026-10-04.
**Priority:** P1
**Area:** new endpoint + Simulation tab create form; `studio/loregen.py`
pattern; `llm.env.example`
(Covers two pieces of the 2026-10-01 feedback: "the blank page is where the
app is weakest" and "premise-to-sandbox ideation" — same feature.)

## Problem

Everything good in the pilot campaign existed because the GM arrived with a
campaign in their head: the BBEG twist, the five factions, the asymmetry.
The app's only "tell me your premise and I'll help" moment is worldgen
options (`POST /world-options`). The Simulation tab hands a new GM an empty
grid — name, goal, coin/muscle/legitimacy for 2–5 factions — with no
ideation help at all. A GM without a concept gets maps and no muse.

The capability is already sitting in the building: the pilot's factions and
villain were authored by an LLM, and the LLM client is provider-agnostic
over `llm.env` (any OpenAI-compatible endpoint: `LLM_BASE_URL` /
`LLM_API_KEY` / `LLM_MODEL`; the built-in Z.ai coding-plan fallback works
today). A storyline generator is one bounded LLM pass plus validation.

## Proposal

### 1. The endpoint

`POST /api/campaign/{name}/simulation/design`

Body: `{"premise": "<one paragraph>", "places": [...]?}` (places optional —
if geography exists, its nations/biomes go in as grounding; fed-or-refused
applies to world grounding exactly as lore generation does: with no world
and no places, the endpoint refuses rather than inventing a vacuum world).

One bounded LLM pass (metered, same budget system as every other pass)
returns JSON validated against the **existing** schema validators:

- `premise` — one line, ≤ 600 chars, the tension (validated like the form's)
- `factions` — 2–5 entries, each `name`, `kind`, `goal` (one line),
  `resources` (coin/muscle/legitimacy levels), optional `place`; run through
  `spine.validate_faction_docs` so what reaches the form is exactly what
  the form accepts today
- `projects` — optional, one per faction: `title`, `objective`, `target`
  2–8, validated against the engine's project normalizer
- `secrets` — optional per faction: one-line hidden angle, **never** part of
  the state payload — offered separately as DM-only knowledge-fact drafts

Replies that fail validation are re-asked once (bounded), then surfaced as
a 502 with the validator's words — same posture as the world-options gate.

### 2. The review surface is the form that already exists

Nothing is staged, nothing is canon. The response **prefills the Create a
simulation form**: premise, faction cards, suggested projects (as copyable
hints), secret drafts (shown only as proposed DM-only facts). The GM edits
anything they want in place, deletes factions, rewrites goals — then clicks
**Create simulation**, which is the accept, and is the same free,
no-LLM, `validate_faction_docs`-guarded path as today. A **Regenerate**
button re-runs the pass. The design payload is also returned as JSON for
the CLI path (`--design` flag on the studio's sim CLI) so the guardrail
"every app feature has a CLI equivalent" holds.

Suggested BBEG framing (proven in the pilot): ask for one faction whose
goal is the long game (the cult's Seal-dig became the campaign clock via a
target-6 project) and one faction with a hidden true motive to become a
DM-only fact. Encode these as prompt guidance, not as schema.

### 3. Provider documentation

No new plumbing — but `llm.env.example` gains a commented GLM/Flash
configuration block (base URL, `LLM_MODEL=<flash model id>`), so the
"trivial with an API key" path is documented rather than folklore.

## Acceptance criteria

- [x] `POST .../simulation/design` returns a validated, form-shaped design
      from one metered LLM pass; invalid model output is re-asked once,
      then 502s with the validator's words.
- [x] The create form can be fully prefilled from the response, edited
      freely, and created through the unchanged free path.
- [x] Nothing touches `simulation/state.yaml` until Create is clicked.
- [x] Secrets appear only as proposed DM-only knowledge drafts.
- [x] Refuses with 422 when there is no world and no places (fed-or-refused).
- [x] `llm.env.example` documents a GLM/Flash configuration.
- [x] CLI equivalent exists; tests cover validation mapping and the
      no-LLM form path (generator mocked).
