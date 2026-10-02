# 17 — Kill CRLF-doubling in the engine-copy seam (canon corruption)

**Priority:** P1
**Area:** `studio/routers/simulation.py`, `studio/sessions.py`, `studio/spine.py`
**Found by:** 2026-10-02 full review P1-1 (independently reproduced twice)

## Problem

Every studio text writer opens text-mode, so state.yaml is CRLF on disk
(Windows hand-edits in Notepad do the same). The staging flows that run the
engine against a temp copy of the state do:

```python
state_text, base_revision = proposal_flows.capture_state(state_path)
# ... capture_state decodes raw bytes, \r\n intact
copy_path.write_text(state_text, encoding="utf-8")   # text mode AGAIN
```

Text mode re-translates every `\n`, so `\r\n` becomes `\r\r\n` in the copy
the engine parses. YAML folds one break inside a plain scalar to a space but
two breaks to a literal newline — so **every dumper-wrapped scalar (width
100: premises up to 600 chars, long goals/secrets/event texts) silently
mutates** (`…raised the toll…` → `…raised\nthe toll…`). The engine re-dumps
the mutated document, the studio stages the after-image, and **accept
publishes the corrupted values into canon**.

Four affected sites, same bug:

1. `routers/simulation.py:667` — post_session (session effects record)
2. `routers/simulation.py:830` — prepare_projects
3. `routers/simulation.py:940-941` — goal-review job
4. `sessions.py:878` — sessions.prepare (session_changes)

Root cause part 2: `spine.write_text_atomic` (`spine.py:362`) itself opens
text-mode (`os.fdopen(fd, "w")`), so any input text that already carries
`\r\n` gets doubled wherever it is written (style files, session notes).

Trigger: any Windows campaign + record a session / prepare project changes /
run a goal review / prepare session-changes with any effect → accept. The
codebase already knows the rule (`publication.revision_of_bytes` documents
the hazard; page writes use `write_bytes`) — these five sites missed it.

## Proposal

- Write the four state copies with exact bytes:
  `copy_path.write_bytes(state_text.encode("utf-8"))` — the copy must be
  byte-identical to what was read, no translation at all.
- `write_text_atomic`: open with `newline=""` (write the text as given) so
  CRLF-carrying inputs stop doubling. New dumps stay LF; that is fine and
  consistent — `_page_snapshot` and the YAML readers accept both endings.
- Leave the engine's own `store.save_state` alone (single translation is
  valid YAML); its temp-name hygiene is ticket 26.

## Acceptance criteria

- [x] A regression test at the SEAM, not the unit: build a state.yaml with
      CRLF on disk containing wrapped plain scalars (premise > 100 chars);
      run the session-effects staging path against a faked engine apply;
      assert the engine's parsed copy has values equal to the canon parse
      (no `\n` where a space was) — and assert the same for all four flows
      or one shared helper if the flows converge.
- [x] `write_text_atomic` with `\r\n`-carrying input writes those bytes
      exactly once (no `\r\r\n` on disk) — unit test.
- [x] No `write_text` on captured-state text remains (grep the four sites);
      canon before/after an accept shows only the intended diff.
- [x] Ticket 10's byte-preservation tests still pass unchanged (page writes
      were already `write_bytes` — they must be untouched by this fix).
