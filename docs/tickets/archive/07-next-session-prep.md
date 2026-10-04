# 07 — "Next session prep" → one Obsidian file

**Status:** completed — implemented 2026-10-02; code-verified and archived 2026-10-04.
**Priority:** P2, small (explicitly "cheap and easy" per the 2026-10-01
feedback)
**Area:** Knowledge tab, new small `studio/prep.py`, `briefing.build` reuse

## Problem

Prep material exists — DM briefing, active quests, fresh rumors, pending
reveals — but it lives in four places, and the GM copies it by hand into
whatever they bring to the table.

## Proposal

A **Next session prep** button (Knowledge tab, next to the briefing card).
One click writes **one new markdown file into the vault**, with working
links back to everything it quotes:

    campaigns/<name>/lore/prep/<YYYY-MM-DD>-next-session.md

Frontmatter: `kind: prep`, `date`, `covers_weeks: [a, b]`. Body sections,
each item linking back to its source of record:

1. **Where we left off** — the briefing's events for the covered range
   (top of the cap), each linking its chronicle
   (`[week 1 — Bare Is Not Broken](../../simulation/chronicle/0001-….md)`)
   and faction page.
2. **Rumors worth dropping** — the most recent week's rumors (top 3).
3. **Active quests** — ticket 06's briefing section (statuses, overdue
   first).
4. **Waiting to be revealed** — public facts not yet revealed (the reveal
   is the discovery; this is the GM's reminder list, not player material).
5. **Open decisions** — pending proposals/reviews, clearly labeled NOT
   canon, as the briefing already does.

Rules that keep it cheap and safe:

- **Regeneration never overwrites**: same date, new file gets `-2`
  (next-free-name, like pending lore files). The GM deletes what they
  don't want — in Obsidian — and the file is disposable by design.
- Composed **deterministically** from the briefing + quests read model +
  state rumors. No LLM pass, no budget, no confirm gate beyond the normal
  campaign lock; it's a compile, not a generation.
- `GET .../prep/markdown` returns the same content as text (for printing
  without Obsidian).
- The file is a normal vault page: wiki-links resolve because the vault
  root is the campaign folder; relative links reach chronicles outside
  `lore/`.

## Acceptance criteria

- [x] Button writes `lore/prep/<date>-next-session.md`, never overwriting.
- [x] Every event/quest/rumor item carries a working link back to its
      containing doc (chronicle path, entity page, fact source file).
- [x] Deterministic: same canon → byte-identical content (test).
- [x] Empty campaigns produce a valid, honest skeleton (no fabricated
      sections).
- [x] Snapshot committed on write; CLI equivalent
      (`python studio/prep.py <campaign>`).
