# 21 — simdesign: scalar `resources` must re-ask, not 500

**Status:** completed — commit 326c204 (2026-10-02); code-verified and archived 2026-10-04.
**Priority:** P2 (small, isolated)
**Area:** `studio/simdesign.py`, `studio/spine.py`
**Found by:** 2026-10-02 full review P2-4

## Problem

`_validate_reply` passes the raw model value for `resources` into
`spine.validate_faction_docs`:

```python
entry = {..., "resources": f.get("resources") or {}}   # a str passes through
# spine.py:900:  for key, level in (f.get("resources") or {}).items():
#                AttributeError: 'str' object has no attribute 'items'
```

A reply with `"resources": "high"` (or a list) raises `AttributeError`,
which is not `ValueError` — so `design_simulation`'s re-ask never runs and
the router's `FedOrRefused/DesignError/BudgetExceeded/RuntimeError` mapping
doesn't match → `500 internal error: AttributeError`, with the metered call
wasted. Reproduced with a 2-faction reply. This defeats the ticket-15
validation posture (bounded re-ask on model-shape problems) for exactly the
shape class re-ask exists for.

## Proposal

In `_validate_reply` (or at `spine.validate_faction_docs`'s entry): coerce
`resources` to a dict only when it is one; any other shape appends an issue
string so the existing `ValueError` → re-ask path handles it. Same treatment
for any sibling field consumed with `.items()`/`.get` on the reply path.

## Acceptance criteria

- [x] A reply with scalar/list `resources` triggers a re-ask (issue named in
      the re-ask quote or findings), never a 500 — unit test with the faked
      client returning the bad shape then a good shape.
- [x] Grep the simdesign reply path for other unguarded `.items()`/dict
      assumptions on model-supplied values; each either coerced or
      issue-reported.
