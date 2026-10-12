# 52 — update.cmd: the friend-visible updater

**Status:** proposed
**Priority:** P1 — the point of the payload split (needs ticket 51)
**Area:** `release/templates/` (`update.cmd`; reuses install.cmd's
preserve logic), `release/build_release.py` (stamp), the install tree
**Found by:** the distribution design, 2026-10-12
(`docs/2026-10-12-distribution-design.md` §3).

## Problem

Today the only update channel is "receive a new 155MB exe and run it."
The installed tree already has both halves of something better —
install.cmd preserves `campaign-data/` across updates, and VERSION is
build-stamped — but nothing compares the installed stamp to a newer
release, and the LLM key is baked into every payload, making rotation a
re-delivery.

## Proposal

Per the design doc §3:

1. The VERSION file gains a stamp block written at CI build time: app
   payload tag/SHA, tools-pack rev, companion release tag (ticket 53).
2. `update.cmd` in the install root: query
   `releases/latest` (public, unauthenticated), compare against the
   installed stamp, download the app-payload asset, verify its SHA256
   against the release body, snapshot the current source dirs (the
   resolve-apply precedent — crash-recovery must be able to see the
   previous tree), swap, preserve `campaign-data/` and `llm.env/`.
3. Explicit, never silent: print `installed <tag> → available <tag>` and
   require a keystroke; network failure or bad tag → keep current, say
   so plainly. `launch.cmd` never invokes it; updating is a gesture the
   DM chooses.
4. **`llm.env` leaves the payload.** It lives in the install root,
   written once (first-install exe may seed it; update payloads never
   contain it). Rotation = edit one file; the existing llm.env
   resolution order (env > file > legacy) is unchanged.

## Acceptance criteria

- [ ] End-to-end on this machine: tag a trivial change → `update.cmd` on
      a scratch install lands the new tree, `campaign-data/` and
      `llm.env/` byte-identical across the swap.
- [ ] Offline / unreachable / SHA mismatch → clear message, tree
      untouched — checked live; the parsing pieces pinned by tests where
      they are Python-side.
- [ ] Key rotation: change `llm.env`, restart, the new key is the one
      used (existing resolution honored; no payload involvement).
- [ ] Stamp: a fresh install's VERSION block names the tag it came from
      — `update.cmd`'s compare is against something real.

## Implementation notes (YYYY-MM-DD)

_lands here when the status flips._
