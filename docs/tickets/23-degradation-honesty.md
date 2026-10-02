# 23 — Degradation honesty: quests/prep/briefing survive broken inputs

**Priority:** P2
**Area:** `studio/prep.py`, `studio/briefing.py`, `studio/quests.py`,
`studio/spine.py` (frontmatter regex)
**Found by:** 2026-10-02 full review P3-17, P3-18, P3-19, P3-23 + followup R5

## Problem

Ticket 09 made quests degrade per-page, and ticket 14 made prep honest —
but four degradation gaps remain where one bad input kills more than itself:

1. **Prep drops quest findings (R5, trace).** `prep.py:141-144` keeps only
   `error` from the briefing's quest section and discards `findings`; the
   markdown then prints "No quest pages yet" when the ONLY quest page is
   unreadable — while the briefing renders the same findings
   (`briefing.py:228-231`). The durable session material silently loses the
   warning ticket 09 added.
2. **A hand-mangled `simulation/knowledge.yaml` kills the whole briefing
   and prep (P3-17).** `briefing.py:113` / `prep.py:59` read it unguarded
   (`spine._safe_load` raises `CampaignFileError`) — events, rumors,
   decisions, quests all lost from the response, while quests/pages
   deliberately degrade per-file.
3. **A hand-broken `simulation/state.yaml` kills the quests read model
   (P3-18).** `quests.py:230` `load()` needs state only for the
   clock/faction resolution; a YAML slip there 422s the whole Quests panel
   instead of degrading with unknown-clock findings.
4. **UTF-8 BOM makes frontmatter invisible (P3-19).** `_FRONTMATTER_RE` is
   `\A---` (`spine.py:554`) — a BOM'd page parses as frontmatter-less, so
   the quest/place page silently loses `kind` with NO finding (worse than
   the unreadable case: it vanishes silently from the read model).
5. **`prep.covers_weeks` prints an inverted range (P3-23).** `prep.py:68`:
   `since_week=10` on a week-3 clock yields "covers weeks 11–3"; the route
   only bounds `ge=0`.

## Proposal

- `prep._quests` carries `findings` and the markdown renders error-level
  findings; distinguish "no quests yet" from "quests exist but N pages
  couldn't be read".
- Guard the knowledge read in briefing/prep the way quests pages are
  guarded: catch `CampaignFileError`, degrade the section, surface a
  finding naming the file.
- `quests.load`: tolerate an unparseable state.yaml (unknown clock →
  findings; timers marked unknown) instead of raising.
- Strip a leading BOM before the frontmatter match (and emit a finding so
  the GM learns why the page was invisible).
- Clamp `since_week` to the clock (or 422 on > clock — pick, document).

## Acceptance criteria

- [x] Broken sole quest page: prep page names the unreadable file, briefing
      shows findings, neither says "No quest pages yet".
- [x] Broken knowledge.yaml: briefing returns its other sections plus a
      knowledge-section finding; prep builds.
- [x] Broken state.yaml: Quests panel renders quests with unknown-clock
      findings instead of a 422 error panel.
- [x] A BOM'd `kind: quest` page appears in the quests read model (or is
      surfaced as a finding) — never silently absent.
- [x] `since_week` above the clock: sane output (clamped or 422), tested.
