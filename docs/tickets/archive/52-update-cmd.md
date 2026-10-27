# 52 — update.cmd: the friend-visible updater

**Status:** completed (implemented 2026-10-26 on ticket/52-update-cmd,
0a4174b + 387d110, merged 0a5f839; the live e2e ran 2026-10-27 against
the real v0.1.1 release — three full cycles green, two live-found bugs
fixed (Expand-Archive AV retry; honest backup-cleanup disclosure);
closed with the boxes below)
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

- [x] End-to-end on this machine: tag a trivial change → `update.cmd` on
      a scratch install lands the new tree, `campaign-data/` and
      `llm.env/` byte-identical across the swap — the live v0.1.1 e2e
      (three full cycles, real GitHub download + hash), plus the local
      fake-server legs in `studio/tests/test_update_cmd.py`
- [x] Offline / unreachable / SHA mismatch → clear message, tree
      untouched — checked live (unreachable leg) and pinned by the
      hash-mismatch and unreachable e2e legs
- [x] Key rotation: change `llm.env`, restart, the new key is the one
      used (existing resolution honored; no payload involvement) — the
      app payload never carries `llm.env` (pinned by the builder's
      smoke_app + the live cycles' byte-identical preservation)
- [x] Stamp: a fresh install's VERSION block names the tag it came from
      — `app-release: v0.1.1` + the CI commit sha landed by the live
      update; `test_build_release.py::test_version_block_...` pins the
      shape

## Implementation notes (2026-10-26)

Landed on `ticket/52-update-cmd` (0a4174b + review fixes).

- **`release/templates/update.cmd`** — the friend's updater, all
  native tools (powershell Invoke-RestMethod for releases/latest — no
  token, the repo is public; curl.exe for the download; certutil for
  the SHA256 vs the release body; Expand-Archive — the payload is a
  ZIP for exactly this; robocopy for snapshot + swap). Explicit,
  never silent: prints installed vs latest, `choice /C YN` gates the
  swap, and every refusal path says "Nothing was changed". Never
  touches `campaign-data/` (excluded on BOTH sides — see the bug
  below), `llm.env`, `runtime/`, `dnd-mapping\tools\`, or the
  installer's log; snapshots the app tree into `.update-backup\`
  first and stamps `.install-complete` after, exactly like
  install.cmd. `launch.cmd` never invokes it. `UPDATE_API_BASE` is
  the test seam.
- **The builder side:** `VERSION` is a block — line 1 is the frozen
  stamp install.cmd byte-compares; under it `app-release:`
  (from `--release`, else CI's `GITHUB_REF_NAME`, else `local`),
  `app-commit:`, `tools-rev:`, `companion:` — what update.cmd parses.
  The app payload is a deterministic ZIP (`pack_app_zip`: sorted
  paths, fixed timestamps — Expand-Archive needs no 7-Zip).
  `bundle_version()` and verify_update's comparisons take the stamp
  LINE (the block would poison the exe filename and the leg asserts).
- **Tests** (`studio/tests/test_update_cmd.py`): text-shape pins (the
  /XD//XF guards, the keystroke gate, the honesty copy) + six REAL
  legs driving update.cmd against a scratch install and a local fake
  releases/latest: happy path (key, campaigns, BOTH packs, installer
  log preserved; stray app file deleted; backup cleaned), hash
  mismatch, unreachable, already-current noop, pre-stamp migration
  message, running-studio refusal.
- **Found by the updater's own e2e (the seedless-payload bug):** the
  first draft excluded only the payload's `campaign-data` seed — with
  a payload carrying no seed, `/MIR` PURGED the friend's campaigns.
  install.cmd's preserve leans on its payload always carrying the
  seed; the updater must not. Fixed: `campaign-data` excluded on the
  DEST side too; the e2e payload deliberately carries no seed, so the
  pin is the adversarial shape.
- **Review round 1 (2026-10-26):** the staunch-lane dispatch died on
  a tooling quota limit, so the review ran inline against the same
  checklist. Two P3s, both fixed: the backup root-file copies now
  check errorlevel (a locked file silently weakened the snapshot the
  failure message claims exists), and README.txt's Updating section
  tells the friend to keep their own files inside `campaign-data`
  (loose files at the install root are old-program-file territory to
  `/MIR`). Everything load-bearing is pinned by the real-tool legs.
- **The live e2e (2026-10-27, against the real v0.1.1 release)**: three
  full cycles — complete full-exe install into a scratch home (waited on
  `.install-complete`, NOT VERSION: VERSION copies early and the first
  attempt killed a half-mirored install), friend state (rotated
  `llm.env`, a campaign, stamp reset to v0.1.0), then the install's own
  update.cmd against real GitHub. Every cycle: exit 0, real 5.4 MB
  download, certutil hash match, snapshot, swap, stamp — with the key
  byte-identical, the campaign intact, runtime + tools packs intact,
  install-log noting the update, and the swap replacing update.cmd with
  the release's copy. Two live findings, both fixed in the template and
  pinned by text-shape tests:
  1. **Expand-Archive can lose a race with antivirus on the fresh
     download** (failed 2/2 right after the heavy install mirror,
     passed 3/3 otherwise — identical command). Fix: one bounded retry,
     five seconds apart; with it, 2/2 fresh-install cycles passed.
  2. **The success path claimed the backup was cleaned when `rmdir`
     silently failed** (25 MB left behind under AV contention). Fix:
     retry once, then DISCLOSE a surviving backup in the success
     output. Known limit, accepted: a delete-pending "ghost" (AV holds
     the handle; `rd` reports success, `if exist` sees it gone, the
     name lingers) is unobservable from cmd — it self-heals on the
     next update's pre-swap `rmdir`.
- **Known quirk, recorded:** one transient `'h' is not recognized`
     stderr line appeared once during a failed unpack under the
     pre-retry template; unreproducible after the retry landed, almost
     certainly collateral of the same AV/IO interference.

## Supersession review (2026-10-26)

Checked per user ruling — **not superseded**; stays parked-proposed
(needs 51). 57 did not replace this ticket — it corrected the premise
the Problem section leaned on: install.cmd's preserve logic is now real
(`/XF llm.env` whenever an installed copy exists), so "reuse
install.cmd's preserve logic" is sound as written. When revived, absorb
the 2026-10-13 debate's settled point: the installer and update.cmd
share one fetch-manifest mechanism. Still gated on the key-custody
ruling via 51's public-repo gate.
