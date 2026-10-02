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

## Batch 3 — the 2026-10-02 full review + followup (17–34)

A nine-lane full-codebase review at `eff1282` (report:
`docs/reviews/2026-10-02-full-code-review.md` — 1 P1, 9 P2s, 51 P3s) plus a
five-finding followup pass by a second reviewer
(`docs/reviews/2026-10-02-followup.md`, all five independently confirmed in
the report's addendum). All 66 findings are covered below, grouped by
module/fix coherence so each ticket is one lane-sized, independently
mergeable piece — finding IDs are cross-referenced inside every ticket.
Tickets 32–33 live in the separate `table-companion/` repo (own commits).

| # | ticket | priority |
|---|---|---|
| 17 | [Kill CRLF-doubling in the engine-copy seam](17-crlf-canonical-newline-seam.md) | P1 (canon corruption) |
| 18 | [Engine hand-edit tolerance](18-engine-hand-edit-tolerance.md) | P2 |
| 19 | [effects.py batch semantics](19-effects-batch-semantics.md) | P2 |
| 20 | [Replay/resolve honesty](20-replay-resolve-honesty.md) | P3 |
| 21 | [simdesign reply robustness](21-simdesign-reply-robustness.md) | P2, small |
| 22 | [Page surgery completion](22-page-surgery-completion.md) | P2 |
| 23 | [Degradation honesty (quests/prep/briefing)](23-degradation-honesty.md) | P2 |
| 24 | [Submission gates must survive the queue](24-submission-gates-queue.md) | P1/P2 borderline |
| 25 | [Router error mapping & input gates](25-router-error-mapping.md) | P3 |
| 26 | [Lock & flag hygiene II](26-lock-flag-hygiene-2.md) | P3 |
| 27 | [Export/restore trust boundary](27-export-restore-trust.md) | P3 |
| 28 | [Publication & review edge cases](28-publication-review-edges.md) | P3 |
| 29 | [Session-staging UI correctness](29-session-staging-ui.md) | P2 |
| 30 | [Frontend failure surfaces & dead affordances](30-frontend-failure-surfaces.md) | P2 |
| 31 | [dnd-mapping polish](31-mapping-polish.md) | P2 |
| 32 | [table-companion hub robustness](32-table-companion-hub-robustness.md) | P2 (separate repo) |
| 33 | [table-companion join/reject + client types](33-table-companion-join-client.md) | P3 (separate repo) |
| 34 | [CI, release & repo hygiene](34-ci-release-hygiene.md) | P3 |

Suggested order: 17 first (silent canon corruption), then 22/24 (the two
silent-wrong-write and gate classes), then the P2 tail, P3s last. Ticket 20
and 24 each contain one explicit GM ruling (resolve overextension semantics;
same-campaign engine queuing vs documented 409).

## Explicit non-goals (rulings, not backlog)

- **No in-app lore editor. Ruled out 2026-10-01.** The answer to "where do I
  type my villain?" is *open Obsidian* — see ticket 04, which is about
  making that answer discoverable, not about building an editor.
