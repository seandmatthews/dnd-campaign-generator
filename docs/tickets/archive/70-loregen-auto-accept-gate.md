# 70 — Loregen: the auto-accept gate checks names, not claims

**Status:** completed (implemented by the 61-70 ticket sweep 2026-10-25/26, b2b115e, reviewed round 1; merged as c5428e0 with green main gates; the sweep's final fast tier AND --all passed on integrated main)
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

- [x] Auto-accept with any non-clean ladder result → staged for review
      with a reason — studio test
      (`test_auto_accept.py::test_lore_entries_auto_accept_blocked_by_advisory_finding`
      pins that a warning-only run the old error-level gate would have
      published now stays pending;
      `::test_lore_entries_auto_accept_blocked_by_non_clean_full_ladder`
      pins the full-ladder half — findings from the ladder merge into the
      result and block, the bundle stays pending, the manual approve path
      still works on it; `::test_lore_entries_error_finding_blocks_auto_accept`
      carries the error-level case with the reason now listing every
      finding)
- [x] The prompts consuming generated text carry the untrusted-text
      line — copy-pinning test
      (`test_loregen.py::test_prompts_consuming_fed_text_mark_it_untrusted`
      pins the line into BOTH prompts that feed file text — the
      chronicler entries prompt and the missing-pages prompt — as really
      sent to the model; it lives once in `_ETHOS`, which all three
      loregen prompts embed)
- [x] The auto-accept path runs the full ladder within the existing
      job budget — measured in the test run
      (`test_auto_accept.py::test_auto_accept_full_ladder_within_the_job_budget`
      drives the REAL subprocess ladder (`check.py --llm --state`) through
      the real route, measures the whole job with `time.monotonic`, and
      asserts it stays under `loregen.LADDER_TIMEOUT_S` — the same 180 s
      budget the structural pass always ran under; measured ~0.1 s in the
      suite, with the ladder's LLM passes pinned offline so the test
      spends nothing)

## Implementation notes

- The full ladder runs ONLY on the auto-accept path (`loregen.
  full_ladder_findings`, invoked from `_auto_accept_staged`), per the
  proposal's own fallback — human-reviewed entries keep today's cost
  (structural pass + the one canon-audit LLM call). The gate itself is
  the lore staging gate (entries + missing pages, both flow through
  `_auto_accept_staged`); the goal-review and week auto-accepts keep
  their error-level bar — outside this ticket's area.
- "Zero findings at ANY level" includes the ladder's own bookkeeping
  notes: a skipped layer (`vale/skipped` — vale is optional) or a failed
  LLM pass is a finding, so a ladder that cannot FULLY run degrades the
  bundle to review instead of waving it through. The strict reading is
  deliberate: this is the one path that skips the human.
- The ladder's LLM passes run inside the verification/check.py
  subprocess (`engines.run_check` grew `llm=`/`state=` passthroughs; no
  re-implementation — the ladder already exists in verification/), so
  their calls meter against check.py's own LLM_MAX_CALLS ceiling (one
  llm.env governs all three clients) — no new budget knob. A ladder
  killed at the timeout produces no findings.json, which lands as the
  blocking finding — a timeout degrades to review, never publishes.
- The untrusted-data line is one sentence in `_ETHOS` (studio/loregen.py),
  so it rides in the entries, missing-pages, and name-helper prompts
  alike; the copy-pinning test pins the two prompts that feed file text.
