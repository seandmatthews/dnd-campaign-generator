# Atlas seam — core-side next steps

**Status:** plan (2026-10-10) — and the plan landed 2026-10-10..12: W1, W4,
W5 shipped as core tickets 48, 47, 50; W2 and W3 are implemented (tickets
49, 46) with their final live verifies pending. The items below are the
seam work items that live in
THIS repo after the residency ruling — what stays here, why, what each one
ships, and how it interlocks with the companion repo.
**Reads with:** [`design/atlas-seam.md`](design/atlas-seam.md) (the full seam
spec — formats, contracts, milestones) and the companion repo's README at
`Documents\dcg-avtt-integration\README.md` (everything that writes
Atlas-owned formats or system-specific data).
**Context:** the studio is gaining Atlas VTT as its at-the-table surface;
the icrpg table companion is frozen at v0 in favor of it. The seam's
ruling: **core keeps everything that speaks spine-native or interchange
formats; everything that writes Atlas-owned formats, or carries
system-specific data, lives in the companion repo.** This file is the
here-half of that split.

---

## 1. The ruling in one paragraph

The core's authority comes from committing to formats nobody owns —
markdown, YAML, `.uvtt`, PNG, `statblock` fences — and never to apps.
Obsidian integration lives in-core on exactly those grounds; Atlas gets the
same treatment. Where the seam spec (`design/atlas-seam.md`, §3) wrote file
homes, this ruling reassigns them: the ICRPG preset file and pack installer
(P0 content), the scene publisher (P2), fence *templates* (P1 content), and
the party publisher (P4) move OUT to `dcg-avtt-integration`. What stays
here is below. The seam spec carries an amendment note; its contracts,
formats, and milestones are unchanged — only the residency moves.

## 2. Core work items

### W1 — `system:` field in `campaign.yaml`

**What:** one optional field (`system: icrpg`), validated loosely (freeform
string, lowercased; no enum — the core does not know the catalog of systems
and must not grow one). It gates the Tier-1 ICRPG prep packet
(icrpg DESIGN §12) and is the same field the companion's pack installer
reads to decide whether a pack applies.
**Why core:** canon metadata about the campaign; wanted before Atlas
existed.
**Ships:** schema addition + doctor check (unset is fine and normal — the
studio stays system-agnostic by default).
**Verify:** sample campaign with `system: icrpg` passes validation; a
typo'd value (`system: icrpggg`) still passes — findings, never verdicts.

### W2 — Statblock fence convention (P1's core half)

**What:** entity pages (`lore/entities/*.md`, `kind: person` — kinds in
code are freeform: person/place/city/world/nation, stubs unknown; the
seam spec's original `pc`/`npc` exist nowhere) may carry a
` ```statblock ` fence in the BODY. Rules: fences enter through the
existing pending flow (they are prose — nothing lands unreviewed); they are
body-only (the spine's frontmatter parser must never see pack-specific
keys); the verification ladder grows a structural check (fence parses as
`key: value` lines, numbers parse, lines are non-empty) — findings, never
verdicts. Field *vocabularies* are the pack's business, not the ladder's.
**Why core:** the fence is an ecosystem interchange format (Fantasy
Statblocks'), carried in spine-native markdown; Atlas reads it with no
plugin installed, and so do other consumers.
**Ships:** pending-flow insertion path for fences; ladder check; one
entity-card button ("draft statblock") that stages a fence pending entry.
The ICRPG template it drafts FROM lives in the companion pack — the core
mechanism is template-agnostic.
**Verify:** approve one fence on a Ruravaqan NPC page → spine loader
unchanged (bodies were always prose) → Atlas links a token to the page and
renders it.

### W3 — `docs/for-new-gms/ATLAS.md` (P3's main half)

**What:** the campaign-running tutorial for the Atlas loop, with the
house-standard "you should see" checkpoints and stated step durations:
install Atlas (community plugin, desktop, Obsidian ≥ 1.8.7) → open the
campaign folder as the vault (the studio registers it; obsidian-integration
§0) → apply the ICRPG preset (companion `publish.py presets`, or the manual
drag for a system Atlas ships built-in) → publish/drag a battlemap →
GM view vs player-view window on the second screen → play (pin the session
note to the scene) → `session_notes.py import` → review queue. It points to
the companion repo's README for the publisher and records the manual
fallbacks (drag a `.uvtt` by hand) as the always-true path, not the
consolation prize.
**Why core:** it documents running a campaign, which is the core's product;
it degrades to zero-Atlas use like every obsidian-integration stage.
**Ships:** the tutorial + a one-line pointer from the main README's
"Start here".
**Verify:** follow it on this machine start to finish, zero code in either
repo (M0 of the seam spec — achievable tonight).

### W4 — `atlas-vtt/` snapshot policy

**What:** the campaign's nested canon-snapshot git repo treats
`atlas-vtt/` as files (committed): tuned scenes are earned artifacts and
their diffs are reviewable. `.obsidian/` stays ignored
(obsidian-integration §4 already demands the ignore line — doctor should
check it exists). Published output riding in snapshots is consistent with
companion notes and params.yaml: generated metadata, not reviewed prose.
**Why core:** snapshot policy is spine policy.
**Ships:** `.gitignore` entries + doctor check + one paragraph in
ATLAS.md ("your Atlas tuning is snapshotted; regenerate never overwrites —
refuse-on-diff is the publisher's promise").
**Verify:** publish a scene (or hand-place any file under `atlas-vtt/`) →
snapshot commit contains it; `.obsidian/` absent from every commit.

### W5 — Companion-note embed-line convention

**What:** record that a site's companion note may carry an appended
Atlas-scene link line (added by the companion publisher on publish,
append-only and idempotent). Companion notes are generated metadata
(obsidian-integration §4) — this names one more consumer's line so future
generators never clobber it.
**Why core:** the companion-note format is a spine convention.
**Ships:** a paragraph in `design/atlas-seam.md` (amendment) and a line in
whatever doc governs companion notes.
**Verify:** none code-side; it is a documented invariant.

### W6 — (optional, later) Sessions-tab "import notes" button

**What:** a button over the existing `session_notes.py import` flow; zero
new machinery. Build only if the CLI feels heavy after two real sessions
(seam spec, open decisions).
**Why core:** the session pipeline is core machinery.
**Verify:** button and CLI produce the same session record.

## 3. Sequencing and the interlock

```
W3+W4  (tutorial + snapshot policy)   → tonight's zero-code loop (seam M0)
W1     (system: field)               → unblocks companion pack installer
W2     (fence convention)            → unblocks companion ICRPG fence templates
companion spikes → pack installer → scene publisher   (their README §12)
W5     (embed-line convention)       → lands before the companion's scene
                                        publisher writes its first line
W6     (optional button)             → after two real sessions
```

W3 and W4 are the only items the table needs this week. W1 and W2 are small
and unblock the companion repo's real work. Nothing here blocks anything
else in the studio.

## 4. Non-goals (core side, restated)

- The core never writes Atlas-owned formats (`.atlasmap`, records,
  presets) — that is the companion's whole job.
- The core never reads `atlas-vtt/` back (table state is chalk).
- The core never grows a system catalog, rules engine, or stat-block
  generator of its own. Systems are data at the edge, forever.
- No LLM in any of the above except the existing review-gated generation
  paths (a drafted fence may use the bounded-LLM pattern later, gated like
  every draft).

## 5. Open decisions (core-side)

| question | leaning |
|---|---|
| `system:` validation strictness | freeform + lowercase, no enum (the catalog lives in packs and in Atlas built-ins) |
| fence structural-check strictness | shape only (parses, numbers parse); vocabularies are pack-side |
| does W6 get built at all | decided by fatigue, not by roadmap |
| does ATLAS.md live in `for-new-gms/` or get split prep vs table | one page until it outgrows one scroll |
