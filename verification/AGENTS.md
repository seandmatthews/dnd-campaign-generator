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

`check.py`'s `_find_key` finds the Z.ai key in this order: `$ZAI_API_KEY`,
then a walk of `~/.zcode/v2/provider_config.json`'s `providerRules` taking
the first non-`enc:` key, then a clear RuntimeError. The sibling copies
(`agentic-simulator/src/llm.py`, `studio/llm_client.py`) keep the same
order and error — never hardcode a provider index, the rules array reorders.
