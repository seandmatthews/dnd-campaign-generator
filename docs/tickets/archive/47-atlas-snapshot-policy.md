# 47 — atlas-vtt/ snapshot policy: committed as files, .obsidian/ ignored

**Status:** completed (2026-10-10)
**Priority:** P2 — the other seam item the table needs this week
**Area:** `studio/canon.py`, `studio/doctor.py`, `docs/for-new-gms/ATLAS.md`,
`docs/design/obsidian-integration.md`, `campaigns/.gitignore` (new)
**Found by:** Atlas seam residency ruling, 2026-10-10
(`docs/2026-10-10-next-steps.md` W4; `docs/design/atlas-seam.md` R1/R2, §6).
Adversarial review 2026-10-10 threw out the first draft's ignore mechanism
(wrong event, dead pattern, dead hook) and found a live forecast-bundle
leak — both folded in below.

## Problem

The canon-snapshot repo (`studio/canon.py` — rooted at
`<data_root>/campaigns/`, `canon.py:108`; `_ensure_repo` git-inits it on
demand, `:58-75`; `snapshot()` commits campaign directories, `:78`,
`:118-147`) has no policy for the two directories Atlas brings with it:

- **`atlas-vtt/`** — tuned scenes, and the table's chalk (fog, token
  positions, HP — R1: table state is not canon, but it rides in the scene
  *files*). The seam rules it is *committed*: tuned scenes are earned
  artifacts whose diffs are reviewable, consistent with companion notes
  and `params.yaml` (generated metadata, not reviewed prose). Nothing
  today ignores it, so committing needs no code — but nothing states the
  policy either.
- **`.obsidian/`** — with Atlas installed, plugin state and workspace
  layout start riding into every snapshot. The ignore demand exists only
  as doc guidance (`obsidian-integration.md:202`), and that line's
  pattern (`campaigns/*/.obsidian/`) is written from a root that has no
  repo: inside the snapshot repo, campaign dirs are `campaigns/`
  *children*, so that pattern matches nothing. The depth-agnostic
  `.obsidian/` is what covers both `campaigns/<name>/.obsidian/` **and**
  `campaigns/<name>/lore/.obsidian/` — the lore-folder vault the
  one-click registration creates (`routers/lore.py:147`).

Two more facts the first draft missed:

- **Live bug:** `studio/forecasts.py:10` states "the bundles are
  git-ignored (campaigns/.gitignore)" — that file does not exist and
  nothing creates it, so `git add -A -- <campaign>` sweeps
  `simulation/forecasts/` into every snapshot today, contradicting
  forecasts.py's own docstring (`export_campaign.py:66-68` excludes
  forecasts from exports on the same false assumption).
- **The early-return trap:** `_ensure_repo` returns immediately when
  `.git` exists (`canon.py:61-62`), so any ignore-ensuring hook inside it
  never runs on already-initialized workspaces — i.e., every real
  machine. And an ignore does not untrack: `.obsidian/` committed before
  the ignore lands keeps riding in snapshots while a "not ignored" check
  goes quiet.

## Proposal

**One mechanism, pinned (ruling 2026-10-10): the workspace-root
`campaigns/.gitignore`.** The two alternatives from the first draft are
dead: a hook in the vault registration fires for the wrong directory and
violates `obsidian_vault.py`'s charter ("this module touches nothing
inside the vault"), and the inside-dir trick needs a studio write event
for a directory the studio never creates.

1. **Ensure the file idempotently on the snapshot path** — around the
   `_ensure_repo` early return, i.e. on every `snapshot_result()` call,
   so pre-existing workspaces gain it on their next snapshot. Contents:
   `.obsidian/` (any depth) and a forecast-bundles pattern (e.g.
   `**/simulation/forecasts/` — exact glob at implementation, tests pin
   behavior). Fixes the forecast leak in the same change; forecasts.py's
   docstring finally becomes true.
2. **Doctor advisory check, textual, no git dependency** — one aggregated
   check (the `check_vault` pattern, `_mk(..., required=False)`,
   `doctor.py:306-350`): `campaigns/.gitignore` missing, or present
   without the pinned patterns, → finding with the fix. The fix text
   names the untrack command (`git rm -r --cached '<name>/.obsidian/'`)
   for the already-committed case, since detecting "tracked" generically
   would need git on PATH plus repo machinery this check deliberately
   avoids.
3. **Correct `obsidian-integration.md:202`** in the same change — its
   `campaigns/*/.obsidian/` pattern cannot work from the repo root that
   actually exists.
4. **One paragraph in ATLAS.md** (ticket 46 lands first; this ticket adds
   the paragraph): "your Atlas tuning is snapshotted; the publisher will
   refuse to overwrite a tuned scene once it exists — until then, the
   hand drag is the path." A standing rule with the manual path named,
   not a present-tense publisher promise (the scene publisher is
   companion-repo and code-free today; 46's honesty bar requires this
   phrasing).

## Acceptance criteria

- [x] a file hand-placed under a campaign's `atlas-vtt/` appears in the
      next snapshot commit — snapshot test (the harness exists:
      `studio/tests/test_hardening.py:360-438` runs real git in tmp)
- [x] `.obsidian/` with junk in it — at campaign root AND under `lore/` —
      never appears in any snapshot commit
- [x] a `simulation/forecasts/` bundle never appears in any snapshot
      commit — pins the leak fix
- [x] a pre-existing workspace (repo already initialized, no
      `campaigns/.gitignore`) gains the file on its next snapshot — pins
      the early-return trap
- [x] doctor: missing or incomplete `campaigns/.gitignore` → one
      aggregated advisory finding; complete file → none —
      `studio/tests/test_doctor.py`
- [x] the ATLAS.md paragraph is present (46 landed first)

## Implementation notes (2026-10-10)

Landed with the seam batch (tickets 46-50). `canon.ensure_workspace_ignore`
runs on the snapshot path under `_SNAPSHOT_LOCK` (every `snapshot_result`
call, so pre-existing repos gain the file; `test_snapshot_policy.py` pins
the early-return trap), appending only missing lines
(`.obsidian/`, `**/simulation/forecasts/`) so a user-maintained gitignore
keeps its own text; a write failure logs and the snapshot proceeds (pinned
by the OSError-injection test). The forecast leak is fixed in the same
change. `doctor.check_workspace_ignore` is the one aggregated textual
advisory check; deviations from the proposal: (a) a MISSING `.gitignore`
reports ok ("the studio writes one on the next snapshot") — it self-heals,
so only an existing file with lines removed reports the finding; (b) the
fix text names `git rm -r --cached` for the already-committed case instead
of a tracked-state probe (no git dependency). `obsidian-integration.md`'s
impossible pattern is rewritten, and ATLAS.md carries the snapshot
paragraph as a standing rule with the manual drag named. Full fast tier
green including the browser e2e. Observed, not fixed (pre-existing): a
hand-`init`ed campaigns repo without a commit identity fails snapshots
until identity is set — `_ensure_repo` sets one only on its own init path.
