# AGENTS.md — start here

A standalone prose-verification kit for D&D generated content. No game
state, no world data — it checks text against rules and attackers. Point it
at a file or directory and read the queue it leaves behind.

Quickstart, ground rules, and the layer model: [`README.md`](README.md).
Run `python tests/test_check.py` before and after changing anything in
`check.py` (zero-dependency, direct-run).

Non-negotiables:

1. Findings, not judgments. Every finding enters the queue as
   `{file, line, rule, level, quote, note}` and quotes exact text. The LLM
   prompts may ask the model for the lighter `{rule, quote, note,
   severity}` shape — check.py decorates the rest before validation; a
   reviewer that returns prose praise is broken by design.
2. Cheapest layer first. Structural checks before Vale; Vale before LLM
   passes; `--llm` is opt-in because it spends money.
3. Never auto-reject. The kit produces a review queue; the human decides.
4. The lexicon ratchets: every slop instance caught by hand becomes one
   line in `styles/Tabletop/Regression.yml`.

## Key-discovery contract

`check.py` resolves its provider exactly like the twins
(`agentic-simulator/src/llm.py`, `studio/llm_client.py` — an import-free
copy of the same contract): `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`
from the environment, then the repo-root `llm.env`, then the built-in Z.ai
coding-plan defaults; with `LLM_API_KEY` unset, the legacy discovery
(`$ZAI_API_KEY`, then a walk of `~/.zcode/v2/provider_config.json`'s
`providerRules` — rule 1 is the coding-plan key and is scanned first)
still applies. A missing key is a readable RuntimeError at call time,
never at import. The `--llm` passes are metered like the studio and the
engine: an `LLM_MAX_CALLS` ceiling per invocation (default 24). Never
hardcode a provider index, the rules array reorders.
