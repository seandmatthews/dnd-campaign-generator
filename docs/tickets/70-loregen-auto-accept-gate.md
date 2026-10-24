# 70 — Loregen: the auto-accept gate checks names, not claims

**Status:** proposed
**Priority:** P3 — a prompt-injection-shaped gap with real containment
but a weak gate
**Area:** `studio/loregen.py`, `studio/routers/lore.py`
**Found by:** nine-lane adversarial review 2026-10-23 (LLM lane,
marked SPECULATION for reach; the mechanism is code-verified)

## Problem

`_hallucinated_names` (`loregen.py:284-301`) is the only mechanical
content check on generated lore; factual claims are policed solely by
the canon-audit LLM pass, which reads the same injectable text (entity
pages are Obsidian-editable; faction `public_action` free text loops
back into next week's prompts). Entries staged with
`auto_accept: true` publish without a human read unless an
ERROR-level finding fires (`routers/lore.py:48-54`) — so
instruction-shaped text planted in an imported world file or a lore
page could ride the chronicler prompt into canon as long as its
proper nouns are grounded. Containment held in the review (targets
clamped, everything staged, key never in a prompt, no file-write
escape) — the gap is the gate's weakness on the one path that skips
the human.

## Proposal

Tighten the auto-accept gate: auto-accept requires a clean FULL
verification ladder (not just structural — the ladder already exists
in verification/), and zero findings at ANY level; anything else
degrades to the normal review queue with a note. Mark model-authored
text as untrusted in the prompts that consume it (one line in the
loregen/chronicler system prompts). Re-verify the ladder's runtime is
acceptable on the staged set; if not, run it only on the auto-accept
path (human-reviewed entries keep today's cost).

## Acceptance criteria

- [ ] Auto-accept with any non-clean ladder result → staged for review
      with a reason — studio test
- [ ] The prompts consuming generated text carry the untrusted-text
      line — copy-pinning test
- [ ] The auto-accept path runs the full ladder within the existing
      job budget — measured in the test run
