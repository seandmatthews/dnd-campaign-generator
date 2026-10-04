# 22 — Page surgery completion: multiline scalars, comments, CRLF, revisions

**Status:** completed — commit 10c6c54 (2026-10-02); code-verified and archived 2026-10-04.
**Priority:** P2 (two silent wrong-writes)
**Area:** `studio/places.py` (`_set_frontmatter_scalars` and friends)
**Found by:** 2026-10-02 full review P3-20, P3-21, P3-31 + followup R3, R4
(all five confirmed; R3/R4 reproduced against the real function)

## Problem

Ticket 10's line surgery is correct for the common cases but has five
remaining holes in `_set_frontmatter_scalars` / `_changed_page_text` /
`_page_snapshot`:

1. **Multiline quoted scalar → silent wrong-write (R3, repro).**
   `status: "hooked\n  "` (valid YAML, parses `hooked `) — the value check
   only recognizes `[ { | >` openers, so surgery rewrites the first line
   and leaves the dangling `"` continuation:
   `status: active` + `  "` → parses as `active "` → the read model degrades
   unknown statuses to `hooked` (`quests.py:19`). The API reports
   `state: published, status: active`, but the change silently doesn't take.
2. **Standalone comment → lossy rebuild (R4, repro).** A `#` comment line
   fails `_FM_KEY_RE` and routes to `_page_text`, whose rebuild "by nature
   drops empty-valued keys and comments" (its own docstring) and strips
   body edge whitespace. A routine status flip on a page with
   `# Keep the timer secret.` + `given_by:` + trailing spaces loses all
   three. (The rebuild's losses are documented — the defect is routing a
   benign comment there when surgery could safely keep it.)
3. **"Dominant newline" is any-CRLF (P3-20, repro).** `_page_snapshot`
   (`places.py:229`) picks CRLF if `\r\n` appears ANYWHERE, so an LF page
   with one pasted CRLF line is wholesale converted by the next surgical
   write.
4. **Comment heuristic eats values containing " #" (P3-21, repro).**
   `rest.find(" #")` treats a `#` inside the current value as an inline
   comment and re-appends it after the new value: `name: "Shop #5"` →
   `name: New Name #5"`. `preferred_map` paths are user-shaped.
5. **places.associate(map) uses the note's revision for params.yaml
   (P3-31, trace).** `places.py:1400-1403` applies `expected_revision`
   (the companion NOTE's revision) to `params.yaml`, whose hash can never
   equal it — any API caller supplying it gets a guaranteed false 409.
   Latent (no current caller sends it) but the docstring invites it.

## Proposal

- Multi-part scalar-span detection: if the changed key's current value
  starts with a quote/`[`/`{` and the line does not close it before
  `fm_end`, that's not line-local — rebuild (or better: refuse with a
  finding, since rebuild also loses the multiline value's shape).
- Comments are safe unchanged lines: let `#`-prefixed top-level lines pass
  the pre-scan (skip them like blank lines) instead of forcing rebuild.
- Newline choice by count, not presence (`data.count(b"\r\n")` vs
  `data.count(b"\n") - that`).
- Only treat ` #` as a comment when it is one (outside quotes of the
  current value) — or stop preserving inline comments on rewritten lines
  and say so; pick a rule and pin it.
- `places.associate`: capture params.yaml's own hash as its base revision
  (absent = create-only), never the note's.

## Acceptance criteria

- [x] R3 repro page: status flip either applies and re-parses to `active`
      byte-verified, or refuses with a finding — never writes `active "`.
- [x] R4 repro page: comment, blank-valued `given_by:`, and body trailing
      whitespace all survive a scalar change byte-for-byte.
- [x] Mixed-endings page: unchanged lines keep their original endings
      (count-based dominance test).
- [x] `name: "Shop #5"` → rewritten line parses to exactly the new value.
- [x] associate with `expected_revision` on a map with params.yaml: no
      false 409; stale params revision still 409s (test).
