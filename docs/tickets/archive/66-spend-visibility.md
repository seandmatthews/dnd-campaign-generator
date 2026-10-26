# 66 — Spend visibility: the two priciest flows record zero usage

**Status:** completed (implemented by the 61-70 ticket sweep 2026-10-25/26, 80b0ed1, reviewed round 1; merged as f25d160 with green main gates; the sweep's final fast tier AND --all passed on integrated main)
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

- [x] Week + forecast run records carry calls/tokens — studio tests
      on the run-record shape (faked engines carry a canned tally)
      (`test_simulation.py::test_week_run_record_carries_the_engine_tally`
      — the last cumulative `run meter` line wins, with the zero-tally
      shape pinned in `::test_week_job_happy_path_proposal_not_copy_out`;
      `test_forecast.py::test_forecast_run_record_carries_the_summed_tally`
      — per-tick tallies summed over the N subprocesses; keys
      `llm_calls`/`llm_tokens`, the extraction record's naming)
- [x] llm.env.example documents the knob — directly checkable
      (a Per-run call ceiling section: roster default max(24, 3N + 6),
      env beats llm.env, either beats the roster default; llm.env is
      now a REAL second home — `env_limit` in both twins reads it, and
      `engines.week_post_budget` resolves through the same
      `llm_client.env_limit` so the watchdog follows the honest ceiling)
- [x] BudgetExceeded text names where to set it — engine test on the
      message (`test_llm.py::test_budget_exceeded_names_where_to_raise_the_ceiling`
      pins "LLM_MAX_CALLS" + "llm.env"; tick.py's WeekAborted names the
      homes too — that text is what the studio's job error surfaces)
