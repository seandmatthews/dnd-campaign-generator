# 14 — Prep tells the whole truth (decisions past the cap, determinism, bounds)

**Priority:** P2
**Area:** `studio/prep.py`, `studio/briefing.py`, `studio/routers/knowledge.py`,
`studio/quests.py` (today plumbing), `studio/tests/test_prep.py`

## Problem

Found by the 2026-10-02 post-sweep review — three honesty gaps in the one
artifact whose job is "what the GM brings to the table":

1. **False "Nothing awaiting a decision."** prep's decisions section reads
   only the briefing's capped slice (`BRIEFING_CAP = 10`,
   `briefing.py:127`; `prep.py:165` filters `b["items"]`), and the
   briefing's sort puts week-`None` items — stale facts and pending
   reviews — last. A campaign with ≥10 week-bearing items has every
   decision item cut from the slice, and prep prints "_Nothing awaiting a
   decision — no pending proposals, reviews, or stale facts._" while a
   pending review or stale fact demonstrably exists. The briefing's own
   renderer prints "— showing N, M more not shown", and `b["omitted"]` is
   available to prep but unused — a silent truncation in violation of
   briefing.py's own docstring rule.
2. **The determinism claim leaks the real clock.** prep.build's `today`
   fixes only the doc date; it never reaches the quests overdue check,
   which uses `datetime.date.today()` (`quests.py:227`, and
   `briefing.py:148` → `quests.py:378`). Regenerating yesterday's file
   across a midnight where a quest `due:` date flips produces different
   bytes than yesterday's run — "same canon + same date → same bytes"
   holds only within a day. (The existing determinism test passes because
   both runs execute on the same real day.)
3. **Unbounded inputs.** `?since_week=-5` renders `covers_weeks: [-4, 3]`
   and prose "covers weeks -4–3" (`prep.py:65`); `FactBody.week` accepts
   0/negatives (the form's `min="1"` is advisory, `knowledge.py:172`
   stores it verbatim); `apply_fact_records` takes `week` untyped from
   record dicts (`knowledge.py:138`) — a future non-numeric caller is a
   500.

## Proposal

1. Decisions read past the cap: either briefing exposes its decisions
   uncapped (a `decisions` key derived from the full item list, separate
   from the capped `items`), or prep re-derives them from the same sources
   — and always prints the "N more not shown" count when anything is cut.
   Never a false empty.
2. Plumb `today` from `prep.build` through `briefing.build` into
   `quests.briefing_section`, so a frozen date is frozen everywhere.
3. Validate at the route boundary: `since_week` clamped to ≥0 (or 422),
   `week` ≥1 or omitted (422), `apply_fact_records` coerces/validates like
   the route does.

## Acceptance criteria

- [x] A campaign with ≥10 week-bearing items plus a pending review/stale
      fact shows that decision in the prep file (or an explicit "N more
      not shown") — never a false empty. Test pins it.
      (`test_decisions_read_past_the_briefing_cap` — the decision rides
      the new uncapped `decisions` key AND the events section prints the
      omitted count)
- [x] The byte-identical regeneration test passes with a frozen date that
      crosses a quest due-date boundary.
      (`test_frozen_today_pins_quest_overdue_across_midnight` — frozen
      2026-10-01 reproduces its bytes and reads not-overdue; a caller
      date past the due date reads overdue, proving `today` flows through
      rather than both builds using the wall clock)
- [x] `since_week < 0` and `week < 1` are refused or clamped, pinned by
      tests; existing prep/knowledge suites green.
      (`test_since_week_and_fact_week_bounds` — Query(ge=0) on all four
      briefing/prep routes, a FactBody validator, and the same gate in
      `apply_fact_records`; studio tier 506 passed)

## Implementation notes (2026-10-02)

- `briefing.build` gained `today=None` (plumbed to
  `quests.briefing_section`, whose parameter already existed) and a
  `decisions` key: the pending/needs_attention items captured from the
  FULL list, before the cap. `render_markdown` is unchanged — its own
  capped view keeps its honest "N more not shown" line.
- prep's events section states what the briefing cap omitted
  (`events_omitted`), so the page never lies by omission in either
  direction: everything that qualifies is either shown or counted.
- Week semantics: a campaign week is `>= 1` (week 0 exists only as the
  engine's founding week, which no provenance record cites); the route
  validator and `apply_fact_records` raise the same readable error.
