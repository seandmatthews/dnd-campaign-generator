# 56 — Campaign identity: aliasing splits the lock; two CLI writers skip it

**Status:** proposed
**Priority:** P1 — silent mutual-exclusion bypass on the campaign lock
and silent last-writer-wins on the canon state file
**Area:** `studio/spine.py`, `studio/canon.py`,
`agentic-simulator/src/{projects,effects,store}.py`
**Found by:** nine-lane adversarial review, 2026-10-23 (backend lane
probe-verified the aliasing on this machine; engine lane found the
CLI gaps)

## Problem

Two gaps in the "one writer per campaign/state" contract.

**(a) Name aliasing (studio).** `_NAME_RE` (`studio/spine.py:52`)
accepts mixed case and trailing dots for EXISTING campaigns (only
creation uses the stricter `_NEW_NAME_RE`). On NTFS, `foo.` and
`Ember-Coast` resolve to the same directory as `foo`/`ember-coast`,
but the lock file is name-mangled (`spine.py:138-139`):
`campaign_lock('foo.')` holds `foo..lock` while every writer on `foo`
holds `foo.lock` — same canon, different locks, mutual exclusion
silently bypassed. Probe-verified 2026-10-23: a write addressed to
`foo.` lands in the real `foo/` directory while holding the wrong
lock. The alias also reaches git: `git add -A -- "foo."` exits 128
("pathspec did not match any files"), so every alias write goes
un-snapshotted.

The same hole opens from inside a valid campaign: `list_campaigns`
(`spine.py:569-571`) advertises a hand-edited `campaign.yaml` `name:`
whenever it is merely VALID (`Ember-Coast` for folder `ember-coast`),
never checking it matches the directory. The UI then drives
everything under the alias — files and lock coincide on NTFS, but
`git add -A -- "Ember-Coast"` silently stages nothing (rc=0, empty)
and `git commit --only` fails pathspec: the campaign's undo trail is
dead from a one-line hand edit, reported only as snapshot `failed`.

**(b) Unlocked CLI writers (engine).** `projects.py --apply` is the
documented DM canon-mutation path (`agentic-simulator/AGENTS.md`),
yet it saves the shared state.yaml with no `standalone_home_lock`
anywhere in the module (`projects.py:1062`; the module never imports
it) — while `store.py:51-62` explicitly enumerates the locking CLIs
("tick.py --live, effects.py --apply, ingest.py --apply") and misses
it. Run it while a week tick or studio job holds the lock: it loads
pre-week state, applies, saves; the week's save then overwrites it —
or the reverse, leaving a chronicle on disk for a week the state
never recorded (the exact failure the state→replay→chronicle order
exists to prevent, `tick.py:324-326`). `effects.py --apply` has the
sibling bug: it takes the lock only around the save
(`effects.py:546-551`); the load and apply run unlocked
(`:521-531`), so two fast applies interleave last-writer-wins.
Contrast the already-fixed `ingest.py:204-208`, which wraps
load+apply+save inside the lock.

## Proposal

Canonicalize campaign identity at the boundary; close the lock set.

1. Existing-campaign routes resolve the requested name against the
   campaigns root and refuse (404) anything that is not the exact
   directory name — a case- or trailing-dot-insensitive MATCH is fine
   as long as the canonical directory name is what flows to locks,
   git pathspecs, and filesystem writes.
2. `list_campaigns` falls back to `d.name` unless `meta.name` equals
   the directory name (case-insensitively) — a valid-but-different
   name never becomes the app's address for the campaign.
3. `projects.py --apply` joins the `standalone_home_lock` claiming
   set (update the `store.py:48-63` enumeration); `effects.py` wraps
   load+validate+apply+save inside the lock, matching ingest.

The two halves touch different projects and are independently
mergeable in either order.

## Acceptance criteria

- [ ] Requests under `foo.` / wrong case either 404 or take the
      canonical lock — pinned by a studio test extending
      `test_campaigns.py` (alias probe: same directory, lock file
      name asserted equal to the canonical one)
- [ ] `listed_name` never diverges from the directory, and snapshots
      succeed under a hand-edited `campaign.yaml` name — same studio
      test (the git-pathspec regression)
- [ ] `projects.py --apply` holds the lock — simulator test: apply
      while a fake holder owns the lock file → clean refusal
- [ ] `effects.py --apply` holds the lock across the whole
      read-modify-write — simulator test mirroring ingest's
