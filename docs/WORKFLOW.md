# Workflow — implement, verify, review, merge

The loop one ticket goes through from `proposed` to `completed`. The
ticket's format lives in `docs/tickets/CONVENTIONS.md`; repo-wide rules
are in `AGENTS.md`. This file records the part those don't: what "done"
means, how verification actually runs, and the rules a review and a
merge obey.

## Definition of done (for a ticket)

- Every acceptance criterion is demonstrably true, and each box is
  ticked only after it was verified. The criteria name the tests that
  pin them — new behavior lands with its pinning tests in the same
  change. A change with no test for a named criterion is not done.
- `python run-tests.py` (fast tier) is green for the WHOLE repo, not
  just the touched project — the studio invokes the other engines, so a
  dnd-mapping change can break studio tests.
- `python run-tests.py --all` when the change touches an engine seam, an
  importer, export/restore, or anything else the integration tier
  covers.
- The ticket's Implementation notes are written: commit refs, and every
  deviation from the proposal with the reason. Silent deviations are
  how drift compounds.
- The status is flipped and the file moved to `docs/tickets/archive/`
  in the same change (CONVENTIONS.md).
- If behavior or copy drifted from any doc, the doc is updated in the
  same change. `studio/API.md` is a contract — code and doc move
  together, never one ahead of the other.

## Running the tests

    python run-tests.py          # fast tier — seconds
    python run-tests.py --all    # + integration tier — minutes

**Fast tier:** the studio pytest suite with every engine faked, the
agentic-simulator pytest suite, the verification ladder, the FMG
importer on synthetic fixtures, the two dnd-mapping direct-run scripts,
and the plain-node JS harness suites (auto-skipped with a note when
node is absent). Stdlib-only runner; every engine invocation is
list-form args with cwd set — nothing ever goes through a shell
(a rule kept from when the repo path contained `&`).

**Integration tier:** real engine CLIs, real bytes, temp dirs
(`studio/tests` marked `integration`; pytest.ini excludes it by
default). It needs git-ignored prerequisites — the ruravaqan campaign
state and the `dnd-mapping/tools/` FMG + settlemaker builds — and is
skipped with a note, not failed, when they are missing.

Traps and per-project notes:

- The `dnd-mapping` and `verification` suites are zero-dependency
  DIRECT-RUN scripts (`python dnd-mapping/tests/test_import_fmg.py`),
  not pytest collections. Pointing pytest at them produces bogus
  tmp-fixture failures — go through run-tests.py, or run the file
  directly.
- JS tests pin logic and message shapes, not layout. A frontend change
  also gets one manual click-through of the changed flow: boot the
  studio (`STUDIO_PORT=8766` to avoid a running instance), do the thing,
  look at it. Python edits need a server restart to take effect —
  static JS reloads, Python does not.
- Lint: `ruff check agentic-simulator/src agentic-simulator/tests studio
  dnd-mapping/src run-tests.py` — the same targets CI enforces.
- CI (`.github/workflows/ci.yml`) runs the fast tier on ubuntu +
  windows, py3.10 + 3.11, pinned installs. The Windows leg is not
  optional: this is a Windows-first app (junctions, MAX_PATH handling,
  WinError-32 log rotation are all Windows code paths).

## Review rules

- The reviewer reports findings; it does not fix them in the same pass.
- Every finding gets one of: a ticket, or an explicit already-fixed /
  won't-fix disposition. Findings are never silently dropped — and a
  review whose findings all turn out to be already fixed is deleted
  without tickets.
- After conversion, the review document itself is deleted — the tickets
  are the record (2026-10-02 ruling). This is why a ticket must be
  self-contained.
- Severity, used by tickets and reviews alike:
  - **P1 — silent wrongness.** Corrupts canon/data or bypasses a
    guardrail with nothing the user can see: corruption, lost work, a
    silent gate bypass, a write that lands somewhere wrong.
  - **P2 — loud wrongness or dishonesty.** The user can see or hit it:
    wrong output, a missing disclosure, a readable crash, a robustness
    hole on hostile input.
  - **P3 — polish.** Consistency, docs and copy, minor robustness,
    hygiene.
- The standing review lens — what past reviews actually caught, so
  start there: malformed and hostile input shapes, encoding/CRLF seams,
  lock and flag interleavings (TOCTOU), crash windows mid-write,
  frontend affordances that fail silently, and honesty gaps between
  what the UI claims and what the state shows.

## Merge discipline

- One lane (branch or worktree) per ticket, independent and mergeable on
  its own — tickets are sized for this by CONVENTIONS.md.
- Merge only with the fast tier green on the lane; add the integration
  tier when the lane touches engine seams.
- A red merge is reverted immediately and re-landed fixed — revert
  first, investigate after. Canon safety outranks momentum.
- No ride-along changes. If something rides along anyway, it is named
  in the commit message.
- The commit that lands the work carries the ticket's status flip and
  archive move with it.
