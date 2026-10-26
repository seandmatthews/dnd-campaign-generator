# 61 — Local API: mutations need an Origin check, not just a Host allowlist

**Status:** completed (implemented by the 61-70 ticket sweep 2026-10-25/26, fd9c19a, reviewed round 1; merged as a5ce8db with green main gates; the sweep's final fast tier AND --all passed on integrated main)
**Priority:** P2 — any webpage the GM visits can fire no-preflight
mutating POSTs at the running studio
**Area:** `studio/server.py`
**Found by:** nine-lane adversarial review 2026-10-23 (security lane,
probe-verified against the repo venv)

## Problem

The middleware allowlist checks `Host` only (`server.py:194-198`) —
it answers DNS rebinding, not cross-site request forgery. A browser
sets `Host: 127.0.0.1:8765` on cross-origin requests too, so any
hostile page can fire *simple* requests (no preflight: no custom
headers, no JSON body) at no-body mutating endpoints. Probe-verified
reachable set: `POST .../lore/obsidian` (writes the user's
`%APPDATA%/obsidian/obsidian.json`), `POST .../snapshot`,
`POST .../maps/world-map`, `POST .../table-setup/publish-*`
(spawns the companion), `POST /api/jobs/{id}/cancel`, and
`GET /api/doctor` (a live 16-token LLM probe — drip-spend of the real
key, cached 60s). JSON-bodied endpoints are currently saved only by
FastAPI 0.141 refusing text/plain bodies for Pydantic models — a
version behavior, not a design guarantee.

## Proposal

Extend the middleware: mutating methods (POST/PUT/PATCH/DELETE) must
carry an `Origin`/`Sec-Fetch-Site` header proving same-origin
(exact `http://127.0.0.1:<port>` / `http://localhost:<port>` /
`http://[::1]:<port>`), else 403. Non-mutating GETs keep Host-only
(reads leak nothing but cached state). No CORS layer is added — the
UI is same-origin.

## Acceptance criteria

- [x] Foreign-Origin (or missing-Origin) POST to a no-body endpoint →
      403, nothing executed — pinned in a studio test extending
      `test_hardening.py`
- [x] Same-origin POST (the UI's own shape) passes unchanged — same
      test
- [x] GET under a hostile Host still 403s via the existing allowlist —
      existing tests stay green
