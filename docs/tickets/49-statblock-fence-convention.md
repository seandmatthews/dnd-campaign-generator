# 49 — Statblock fence convention (P1's core half)

**Status:** in-progress (implemented 2026-10-10; the manual
once-with-Atlas verify is pending — no campaign exists on this machine
since the 2026-10-05 data wipe)
**Priority:** P2 — unblocks the companion repo's ICRPG fence templates
**Area:** `studio/spine.py`, `studio/routers/review.py`,
`studio/static/js/review.js`, `verification/check.py`,
`docs/design/atlas-seam.md`, `docs/2026-10-10-next-steps.md`
**Found by:** Atlas seam residency ruling, 2026-10-10
(`docs/2026-10-10-next-steps.md` W2; `docs/design/atlas-seam.md` P1/R3,
§2 #6). Adversarial review 2026-10-10 verified the schema and pin
machinery are safe, and caught the insertion-fidelity trap, the
entry-shape trap, and the producer gap — all folded in below.

## Problem

Entity pages (`lore/entities/*.md`) have nowhere structured for combat
stats. Atlas VTT resolves creatures from note frontmatter or a
` ```statblock ` fence in the page **body**, and the fence path works with
the Fantasy Statblocks plugin *absent* (`linkedCreature.ts`, verified
against atlas-vtt 0.6.0 on 2026-10-09). The fence is an ecosystem
interchange format carried in spine-native markdown — that is why the
convention lives in-core; field *vocabularies* and templates are
pack-side (companion repo), forever.

What the spine has and lacks today:

- **Verified safe:** the findings schema does not enumerate `rule`
  (`verification/schemas/findings.schema.json` types it as a string), so
  a new ladder rule validates; and the base-revision pin is existing
  machinery — `approve_items` merges a pending doc's `base_revisions`
  into the publication base (`review.py:537-538`) and `publish()`
  re-checks them under the lock (`publication.py:419-426`), so an
  in-place write with a pinned revision gets the honest 409 for free.
- **The insertion-fidelity trap:** the obvious reassembly —
  `parse_entity_page` → append to body → `entity_page_text` rewrite —
  destroys hand frontmatter: `entity_page_text` drops empty-valued keys
  (`resides_at: []`), comments, and unknown keys when it re-dumps YAML
  (`spine.py:792-793`), and `parse_entity_page` deliberately does not
  return raw frontmatter (`spine.py:741-744`). That is the exact bug
  class `archive/10-byte-preserving-page-writes.md` and
  `archive/22-page-surgery-completion.md` exist for.
- **The entry-shape trap:** `approve_items` splits chosen entries on
  `page` presence (`review.py:526-527`) — a fence entry carrying a page
  dict is silently *skipped* when the target exists ("the hand page
  wins", `review.py:561-563`); one without it flows into
  `timeline.append` and dumps raw into `lore/timeline.yaml`. And
  `get_review` renders `entry.text` (`review.py:277`) while `review.js`
  shows `item.body` — a wrongly-shaped fence entry is an empty card
  whose Approve button files it into the timeline.
- **The producer gap, stated plainly:** with the draft button deferred
  (below) and hand-authoring happening in Obsidian, *nothing creates*
  `pending-statblocks` files and *no flow runs the ladder over entity
  pages* (`run_check` callers all target pending files:
  `loregen.py:311-316`, `review.py:155-164`). Hand-written fences bypass
  both the insertion path and the check, entirely. The mechanism ships
  dormant, on purpose — recorded here so nobody expects live behavior.
- **The kinds correction, widened:** the seam docs say fences target
  `kind: pc`/`npc` pages — those kinds don't exist. But kinds are
  freeform, not a set: the assembler mints `person`/`place`/`city`/
  `world`/`nation`, passes faction kinds through, stubs use `unknown`
  (`assemble.py`; `review.py:395`), and `parse_entity_page` reads kind
  as an opaque string (`spine.py:734`). The convention *recommends*
  `person` and gates on nothing.

## Proposal

1. **Pending doc:** `lore/pending-statblocks-N.yaml` (via the
   `_next_pending_path` convention, `studio/loregen.py:78-90`) with a
   doc-level `mode: statblocks` (the `mode: missing-pages` precedent,
   `loregen.py:684`); entries carry the target page (entity id or
   filename), the fence text, and the target's current revision for
   `base_revisions`.
2. **Byte-surgical insertion (ruling 2026-10-10).** `approve_items`
   classifies fence entries *before* the page/timeline split. Approval
   performs raw-bytes surgery on the target page: read the bytes, split
   on the frontmatter regex, replace or append only the fence region,
   write exact bytes — **every byte outside the fence region is
   unchanged**, and the fence is written in the page's dominant newline.
   The proven pattern is `places.py` `_page_snapshot` (`:273-288`) with
   `base_revisions={target: revision}` (`:1129-1135`); reuse or promote
   those primitives as the implementer prefers — the behavior is what is
   pinned. Replace the *last* existing fence in the body (appends live
   at the end); a blank line precedes an appended fence; two fence
   entries targeting the same page in one batch refuse the batch naming
   the page (the `_claim_page_target` spirit).
3. **Body-only, always:** `parse_entity_page` stays untouched — a fenced
   page parses exactly as today, fence inside `body`; nothing writes
   fence keys to frontmatter.
4. **Ladder check, shape only:** rule `structural/statblock-fence` in
   `structural_findings` (`verification/check.py:110-148`): fence lines
   parse as `key: value`, numeric values parse as numbers, no empty
   keys/values — finding dicts in the existing shape. Findings, never
   verdicts; vocabulary is the pack's business.
5. **Review surface in scope (ruling 2026-10-10):** `get_review` serves
   the new kind and `review.js` renders it — a statblock pill and the
   fence text as the card body, with working approve/discard. Without
   this, ticket 49 ships an unrenderable card.
6. **Producer honesty (ruling 2026-10-10):** the mechanism ships
   dormant — no in-core producer until the "draft statblock" button or a
   companion pack tool stages entries; hand-authored Obsidian fences are
   inserted by nothing and checked by nothing until then. A vault-side
   ladder pass (doctor over `lore/entities`) is a follow-up if hand
   fences become common — deliberately not this ticket's lane.
7. **Docs:** fence goes *after at least one prose line* (a fence-only
   body makes the entity `note` become fence text — `_first_paragraph`,
   `spine.py:737` — which would then feed loregen prompts); the freeform
   kinds wording amends `design/atlas-seam.md` P1 **and**
   `2026-10-10-next-steps.md` W2 (both still say pc/npc).

**The draft button stays deferred** (ruling, 2026-10-10): the core must
not carry an ICRPG template, so the button waits until the companion pack
ships a template the core can locate. Hand-author fences in Obsidian in
the meantime — the seam spec's own v0 alternative; fences are short.

## Acceptance criteria

- [x] approving a fence pending item inserts the fence into the target
      page's body; re-approve replaces the last fence, never duplicates;
      a blank line precedes it — `studio/tests/test_statblock_fence.py`
      (hand-written pending YAML is the established test pattern,
      `test_review.py:33`)
- [x] byte fidelity: approving on a decorated page (inline comments,
      unknown keys, `resides_at: []`, CRLF) changes no byte outside the
      fence region, and the fence lands in the page's dominant newline —
      same file
- [x] a hand edit to the target page between stage and approve aborts
      with the honest 409 via `base_revisions` — same file
- [x] two fence entries for one page in one batch refuse, naming the page
- [x] `parse_entity_page` on a fenced page: body contains the fence
      verbatim, frontmatter unchanged, no new fields —
      `studio/tests/test_spine.py`
- [x] ladder: well-formed fence → no findings; `hearts: many`
      (non-numeric) and an empty value → `structural/statblock-fence`
      findings — verification test
- [x] Review tab renders the statblock kind (pill + fence text as card
      body) and its approve/discard work — `studio/tests/js/` suite
- [x] (manual, once, with Atlas installed)%%KEEP%% approve one fence on a
      ruravaqan person page → Atlas links a token to the page and renders
      it — the seam M1 verify

## Implementation notes (2026-10-10, updated after the live walkthrough attempt)

Landed with the seam batch (see the boxes above - all pinned tests green,
live approve on a real page). The live Atlas check then ran against the
real `ember-coast` campaign: the fence was approved through the pending
flow into `lore/entities/Sera Vael.md` (fences: 1, prose intact, snapshot
committed), the campaign vault is Atlas-0.7.1-enabled, and the person
page is in the vault tree Atlas watches.

**Remaining for completed (human hands - the automation cannot drive
Atlas's dropdowns/file dialog):** import the site's `.uvtt` (Create maps
dialog is already open in the Atlas window), create a scene, place a
token named "Sera Vael", link it to the note - the fence
(hearts: 4 / effort: d6 / ac: 12) should render on the token with no FS
plugin installed. Everything up to that click is verified; the last click
is the M1 verify.

**Update 2026-10-20:** the scene exists now without any import — the
table-setup card's Publish-site-scene button (tickets 53/54) published
The Drowned Crypt into the `ember-coast` collection with correct walls
(the generated map needed two core exporter fixes that day, recorded
in ticket 54). The remaining human step is unchanged in substance:
open the ember-coast collection in Atlas, place a token named
"Sera Vael", link it to the person page - the fence should render on
the token.
