# 50 — Companion-note embed-line convention

**Status:** completed (2026-10-10)
**Priority:** P3 — must land before the companion scene publisher writes its
first line (sequencing, not urgency)
**Area:** `docs/design/obsidian-integration.md`, `docs/design/atlas-seam.md`
**Found by:** Atlas seam residency ruling, 2026-10-10
(`docs/2026-10-10-next-steps.md` W5; `docs/design/atlas-seam.md` P2/§2 #7).
Adversarial review 2026-10-10 corrected the gap claim, the invariant
wording, and the self-heal story; all three reworked below.

## Problem

The Atlas-scene line's *existence* is already recorded — `atlas-seam.md`
mentions it four times (§2 row 7 `:95`, P2 `:157-160`, M2 `:228`) and the
companion README already carries the write semantics ("append-only and
idempotent", `dcg-avtt-integration/README.md:58-64`). The real gap is
**core-side silence**: neither the note-format contract
(`obsidian-integration.md:177-182`) nor `_companion_note`'s docstring
(`studio/routers/maps.py:308-310`, "hand edits do not survive") tells a
generator author that a foreign publisher line may exist or who owns it.
That silence is what licenses a silent clobber — and the one in-core
generator *does* rewrite notes wholesale on map re-import
(`maps.py:307-318`), so the first draft's absolute ("generators never
clobber foreign lines") was violated by existing code on day one.

The first draft's "self-heals" was also wrong in the scenario it exists
for: under refuse-on-diff, republishing an unchanged scene refuses
*before* any append fires, so a line lost to a re-import would never come
back unless the publisher treats the note repair as independent of
scene-file diffs.

## Proposal

Doc-only, no code — the original ruling stands. The invariant is written
as an **ownership rule over bytes** so it is true the day it lands:

1. **obsidian-integration §4**, one paragraph beside the format contract:
   a companion note may carry appended, publisher-owned lines. Rules:
   appended at the **end of the note body**; **one self-identifying line
   per publisher** (recognizable without parsing the rest of the note; a
   publisher deduplicates only its own line — so the scene publisher and
   the parked party publisher coexist); appends **preserve the note's
   existing line-ending style** (notes are CRLF on this codebase,
   `maps.py:317`).
   - Body-preserving writers must leave publisher lines untouched — the
     attach-to-place path already complies (`places.py:1444-1460` edits
     frontmatter only, body byte-for-byte).
   - `_companion_note` is **grandfathered by name**: a documented
     whole-note regenerator whose rewrites on map re-import are accepted,
     because the line is publisher-owned and re-appended on the next
     publish.
   - Any *new* note writer must either preserve foreign lines or document
     its healing story.
   - The **heal is required publisher behavior**: the publisher checks
     for its line on every `scenes` run for a site — *including a
     refuse-on-diff no-op, which still repairs the note* — and re-appends
     when missing. The companion README must pin this before its
     publisher ships; this ticket is the deadline for the convention
     (seam sequencing).
2. **design/atlas-seam.md:** amend P2's existing embed-line sentence in
   place (`:157-160`) with the append-only/idempotent semantics and the
   §4 cross-ref — no fourth scattered restatement. The consumer table
   (`:171-175`) gains an Atlas row (site PNG + `.uvtt` → Atlas scene;
   embed line appended on publish), since "each artifact has a natural
   consumer" is how §4 teaches a generator author to look.

## Acceptance criteria

- [x] §4 carries the ownership rule including the named
      `_companion_note` exception and the heal requirement
      (`grep -i "re-import" and "re-append"` both land in the §4
      paragraph)
- [x] §4's consumer table has an Atlas row
- [x] `design/atlas-seam.md` P2's embed-line sentence carries the
      semantics and cross-references §4
- [x] the precision sentences are present and greppable ("end of the
      note body", "self-identifying", "line-ending")

## Implementation notes (2026-10-10)

Landed with the seam batch, per the ruling: obsidian-integration §4
carries the byte-ownership rule (the `_companion_note` grandfather, the
required publisher heal incl. refuse-on-diff no-ops, self-identifying
end-of-body lines, line-ending preservation), and its consumer table
gained the Atlas row; `design/atlas-seam.md` P2's embed-line sentence was
amended IN PLACE (no fourth scattered mention) with the §4 cross-ref.
All four grep criteria verified. No code touched — doc-only as ruled.
