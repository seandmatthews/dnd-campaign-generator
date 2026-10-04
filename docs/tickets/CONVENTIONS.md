# Ticket conventions

How tickets in this directory work — for whoever writes one (usually an
agent). The copy-paste template is at the bottom; the rules come first
because they are the part that isn't obvious from the skeleton.

## What a ticket is

A ticket is the durable record of one finding from real use. Reviews of
real use (a first-GM campaign run, a code review, a GUI QA pass, a user
request) are converted into tickets, and then the review documents
themselves are deleted — the tickets are the record. Two consequences:

- A ticket must be **self-contained**: it carries its own evidence and
  must never dangle a pointer at a deleted review doc.
- Completed tickets are **never deleted**. `archive/` is the history;
  only a ticket's status line and location change over its life.

## Rules

- **Numbering:** sequential two-digit number (`36-…`), never reused;
  gaps are fine. The number is the cross-reference id — later tickets
  cite each other by number.
- **One ticket = one lane:** sized so one agent can implement and merge
  it independently in one sitting. Group findings by module/fix
  coherence, not by the order the review found them.
- **Acceptance criteria are test-nameable.** Each criterion names the
  test that pins it (or is a directly checkable claim). "Works better"
  is not a criterion.
- **User rulings live in the ticket**, under `## Ruling (do not revisit)`
  or `## Decisions baked in`. A ruled-out approach is not re-proposed in
  a later ticket (e.g. the no-in-app-lore-editor ruling, recorded in
  `archive/04-obsidian-household.md`).
- **Deviations are documented, not silent.** When an implementation lands
  differently than proposed, the ticket's Implementation notes say so and
  why (see `archive/08-rumor-handout.md`, amended by ticket 16).
- **Status lifecycle:** `proposed` → `in-progress` → `completed`,
  `superseded`, or `abandoned`. "Parked" is not a status — write
  `proposed` with a parked note. Flipping to one of the last three means
  the file moves to `archive/` in the same change.
- **No index.** There is deliberately no README or table listing the
  tickets; the status line in each file is the record.

## Template

Copy for a new ticket. The bold fields: `Status` (always, exactly one of
the five statuses), `Priority` (P1/P2/P3 or a short phrase), `Area`
(files/modules the work touches), `Found by` (what surfaced it, with
date). The three spine sections are Problem / Proposal / Acceptance
criteria; Implementation notes are added when the work lands, not up
front.

```markdown
# NN — Title in a few words

**Status:** proposed
**Priority:** P1 | P2 | P3 (or a short phrase)
**Area:** `files/modules/` the work touches
**Found by:** what surfaced it + date

## Problem

What is wrong today, with evidence: observed behavior, file:line,
reproduction. Ground it — this section must still make sense after the
review that produced it is deleted.

## Proposal

The concrete change. Enough detail that an agent can implement it
without re-deriving the design; call out anything that needs a user
ruling rather than deciding it silently.

## Acceptance criteria

- [ ] criterion — pinned by `studio/tests/test_foo.py::test_bar`
- [ ] criterion — pinned by `studio/tests/js/test_baz.cjs`

## Implementation notes (YYYY-MM-DD)

How it actually landed: commit refs, deliberate deviations from the
proposal, follow-ups. Written when the status flips.
```

Good worked examples in `archive/`: `02-job-queue.md` (a P1 with
implementation notes), `20-replay-resolve-honesty.md` (a ticket carrying
a user ruling), `08-rumor-handout.md` (amended by a later ticket).
