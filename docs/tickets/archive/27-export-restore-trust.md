# 27 — Export/restore trust boundary: exclusions are one-way today

**Status:** completed — merged with the 2026-10-02 review-sweep waves; code-verified and archived 2026-10-04.
**Priority:** P3
**Area:** `studio/export_campaign.py`
**Found by:** 2026-10-02 full review P3-25, P3-26

## Problem

The exporter carefully excludes `.git/*`, `llm.env*`, `.studio-jobs/*`,
`.studio-transactions/*`, `simulation/forecasts/*`, `__pycache__/*`
(`EXCLUDES`, lines 51-65). The restore side ignores that list entirely:

1. **`_safe_member` refuses traversal but accepts every excluded class**
   (P3-25, repro: `campaign/.git/hooks/post-commit`, `campaign/llm.env`,
   `campaign/simulation/forecasts/f/manifest.yaml` all pass). Restoring a
   hand-zipped campaign folder (Explorer zip of a live campaign — a very
   plausible way to share one) resurrects: a stale ACTIVE forecast
   manifest that wedges new forecasts with 409 (the exact failure the
   exclusion was written to prevent, per its own comment); an `llm.env`
   credential the export promises never rides along; a planted `.git`
   that makes the campaign a nested repo. Manifest hashes don't
   discriminate — the manifest rides in the same archive.
2. **The exporter's own `llm.env*` pattern only matches top-level**
   (P3-26). `fnmatch('sessions/llm.env', 'llm.env*')` is False — the
   pattern anchors at the string start, so a credential copy at any depth
   rides the archive, contradicting the docstring "a credential dropped
   inside the campaign never rides along".

## Proposal

- Restore applies the same `EXCLUDES` (skip excluded members with a
  warning naming them — a hand zip of a live campaign SHOULD restore its
  forecasts dir as absent, matching a studio export's shape).
- Make the credential pattern depth-independent (`*/llm.env*` plus the
  top-level pattern, or match on the basename for the credential class
  specifically).

## Acceptance criteria

- [x] Restore of an archive containing `.git`, `llm.env` (top-level and
      nested), and a forecast manifest: none land on disk; the response
      names what was skipped.
- [x] A studio-exported archive restores identically to today (the
      exclusion list is shared, so nothing legitimately in an export is
      dropped) — round-trip test.
- [x] `excluded('sessions/llm.env')` is True (unit test on the pattern).
