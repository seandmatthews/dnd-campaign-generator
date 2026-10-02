# 32 — table-companion: hub robustness (watcher, frames, partial mutations)

**Priority:** P2 — separate repo (`table-companion/`), own commits
**Area:** `table-companion/server/hub.py`, `server/app.py`, `server/actions.py`,
`server/content.py`
**Found by:** 2026-10-02 full review P2-9, P3-40, P3-41, P3-43

## Problem

1. **The broadcast error path can kill the timer-watcher task (P2-9,
   suspected).** Two broadcasts can be in flight concurrently (a slow
   phone's `send_text` yields on drain); when a dead conn fails in both,
   the second `self.conns.remove(conn)` raises `ValueError` out of
   `broadcast()` (`hub.py:64-70`). `app.py:140` calls it outside the error
   guard, but the real victim is `_timer_watcher` (`app.py:155-161`) — no
   guard at all — whose task dies, silently stopping server-side alarm
   resolution (DESIGN.md §6's core authority) until a client action
   happens to run `check_timers`. Related: concurrent broadcasts are
   unserialized and the client never checks `state.version` (`net.ts:60-63`
   applies any arriving state).
2. **Non-dict JSON frames or wrong-typed args crash the WS handler
   (P3-40).** `"x"`, `[1]`, `null`, or `{"default": "12a"}` raise
   non-`ActionError` exceptions out of the loop (`app.py:91-95`,
   `actions.py:82`…) — the socket aborts with no error message to the
   client, contrary to the ActionError contract.
3. **Rejected `*_update` actions partially mutate state (P3-41).**
   Fields are mutated before later fields validate (`actions.py:187-198`
   pc_update, npc/loot/timer same); the caller catches `ActionError` and
   continues without commit/broadcast — the sender sees an error toast
   while their earlier fields live unbroadcast in memory until an
   unrelated action commits them under a wrong version bump.
4. **Malformed starter pack kills the GM's connection (P3-43).** Valid
   JSON with a missing `name`/`duration_s` raises `KeyError` through
   `apply_action` (`content.py:43-58`); only FileNotFoundError/
   JSONDecodeError become ActionError — plus entries appended before the
   crash sit uncommitted.

## Proposal

- `self.conns.discard(conn)` (or guard removal); wrap the watcher body in
  try/except with a warning log; serialize broadcasts per room (an
  asyncio.Lock or a single sender task) so full-state ordering is
  guaranteed; optionally stamp and check `state.version` client-side.
- Validate the frame shape (`isinstance(msg, dict)`) and coerce/validate
  args centrally (`_need` raising ActionError on non-int-coercible)
  before any handler runs.
- Make `*_update` handlers validate-all-then-mutate (collect changes,
  apply once) so a rejection leaves state untouched.
- Content packs: validate required keys/types per entry → ActionError
  naming the entry; apply only after full validation.

## Acceptance criteria

- [ ] A deliberately stalled socket + overlapping broadcasts: watcher
      survives, alarms still resolve (test with a fake conn that raises).
- [ ] Malformed frames return an error message to the client, socket
      stays open (test each wrong shape).
- [ ] `pc_update {name, hearts_max: 99}` rejection leaves `name`
      uncommitted and unbroadcast (state-diff test).
- [ ] A pack missing `name` → readable ActionError, no partial apply.
