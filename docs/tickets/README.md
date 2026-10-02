# Tickets

Each ticket carries its evidence, a concrete proposal, and acceptance
criteria. Two batches so far, each from one review of real use.

## Batch 1 — the 2026-10-01 first-GM campaign run (01–08)

A full homebrew campaign (The Greywatch Marches) was built end-to-end
through the app by a GM who had never opened the repo. These tickets fell
out of that run.

| # | ticket | priority |
|---|---|---|
| 01 | [Honest time budget](01-honest-time-budget.md) | trivial, high visibility |
| 02 | [Job queue + auto-accept + wait estimates](02-job-queue.md) | P1 |
| 03 | [Premise-to-sandbox storyline generation](03-storyline-generation.md) | P1 (covers blank-page + ideation) |
| 04 | [Obsidian household onboarding](04-obsidian-household.md) | P2, small |
| 06 | [Quests as first-class entities](06-quests-first-class.md) | P1 (full spec inside) |
| 07 | [Next-session prep file](07-next-session-prep.md) | P2, small |
| 08 | [Rumor → handout pipeline](08-rumor-handout.md) | P3, low |

(No ticket 05 — the original feedback's point 5 is ticket 03's problem
statement.)

## Batch 2 — the 2026-10-02 post-sweep review (09–16)

Batch 1 (plus the arch-fix wave it rode in with) landed in one evening;
a full code review of the landing found one P1, three P2s, and a P3
cluster. These tickets fix all of it, grouped so each is one
lane-sized, independently mergeable piece of work (same shape the sweep
consumed).

| # | ticket | priority |
|---|---|---|
| 09 | [Quests must survive a hand-edit](09-quests-survive-hand-edits.md) | P1 |
| 10 | [Byte-preserving page writes](10-byte-preserving-page-writes.md) | P2 |
| 11 | [Sessions: what you checked is what stages](11-sessions-stage-what-you-checked.md) | P2 |
| 12 | [Engine element-shape guards: finish the job](12-engine-shape-guards-complete.md) | P3 |
| 13 | [Lock & queue honesty polish](13-lock-and-queue-honesty.md) | P3 |
| 14 | [Prep tells the whole truth](14-prep-tells-the-whole-truth.md) | P2 |
| 15 | [Simdesign hardening](15-simdesign-hardening.md) | P3 |
| 16 | [Docs & copy consistency sweep](16-docs-and-copy-consistency.md) | P3 |

## Explicit non-goals (rulings, not backlog)

- **No in-app lore editor. Ruled out 2026-10-01.** The answer to "where do I
  type my villain?" is *open Obsidian* — see ticket 04, which is about
  making that answer discoverable, not about building an editor.
