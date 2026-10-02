# 26 — Lock & flag hygiene II

**Priority:** P3
**Area:** `studio/routers/lore.py`, `studio/engines.py`, `studio/routers/simulation.py`,
`studio/publication.py`, `agentic-simulator/src/store.py`
**Found by:** 2026-10-02 full review P3-11, P3-12, P3-29, P3-30
(ticket 13's sequel — the lanes it left)

## Problem

1. **Lore style writes bypass the campaign lock (P3-12).** `put_style` /
   `post_style` (`routers/lore.py:197-235`) are the only interactive canon
   writers without `campaign_lock_nowait`: no 409 during a week job, and
   the racing `canon.snapshot` can commit a half-published state under the
   wrong message (atomic writes keep it corruption-free; contract + history
   noise only).
2. **Week-run flag lifecycle (P3-29).** The flag is written BEFORE
   `engine_lock_logged` (`simulation.py:500`, `:1099`); if the lock wait
   raises its dead-man error, `run_week_proposal`'s `finally` never runs →
   the flag dangles and `GET status` reports `week_run_in_progress: true`
   until reboot. Also `clear_week_flag`'s read→unlink is non-atomic (a
   concurrent write can delete the other run's live flag) and a queued
   second campaign's job overwrites the attribution while the first runs.
3. **No cross-process exclusion on the shared engine home (P3-29's
   engine-home gap + P3-11).**
   `ENGINE_LOCK` is threading-only. A standalone `tick.py --live` or
   `effects.py --apply` (explicitly supported, `session_notes.py:27`)
   racing a studio week interleaves `world/state.yaml` last-writer-wins,
   can leak a foreign chronicle into a proposal's `new_files` diff, and
   shares the engine's fixed `state.yaml.tmp` name (`store.py:294-308`) —
   two writers, one tmp → one `os.replace` can land a half-written file.
4. **Boot-time `recover_campaign` takes no campaign lock (P3-30, latent).**
   Lifespan recovery rolls transactions forward lock-free; only reachable
   with two server processes on one campaigns root (single-port bind
   precludes it today) — document or take the lock.

## Proposal

- Style writes take `campaign_lock_nowait` like every other short writer.
- Write the week flag AFTER acquiring the engine lock (or inside
  `run_week_proposal` so its `finally` always covers it); make clearing
  atomic (write-clear or compare-name-then-unlink under a small module
  lock); include the campaign name in the flag (already) and have boot
  restore only when attribution matches.
- Engine-home cross-process: a lock file in the engine home (probe like
  spine's campaign lock) taken by the studio's engine jobs AND the
  engine's `--live`/apply mains, or at minimum per-writer unique tmp names
  in `save_state` (`mkstemp` pattern already used studio-side) plus a
  documented "don't run standalone during studio weeks" rule surfaced in
  the standalone CLI's startup.
- `recover_campaign`: take the campaign lock (cheap at boot) or document
  why not.

## Acceptance criteria

- [x] Style PUT during a held campaign lock → 409 like its siblings
      (test).
- [x] Engine-lock deadline firing mid-queue leaves no dangling flag;
      `GET status` honest afterwards (test with the lock held > deadline).
- [x] Standalone `--live` while a studio week holds the home: refused with
      a readable message (new lock file), OR save_state uses unique tmp
      names and the constraint is documented in both CLIs.
- [x] Recovery-under-lock (or documented exemption) decided and noted in
      `publication.recover_campaign`'s docstring.
