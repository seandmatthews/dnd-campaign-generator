# 10 — Page writes are byte-preserving (frontmatter surgery, not re-serialization)

**Priority:** P2
**Area:** `studio/places.py` (`_page_text`), `studio/quests.py` (`set_status`),
tests for both

## Problem

Found by the 2026-10-02 post-sweep review. A quest status flip through the
PATCH route or the CLI `set` rewrites the page via `places._page_text`
(`places.py:92`), which:

- drops every frontmatter key whose value is empty — a GM's blank
  `given_by:` vanishes on the first write;
- re-serializes the frontmatter with `yaml.safe_dump`, destroying every
  `#` comment — including the inline ones in quests.py's own documented
  template — and reformatting key order/spacing;
- normalizes CRLF→LF file-wide (full-file git noise on every
  Windows/Obsidian-authored page) and `.strip()`s the body's edges.

Keys and values survive semantically (a round-trip test pins that much),
but quests pages are exactly the hand-edited-with-comments frontmatter
ticket 06 targets: the first status flip mangles the GM's page. Place
pages share the helper (and places.py was rewritten in the same wave, so
this is new behavior there too).

## Proposal

Line surgery for single-scalar changes: when a write changes exactly one
scalar frontmatter key (quests: `status`), edit or insert that one line in
the original text and leave every other byte — comments, blank-valued
keys, line endings, body whitespace — untouched. Preserve the file's
dominant line-ending style. Keep the re-serializing path only for writes
that genuinely restructure the frontmatter, and say so in its docstring.
places.py single-field PATCHes ride the same helper.

## Acceptance criteria

- [x] A status flip on a page with inline comments, a blank-valued key,
      CRLF endings, and body edge-whitespace changes only the status line
      (byte-level test).
      (`test_status_flip_is_line_surgery_not_reserialization`)
- [x] `set` hooked→active→resolved on an Obsidian-authored page leaves
      bytes identical to the same edits made by hand in a text editor.
      (`test_status_flips_round_trip_like_hand_edits`)
- [x] Unknown/extra keys preserved verbatim (the existing round-trip test
      keeps passing).
- [x] places.py single-field writes get the same guarantee; any write that
      must re-serialize says why in a comment and preserves CRLF.
      (`test_scalar_place_patches_are_line_surgery` — scalar
      parent_place_id inserts as one line; the clearing write re-serializes
      with CRLF kept, per _page_text's docstring)

## Implementation notes (2026-10-02)

- `places._set_frontmatter_scalars` is the surgery: pure non-empty scalar
  changes edit or insert single lines (yaml.safe_dump decides the quoting
  so a surgical line parses exactly like a re-serialized one); a trailing
  `# comment` on an edited line rides along. Anything not line-local —
  list/flow/block-collection values, deletions, keys whose current value
  is a collection, a block containing non-key top-level content (a stray
  `{}`), or no frontmatter at all — returns None and the caller falls
  back to `_page_text`.
- `_page_snapshot` now returns the page's dominant newline alongside
  (text, revision): the text stays newline-normalized for parsing, and
  both paths honor the file's own endings — the CRLF hand-edit round-trips
  exactly. Mixed endings fold to the dominant style.
- `_changed_page_text(raw, meta, body, changes, newline)` is the one
  entry point every page write uses (quests status, places link/patch/
  associate, map-note attach): surgery when the change set is pure
  scalars, re-serialization otherwise, with `meta` already carrying the
  change for the fallback.
- The places patch path now tracks `written` (the keys the write touches)
  separately from `changed` (the DM-facing labels): a rename also folds
  the old name into `aliases`, and that list write correctly forces the
  re-serializing path.
- One existing test strengthened: `test_page_snapshot_hashes_the_bytes_
  it_parses` also pins the newline-style contract.
