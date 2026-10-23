# 58 — Git-less installs: designed degrade for the undo spine + a friend backup story

**Status:** proposed
**Priority:** P1 — the core safety promise ("history is your undo") is
dead on 100% of friend installs and presented as a permanent error;
the data itself has no backup story at all
**Area:** `studio/canon.py`, `studio/publication.py`,
`studio/doctor.py`, `studio/static/js/core.js`,
`release/templates/README.txt`
**Found by:** nine-lane adversarial review, 2026-10-23 (release lane;
engine and backend lanes converged on the transaction growth and
irreversibility)

## Problem

The release bundle ships no git, so `canon.py:37,130-134` returns
state `unavailable` for every snapshot on every installed copy:

- The UI renders `unavailable` like `failed` (`core.js:342-380,
  426-440`): a toast after every publication, a persistent banner
  ("the git undo trail is missing it"), and a Retry button that can
  never succeed ("Snapshot still unavailable").
- `publication.py:788-803` retains every transaction's full
  before/after payload while the snapshot state never reaches
  `SNAPSHOT_OK_STATES` (`:98`) — receipts are capped
  (`RECEIPT_CAP = 200`, `:85`) but transactions are not: each accept,
  quest flip, and knowledge record accumulates forever under
  `.studio-transactions/`.
- The doctor has NO git check — the one machine class where the undo
  spine is dead reports all-clear.
- Accept is effectively irreversible: the pre-accept bytes exist only
  as the retained before-image, which the next successful snapshot
  retry prunes (`publication.py:625-639`), and no UI action exposes
  it as undo anyway.

And the data itself has no story: `README.txt`'s "Your data" section
names "the 'campaigns' folder next to this file" — wrong;
`launch.cmd` points `CAMPAIGN_DATA_ROOT` at
`%LOCALAPPDATA%\Campaign Studio\campaign-data\` — has no backup
line, and the only export is a CLI (`python
studio/export_campaign.py`) the friend cannot run (no python on
PATH). A dead laptop loses a year of campaign + undo history with
nothing anywhere having told them to copy a folder.

## Proposal

Two halves, each with a ruling callout.

**(a) Designed degrade** — RULING NEEDED: treat `unavailable` as the
expected state when git is absent (recommended), vs shipping a
portable git in the bundle (likely rejected on size):
- Distinct copy for `unavailable` on installs without git: a
  one-time notice per campaign, not a permanent banner; no dead
  Retry (absent, or disabled with the reason).
- Bound transaction retention: prune non-OK-state transactions
  beyond a cap — the before-image's undo value is already destroyed
  by the first successful snapshot retry, so unbounded retention is
  pure growth.
- Doctor gains a git check that NAMES the missing undo trail (info on
  bundle installs, never a failure — it is a designed state).

**(b) Friend backup** — RULING NEEDED: an Export button in the UI vs
an `export.cmd` beside `launch.cmd` driving the bundled python
(recommended — smaller, no review flow):
- `README.txt` names the real data path and gains a one-paragraph
  backup instruction ("copy this folder").
- The friend-runnable backup path exists and works without a system
  python.

## Acceptance criteria

- [x] `unavailable` renders as designed degrade (one-time notice, no
      permanent banner, no dead Retry) — pinned by a js harness on
      the snapshot-state branches plus a studio test on the snapshot
      report shape
- [x] Non-OK-state transactions pruned under a cap — extends
      `studio/tests/test_publication.py`
- [x] Doctor reports the git/undo state — studio test on the doctor
      report keys
- [x] README names the real data path + backup line — directly
      checkable
- [x] Friend backup path runs without a system python — pinned by
      the release smoke or a studio test once the ruling lands
