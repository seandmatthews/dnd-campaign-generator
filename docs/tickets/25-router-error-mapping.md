# 25 — Router error mapping & input-gate consistency

**Priority:** P3
**Area:** `studio/routers/knowledge.py`, `routers/lore.py`, `routers/sessions.py`,
`routers/quests.py`, `studio/llm_client.py`
**Found by:** 2026-10-02 full review P3-13, P3-14, P3-15, P3-16, P3-22, P3-24

## Problem

Six spots where the route's error mapping or input gate drifts from the
app's own conventions:

1. **Knowledge record/supersede leak publication errors to a raw 500**
   (P3-13). `routers/knowledge.py:91-140` maps `KnowledgeError` and
   `PublicationConflictError` but not `publication.RevisionConflict` /
   `PublicationError`, which `publish()` actually raises — a stale base on
   knowledge.yaml becomes `500 internal error: RevisionConflict` instead of
   a 409 (every other publisher maps both).
2. **`POST /lore/name` answers provider failures with 500 not 503**
   (P3-14). `routers/lore.py:264-277` catches `FedOrRefused` /
   `BudgetExceeded` but not bare `RuntimeError` — its sibling LLM endpoints
   (`simulation.py:384`, `maps.py:260`) do.
3. **`RevealBody.week` unvalidated (P3-15).** Ticket 14 added the `>= 1`
   gate to `FactBody.week`; `RevealBody` (`knowledge.py:65`) still accepts
   0/negative — "known since week -5" persists into the disclosure store
   and the player handout.
4. **`GET sessions/{id}` returns 422 for an unknown id (P3-16).** API.md
   documents 404, and the sibling extract route maps the same
   `SessionError` to 404 (`routers/sessions.py:164`); the GET doesn't
   (`:91-94`).
5. **`quests.set_status` can 500 between panel load and locked re-read
   (P3-22).** `quests.py:374-375` re-reads the page under the lock outside
   `load()`'s per-page guard; a page breaking in that window
   (`UnicodeDecodeError` / `PlaceError`) is in neither `_QUEST_422` nor the
   global handler → opaque 500 instead of a readable 422 naming the file.
6. **`llm_client.complete` dereferences before its own guard (P3-24).**
   `llm_client.py:243` does `data.get("usage")` before the
   `isinstance(data, dict)` check that protects `choices` at `:251` — a
   non-dict 200 body (proxy returning a JSON array) → `AttributeError`.

## Proposal

Map the typed errors where the siblings already do; move the isinstance
guard above the first dereference; add the missing validators. All six are
mechanical — no design decisions.

## Acceptance criteria

- [ ] Stale-base knowledge write → 409 with the standard message shape
      (not 500); mid-flight publication failure keeps the "staged
      transaction kept" wording.
- [ ] Provider outage on /lore/name → 503 (matching siblings).
- [ ] `reveal {"week": 0}` → 422; positive weeks unaffected.
- [ ] Unknown session id on GET → 404 (API.md unchanged and now true).
- [ ] Page breaking between load and locked re-read → 422 naming the file.
- [ ] Non-dict 200 from the provider → the readable provider-error path,
      never AttributeError.
