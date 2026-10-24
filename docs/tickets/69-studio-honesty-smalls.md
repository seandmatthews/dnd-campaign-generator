# 69 — Studio honesty smalls: doc drift, hardcoded version, the 504 that lies

**Status:** proposed
**Priority:** P3 — a batch of small honesty fixes, each independent,
grouped because none is a lane alone
**Area:** `studio/API.md`, `studio/routers/{system,table}.py`,
`studio/static/js/{core,sim}.js`
**Found by:** nine-lane adversarial review 2026-10-23 (backend +
frontend lanes)

## Problem

1. API.md drift: `GET .../simulation` is documented without
   `places`/`projects` (code returns both), and the views engine line
   contradicts both the code and API.md's own earlier line (missing
   `--state`). API.md is a contract — code and doc move together.
2. `/api/health` hardcodes `"version": "0.1.0"` while the carefully
   built VERSION stamp is invisible at runtime — a friend reporting a
   bug has no in-app way to name their build.
3. The table-setup 504 asserts "nothing was changed" while
   `subprocess.run(timeout=...)` kills the companion mid-publish —
   partial `atlas-vtt/` writes are real, and the skipped snapshot
   lets them ride into a later unrelated commit.
4. Concurrent non-quick `GET /api/doctor` calls both run the full
   minutes-long probe (no lock on `_doctor_cache`) — the same
   threadpool-drain hazard review.py already fixed for check.py.
5. Log boxes grow unbounded in the DOM during chatty runs
   (`makeLogBox` appends every line for the life of the watch).
6. A stale `factPrefill` can self-fill the record form much later if
   the handoff render fails.

## Proposal

API.md catches up to the code; health reads the install's VERSION
(falls back to pyproject's); the 504 copy says "the publisher was
killed at the timeout — any partial writes are uncommitted; check
atlas-vtt/ before republishing"; a doctor probe lock (second caller
waits or gets the cached/running answer); the log box keeps a capped
tail (the logtail ring's size); `factPrefill` is cleared once
consumed regardless of render success.

## Acceptance criteria

- [ ] API.md's simulation shape + engine line match the code — the
      existing doc-pinning test pattern
- [ ] health carries the install VERSION — studio test with a staged
      VERSION
- [ ] The 504 detail no longer claims "nothing was changed" — studio
      test on the detail string
- [ ] Concurrent doctor calls collapse to one probe — studio test on
      the lock
- [ ] Log box caps its DOM tail — cjs harness
