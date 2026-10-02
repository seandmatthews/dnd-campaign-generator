# 20 — Replay verification robustness & resolve rules honesty

**Priority:** P3
**Area:** `agentic-simulator/src/tick.py` (replay_verify), `resolve.py`
**Found by:** 2026-10-02 full review P3-1, P3-9, P3-10

## Problem

1. **replay_verify crashes on malformed replays it promised to degrade
   (P3-1, repro).** A structurally broken replay file is supposed to be "a
   message, never a traceback". But `outcomes` entries that aren't dicts hit
   `r.get` in `_divergence_message` (`tick.py:446-448`) → `AttributeError`
   escaping the `except` at `:409`; and the project-recompute block's
   `except (KeyError, ValueError, TypeError)` (`:428`) omits `StopIteration`,
   which `apply_mechanical`'s `next(f for f in ...)` (`:140`) raises for a
   proposal whose actor is absent from the embedded base_state. Existing
   tests only cover missing keys (`test_tick.py:661`).
2. **`project_rule_version` written but never compared (P3-9, trace).**
   Replay schema 3 records it (`tick.py:348`) but the recompute diffs
   project mechanics with the *current* `projects.py` regardless
   (`:413-436`). After a future mechanics bump, old replays will report
   "week N diverged in project mechanics" — implying corruption — instead
   of the honest "rules changed, not comparable" the dice path already
   gives (`:388-391` checks `rule_version`).
3. **resolve.py's stated overextension rule contradicts the code
   (P3-10, trace).** The docstring says "defending does not overextend the
   defender" (`resolve.py:160-163`), but an ACTING defender's defenses do
   increment `contest_count` (`:215-219`), and a faction whose proposal
   wasn't an attack yet is attacked by two rivals rolls both defenses at -2
   and never has its own proposal resolved at all — the chronicle labels
   its district action "succeeded/failed (contested vs X)" when it was
   never rolled against the district. Code is self-consistent; the spec
   sentences conflict.

## Proposal

- Catch by contract, not by exception list: validate shapes (outcomes
  entries are dicts) before the comparison loop; make the project-recompute
  guard cover `StopIteration` (or look up actors without `next()`).
- Gate project-mechanics comparison on `project_rule_version` exactly as
  the dice path gates on `rule_version`.
- Resolve ruling needed (GM decision): either defenses don't overextend an
  acting defender and uncontested proposals of contested defenders still
  resolve (docstring wins), or the docstring is corrected to match the
  code. Record the ruling in the docstring either way.

## Acceptance criteria

- [ ] `--replay` on a doc with `outcomes: ['bogus']` and on a replay whose
      base_state lacks the actor: both exit with a readable message, never
      a traceback.
- [ ] A replay with a mismatched `project_rule_version` reports
      "not comparable" instead of divergence (test with a doctored version).
- [ ] The overextension ruling is decided, implemented or documented, and
      pinned by a test that exercises an acting defender attacked by two
      rivals.
