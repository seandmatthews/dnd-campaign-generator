# 46 — ATLAS.md: the at-the-table tutorial

**Status:** completed (implemented 2026-10-10; the walkthrough's
app-side prerequisites were all satisfied by 2026-10-24 — ember-coast
with a site + simulation, the Drowned Crypt republished as
`the-drowned-crypt-v2` under the ticket-59 exporter. Closed 2026-10-26
per user ruling: the one unchecked box — the Atlas-side clicks of
ATLAS.md — is retired, not satisfied; see the close-out below)
**Priority:** P2 — one of the two seam items the table needs this week
**Area:** `docs/for-new-gms/`, `README.md`, `docs/design/atlas-seam.md`,
`release/build_release.py`
**Found by:** Atlas seam residency ruling, 2026-10-10
(`docs/2026-10-10-next-steps.md` W3; `docs/design/atlas-seam.md` M0/P3).
Adversarial review 2026-10-10 caught two factual errors in the first draft
(vault-registration scope, session-note path) — corrected in place below;
the corrections are the reason two steps changed.

## Problem

Atlas VTT is the studio's at-the-table surface (the seam ruling; the icrpg
table companion is frozen at v0 in its favor), and nothing in
`docs/for-new-gms/` mentions it. `TUTORIAL.md` covers generation → review
and stops at the vault; a new GM has no guided path from "lore approved" to
"a map with walls and lights on the screen at the table".

The full loop already works with zero product code — verified against
Atlas VTT 0.6.0 source on 2026-10-09 (`design/atlas-seam.md` §2) — but two
facts a tutorial writer would get wrong from the seam spec alone:

1. **The vault Atlas needs is the campaign ROOT, and nothing registers it.**
   The studio's one-click registration puts the campaign's `lore/`
   subfolder in Obsidian's vault list (`routers/lore.py:147` hardcodes
   `require_campaign(name) / "lore"`; `obsidian_vault.py:18-22` says so in
   its charter) — a smaller vault for the lore deep-link. Opening *that*
   as the Atlas vault puts `atlas-vtt/` under `lore/` and breaks the seam.
   The campaign-root vault is opened manually.
2. **The session-note layout in the seam spec does not exist.**
   `design/atlas-seam.md` P3 says the DM writes
   `sessions/<date>-<title>/notes/0001.md`; real session records are
   studio-minted `sessions/session-<hex>/` (`sessions.py:87`), created BY
   `session_notes.py import --notes-file <any vault file>`, which copies
   the note immutably — later edits are appended revisions and never touch
   the pinned file. A tutorial per the spec text would plant a stray
   directory nothing consumes.

## Proposal

Write `docs/for-new-gms/ATLAS.md` — the campaign-running tutorial for the
Atlas loop, in the house style of `TUTORIAL.md`: a "you should see"
checkpoint at every step and each long step's expected duration stated
before it runs. The loop, corrected:

1. **Install Atlas** (community plugin; Obsidian desktop ≥ 1.8.7).
2. **Open the campaign folder as the vault** — Obsidian → "Open folder as
   vault" → `campaign-data/campaigns/<name>/`. Say plainly that the World
   Building tab's "Authoring lore" card ("Register this folder as a
   vault") registers the `lore/` subfolder only — the lore deep-link's
   smaller vault, not the Atlas vault.
3. **Apply a system preset.** ATLAS.md carries a complete, copy-pasteable
   example ICRPG preset inline, clearly labeled as house-rule values the
   DM edits; the companion pack is named as the future maintained home and
   never linked as live. (Nothing in either repo can validate a preset
   against Atlas's schema — it lives in Atlas's TS types — so the
   "preset appears live in Atlas" checkpoint is the validation.)
4. **Get a battlemap in:** every generated *site* has
   `maps/sites/<slug>/<slug>.uvtt` beside its PNG and companion note
   (cities get none — sites only). Drag by hand today.
5. **GM view vs player view** on the second screen.
6. **Play — pin a session note to the scene** (any vault file, edited in
   Atlas's floating panel). Be honest about what import does: it copies.
7. **Bring it home:** `session_notes.py import --notes-file <the pinned
   note>` → `extract` (metered LLM call, needs the key) → `prepare` →
   `accept` — the TUTORIAL's existing session pipeline, which requires the
   campaign's simulation to exist (`simulation/state.yaml`; TUTORIAL step
   5 provides it).

Plus:

- a one-line pointer from the main README's "Start here" section
  (`README.md:14`), next to the existing tutorial link;
- correct the session-note path in `design/atlas-seam.md` P3 (drop the
  `<date>-<title>` layout) in the same change — the spec should not keep
  teaching it;
- **one deliberate code touch, owned:** `release/build_release.py:159-163`
  stages exactly one file from `for-new-gms/` (TUTORIAL.md) — add the one
  `copy2` line so installed copies (the friend-handoff audience) carry
  ATLAS.md. No new route; the in-app help modal keeps linking `/tutorial`
  only. This is the ticket's single deviation from "zero code";
- ticket 47 **follows** this one (it adds the snapshot-policy paragraph to
  this page) — one lane each, 46 first.

## Ruling (do not revisit)

- Step 2 is the manual open-as-vault on the campaign root, documented
  accurately beside the one-click card's real scope. No campaign-root
  registration feature unless a later ticket proposes one (it would break
  the zero-code claim).
- The example ICRPG preset lives inline in ATLAS.md as doc content —
  house-rule values, DM-editable. It does not wait for the companion pack,
  and this repo does not invent "official" values.

## Acceptance criteria

- [x] `docs/for-new-gms/ATLAS.md` exists and walks the corrected loop;
      every path, command, and endpoint it presents as working exists
      today; UI names match the real labels (World Building tab,
      "Authoring lore" card)
- [x] README "Start here" links it
- [x] `design/atlas-seam.md` P3 no longer names the `<date>-<title>` layout
- [x] `release/build_release.py` stages ATLAS.md, and a fresh build's smoke
      gate finds it staged
- [ ] followed start-to-finish on this machine against a real campaign,
      changing zero product code beyond the staging line — with the "you
      should see" checkpoints verified at each step

## Implementation notes (2026-10-10, updated after the live walkthrough attempt)

Landed with the seam batch: ATLAS.md, README pointer, seam-spec P3 path
fix, build staging (boxes 1-4 above). The live walkthrough then ran on a
REAL campaign (`ember-coast`, built through the studio API: world 11
nations, battlemap site `the-drowned-crypt` with its `.uvtt`, 3-faction
simulation, hand-authored person page):

- **Verified live:** Obsidian 1.8.10/1.13.7 installed; the campaign folder
  opened as the vault through the REAL Open-folder-as-vault flow; Atlas
  VTT 0.7.1 installed and enabled in the vault (Settings UI confirmed);
  the example ICRPG preset staged at
  `atlas-vtt/system-presets/icrpg-house.json` and its every field verified
  present in the INSTALLED 0.7.1 parser (unitType/dice/initiative/
  resources/widgets incl. clock segments/lightPresets); the session loop
  ran END-TO-END: pinned table note -> import -> metered extract (8
  suggestions, 1 honestly blocked) -> prepare (the blocked card was
  REFUSED without explicit grade confirmation - the tutorial's exact
  claim, proven live) -> accept: 5 facts + 2 history events into canon.
- **Remaining for completed (human hands, ~3 minutes - the automation
  cannot drive Atlas's dropdowns/file dialog):** in the open Atlas Asset
  Manager: + -> Create Collection; + -> Add Map -> pick
  `maps/sites/the-drowned-crypt/the-drowned-crypt.uvtt` -> Create (the
  walls/doors/lights checkpoint); collection settings -> system preset
  picker -> "ICRPG (house)" appears; Open player window; pin
  `table-notes/2026-10-10.md` to the scene.

### Update 2026-10-20 — the map file was wrong; the publisher now does step 4

The remaining steps above got SIMPLER by the table-setup card's live
verify (ticket 53/54): the generated `.uvtt` had never actually been
importable — the exporter wrote map_size in pixels (Dungeondraft
dialect) where Atlas reads squares, and the dungeon walls themselves
were generated transposed (both fixed in core that day; the Drowned
Crypt artifacts regenerated from the same seed, walls now correct).
The card's **Publish site scene** button landed the fixed scene into
the `ember-coast` collection itself (174 walls, 1 door, 4 lights), so
the remaining human steps are: open the campaign vault in Obsidian →
Atlas Asset Manager → confirm the **ember-coast** collection shows The
Drowned Crypt with walls/doors/lights (the old hand-drag through
Create maps is no longer needed) → collection settings' system-preset
picker shows **ICRPG** (the publisher's pack) beside *ICRPG (house)* →
Open player window → pin the table note. The walkthrough page's step-4
hand-drag stays as the always-true manual twin.
- **Found by real use (2026-10-10, the DM's own first import):** Atlas 0.7.1's
  Create-maps BROWSE button filters to `image/*` — the `.uvtt` is greyed out
  there and cannot be picked. The file's way in is DRAG-AND-DROP (the drop
  accepts .dd2vtt/.uvtt/.df2vtt). The .uvtt passed the full 0.7.1 validation
  chain when simulated against the installed bundle (structure, walls, doors,
  lights, budgets, embedded PNG), so this is a picker filter, not a file
  problem — and the PNG need not be imported separately (the .uvtt embeds it).
  ATLAS.md step 4 + troubleshooting rewritten accordingly.
- Deviation found by the walkthrough: the community-list install path was
  replaced by placing the 0.7.1 release files + community-plugins.json
  (Obsidian's trust prompt appeared and was accepted) - same end state;
  0.7.1 is newer than the 0.6.0 floor the page mentions, which the page's
  "0.6.0 or newer" wording already covers.

## Close-out (2026-10-26)

Closed by user ruling with the last box retired, not satisfied: every
step short of the final Atlas clicks was verified live (above), and the
loop's app side is done. Real use at the table is now the verify — an
issue found there mints a NEW ticket, not a back-reference here. Shared
ruling across 46/49/53/54/59; the table-surface verify formally lives
in dcg-avtt-integration (its AGENTS.md §"Atlas-side e2e", 6dcd5ea).
