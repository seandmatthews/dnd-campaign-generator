# 66 — Spend visibility: the two priciest flows record zero usage

**Status:** proposed
**Priority:** P3 — the most expensive operations are the least
metered ones
**Area:** `studio/engines.py`, `studio/llm_client.py`,
`agentic-simulator/src/llm.py`, `llm.env.example`
**Found by:** nine-lane adversarial review 2026-10-23 (LLM + product
lanes)

## Problem

Week and forecast run records carry identity (engine/model/base_url)
but no usage — contrast `simdesign.py:473-474` and
`sessions.py:1276-1283`, which record `usage`/`llm_tokens`. The only
week visibility is per-call stderr lines into a capped log tail; a
forecast ("N× a week's API spend", `routers/simulation.py:109`)
records nothing durable. Meanwhile `LLM_MAX_CALLS` is env-only while
every sibling knob lives in llm.env (`llm.env.example` never mentions
it), the `BudgetExceeded` text says "raise LLM_MAX_CALLS" without
saying where, and one global env value silently overrides the tick's
roster-scaled default (`engines._run` merges `os.environ` into engine
children).

## Proposal

Thread `llm_client.run_usage()` (and the engine's per-run tally from
stderr) into the week/forecast run records; document LLM_MAX_CALLS in
llm.env.example with its interaction (env beats the roster default);
the BudgetExceeded message names llm.env + the env var as both valid
homes.

## Acceptance criteria

- [ ] Week + forecast run records carry calls/tokens — studio tests
      on the run-record shape (faked engines carry a canned tally)
- [ ] llm.env.example documents the knob — directly checkable
- [ ] BudgetExceeded text names where to set it — engine test on the
      message
