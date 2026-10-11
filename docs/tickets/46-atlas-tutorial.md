# 46 — ATLAS.md: the at-the-table tutorial

**Status:** in-progress (implemented 2026-10-10; the live start-to-finish
walkthrough is pending — no campaign exists on this machine since the
2026-10-05 data wipe, and building one with a site + simulation is an
evening of LLM runs)
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

## Implementation notes (2026-10-10)

Landed with the seam batch: `docs/for-new-gms/ATLAS.md` (the corrected
loop — manual open-as-vault on the campaign root, inline example ICRPG
preset with every field verified against the atlas-vtt 0.6.0 parser
source, real session-note flow, sites-only `.uvtt`), the README "Start
here" pointer, the `atlas-seam.md` P3 path correction, and the one code
touch: `release/build_release.py` stages ATLAS.md beside TUTORIAL.md.
Every command/path in the page was checked against the code
(`session_notes.py` CLI surface, `maps.py` site artifacts, Obsidian/Atlas
folder names from the plugin source). REMAINING for completed: the
start-to-finish walkthrough on a real campaign (needs a campaign with a
generated site + a simulation), and one full release build to see the
staged file ship (the copy2 line mirrors the TUTORIAL one; not
build-verified yet).
