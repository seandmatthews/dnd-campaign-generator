# 67 — Supply-chain pins: movable tags, unpinned actions, floating node

**Status:** proposed
**Priority:** P3 — every "pin" in the chain is either a movable tag or
a floating latest
**Area**: `.github/workflows/ci.yml`, `setup-tools.py`,
`release/build_release.py`
**Found by:** nine-lane adversarial review 2026-10-23 (security lane)

## Problem

1. CI actions pin to major tags (`checkout@v4`, `setup-python@v5`,
   `setup-node@v4`) — mutable references, the standard supply-chain
   recommendation is commit SHAs.
2. FMG is pinned at tag `1.153.1` with an in-code claim that "tags
   are immutable" (`setup-tools.py:49`; same claim over the
   companion in `build_release.py:56-63`) — tags are movable
   server-side. settlemaker already pins a commit SHA
   (`setup-tools.py:51`) — the stronger pattern exists in-repo.
3. The builder takes the LATEST v24 from the live nodejs.org index on
   every build (`build_release.py` node cache) — two builds with
   adjacent stamps can carry different node runtimes with no trace.
4. Release downloads (python embed, node, 7z, get-pip) are trusted
   without checksums — a corrupted cached zip is reused forever.

## Proposal

SHA-pin the three CI actions; pin FMG (and optionally the companion)
at the tag's commit SHA with the tag recorded beside it for humans;
pin node to an exact version; record SHA256s for the four downloads
and verify before reuse (a mismatch re-downloads once, then fails).

## Acceptance criteria

- [ ] CI actions resolve by SHA — directly checkable
- [ ] setup-tools verifies the FMG checkout's SHA, not just marker
      files — extends `test_setup_tools.py`
- [ ] Node version pinned; downloads checksum-verified — same file's
      tests / a builder unit test
