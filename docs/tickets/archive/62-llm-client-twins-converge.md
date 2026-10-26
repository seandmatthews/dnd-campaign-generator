# 62 — LLM clients: the engine twin keeps its crash, truncation reads as complete, and check.py ignores llm.env

**Status:** completed (implemented by the 61-70 ticket sweep 2026-10-25/26, 4fd5d62, reviewed round 1; merged as 1bdb6d9 with green main gates; the sweep's final fast tier AND --all passed on integrated main)
**Priority:** P2 — one crash the studio twin already fixed, silent
truncation into canon, and a third client with its own hardcoded
provider
**Area:** `agentic-simulator/src/llm.py`, `studio/llm_client.py`,
`verification/check.py`
**Found by:** nine-lane adversarial review 2026-10-23 (LLM lane)

## Problem

Three defects in the client layer:

1. **Engine usage deref before the shape guard** —
   `agentic-simulator/src/llm.py:239` runs `data.get("usage") or {}`
   before any `isinstance(data, dict)` check. The studio twin fixed
   exactly this (P3-24, `studio/llm_client.py:242-250`); the twin
   wasn't patched. A 200 whose body is a JSON array (proxy anomaly)
   → `AttributeError` → per-faction HOLDS, possibly a full-week
   abort with a cryptic cause.
2. **Known-truncated output returns as complete** — both clients:
   when `finish_reason == "length"` survives to the budget cap,
   `complete()` returns the longest content seen with no marker
   (`llm.py:255-260`, `llm_client.py:268-274`); "longest-wins" can
   prefer a truncated reply over a shorter complete one. The
   chronicle writer consumes it verbatim — a mid-word cut lands in
   staged canon with nothing flagging it.
3. **check.py is a third client** — `verification/check.py:33-34,
   254-289` hardcodes the z.ai endpoint + `glm-5.3-flash`, reads only
   `$ZAI_API_KEY`/ZCode-config (never llm.env), scans provider rules
   in a different order, and has no metering, retry, or shape guards
   (`data["choices"][0]` IndexErrors on a no-choices 200). A user on
   their own provider gets silent wrong-key spend or a bogus "no
   key" failure.

## Proposal

Port the shape guard to the engine twin; return/raise a truncation
marker from `complete()` when `finish_reason` is `length` at cap
(callers surface it — the chronicle gets a visible `[truncated]`
note, the longest COMPLETE reply wins over a longer truncated one);
re-point check.py's `_find_key`/endpoint/model at the llm.env
precedence chain (import-free copy of the documented contract, or
spawn through one of the twins) with metering + the same 429/5xx
retry and a choices-shape guard.

## Acceptance criteria

- [x] 200-with-array body → readable provider error, not
      AttributeError — engine test mirroring the studio twin's
- [x] Length-exhausted completion is marked/handled and a complete
      shorter reply beats a longer truncated one — engine + studio
      tests
- [x] check.py resolves endpoint/key/model from llm.env precedence and
      meters its calls — verification direct-run test
- [x] No-choices 200 → readable error — same test
