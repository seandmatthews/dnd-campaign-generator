# 69 — Studio honesty smalls: doc drift, hardcoded version, the 504 that lies

**Status:** completed (implemented by the 61-70 ticket sweep 2026-10-25/26, 4e8ff0c, reviewed round 1; merged as 935efc6 with green main gates; the sweep's final fast tier AND --all passed on integrated main)
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

- [x] API.md's simulation shape + engine line match the code — the
      existing doc-pinning test pattern
      (`test_api_doc.py::test_simulation_doc_names_every_key_the_route_returns`
      pins every key the route answers into the doc's shape line,
      `::test_views_engine_line_carries_the_state_flag` pins `--state`
      into the engine line, `::test_health_doc_does_not_pin_a_hardcoded_version`
      keeps the health line honest too)
- [x] health carries the install VERSION — studio test with a staged
      VERSION (`test_system.py::test_health_carries_the_install_version`,
      plus the pyproject fallback and the stamp-wins / blank / "unknown"
      ladder in `::test_install_version_stages_win` and the rewritten
      `::test_health_ok`)
- [x] The 504 detail no longer claims "nothing was changed" — studio
      test on the detail string (`test_table_setup.py::test_publish_times_out_honestly`
      and the scenes twin `test_table_scenes.py::test_scenes_times_out_honestly`)
- [x] Concurrent doctor calls collapse to one probe — studio test on
      the lock (`test_system.py::test_concurrent_doctor_calls_collapse_to_one_probe`,
      with `::test_doctor_cache_still_serves_within_the_window` keeping
      the 60s cache)
- [x] Log box caps its DOM tail — cjs harness (`studio/tests/js/test_maps_log_clean.cjs`:
      the cap is derived from jobs.py's LOG_CAP so the two cannot drift,
      plus the `open()` reset leg; the stale-`factPrefill` fix rides the
      same change-set, pinned by `studio/tests/js/test_fact_prefill.cjs`)
