# Distribution: the updater, the bundle, and the friend-facing seam

**Status:** plan (2026-10-12). The design for how this repo reaches
less-technical friends without re-delivering a 155MB exe per iteration,
and how the Atlas seam's companion reaches the same audience. Tickets:
51 (payload split + CI releases), 52 (update.cmd), 53 (table-setup card +
bundled companion). The companion-side pin mechanism is its ticket 07.
**Context:** the studio ships as a self-extracting exe (one-file release,
2026-10-01); the friend QA handoff (2026-10-05) is the distribution's
whole audience; `dcg-avtt-integration` went to GitHub 2026-10-11
(private), and the core declared AGPL-3.0-only the same day.

---

## 0. The constraint and the principle

**The audience is less-technical friends.** They do not know what git or
uv are; the double-click exe exists to make their life easy. The
friend-facing interface for everything in the family is therefore
**buttons in the app**, with the documented CLI as the always-true
fallback (the ATLAS.md pattern). uv and git are developer tools and never
appear on a friend's machine.

**The principle:** ease comes from one download and buttons that verify;
separation comes from the spawn-and-file contracts and native update
channels — not from splitting the download. Separate at the
process/format boundary (the seam contracts, unchanged); bundle at the
artifact boundary (one exe, one update channel); delegate at the
native-channel boundary (Obsidian installs Obsidian's things).

## 1. What the exe actually carries (measured 2026-10-12)

The built artifact is 155.7MB (`release/dist/CampaignStudio-0.1.0.exe`).
On disk: studio 21MB, agentic-simulator 0.7MB, dnd-mapping minus the tool
clones 28MB (mostly git-ignored worlds data), verification + docs <1MB —
call the churny app source ~5–10MB zipped. The rest barely moves: Python
embeddable ~11MB, node ~30MB, and `dnd-mapping/tools/` (FMG 463MB +
settlemaker 216MB with node_modules — the bulk of the artifact), which
changes only when `dnd-mapping/vendor-patches/` changes. Every iteration
re-delivers all of it, plus a freshly baked LLM key.

## 2. Three payloads, one channel

- **Runtime pack** — Python embeddable + node. Ships once; updates
  ~never. (The builder already downloads both from python.org /
  nodejs.org into its cache.)
- **Tools pack** — `dnd-mapping/tools/` with node_modules. Ships once;
  updates only when vendor-patches move.
- **App payload** — studio + engine + dnd-mapping source + docs + launch
  scripts + the pinned companion snapshot (§4). Single-digit MB. This is
  what updates actually ship.

**The full exe never goes away**: it stays the first-install vehicle and
the offline fallback. A bootstrap-only installer (few-hundred-KB fetcher
that pulls everything) was considered and rejected — it adds first-install
failure modes to save a one-time download; the repeated re-delivery was
the pain, not the first one.

## 3. The updater (core ticket 52)

The installed tree carries a stamp (app payload tag/SHA, tools rev,
companion release) written at CI build time. `update.cmd` in the install
root queries `GET /repos/seandmatthews/dnd-campaign-generator/releases/latest`,
compares, downloads the app payload, snapshots the current source dirs
(resolve-apply precedent), swaps, and preserves `campaign-data/` and
`llm.env/` — the preserve-list logic install.cmd already proves. Explicit,
never silent; offline → keep current, say so; launch.cmd never calls it
on its own.

**Public gate (ruling 2026-10-12: the repo goes public).** A private repo
would put a GitHub token inside the friend's install and bill Actions
minutes for release builds; public needs no auth for the update check,
and AGPL-public is the honest end state now that the license is declared.
Gate on the audit-for-public pass (llm.env and campaign-data gitignored;
the rejected `.maps` pruned 2026-10-04; still owed: a docs/history
eyeball).

**Key story:** `llm.env` leaves the payload. First install writes it once
in the install root; updates never touch it; rotation = edit one file.
The builder keeps a seed option on the first-install exe only (the
prepare-a-friend's-machine flow), and the payload pipeline loses the
baked-key wart entirely.

## 4. The companion in the bundle (core ticket 53; companion ticket 07)

The friend's path to the seam's cool stuff is not git+uv — it is the
bundle. `dcg-avtt-integration` rides in the install root as
`avtt-integration/` — a pinned RELEASE (a git tag, per companion ticket
07's discipline), source + AGPL LICENSE intact, same pattern as the
GPL-3.0 settlemaker already shipped with license and source (the bundler
copies `dnd-mapping/` wholesale; `IGNORE_BASE` touches none of it).

The studio spawns it behind buttons — companion contract rule 4
anticipated exactly this ("spawning a process is loose coupling; merging
code is not the fix"). No new runtime: the bundled Python + PyYAML runs
the companion. Code residency does not move: the companion repo owns the
code; the bundle carries a pinned rev recorded in the stamp.

**The pin chain:** the companion tags releases; each release declares its
`ATLAS_PIN` (which Atlas version its ports target, machine-readable,
backed by the golden fixtures); the bundle stamp records the companion
tag + its pin; the table-setup card renders `publish.py version`
(companion VERSION, pin, detected Atlas from the vault's
`.obsidian/plugins/atlas-vtt/manifest.json` — id `atlas-vtt`, real shape
verified on ember-coast 0.7.1). Mismatch policy: presets warn and
proceed, scenes degrade to the `_inbox/` drag-in path — a stale pin fails
safe and loudly by the seam's own architecture.

**Release coupling:** friends see companion updates on the studio's
release cadence, not the companion's. Acceptable: the golden-fixture
degrade path makes staleness harmless, and the updater makes studio
releases cheap.

## 5. Obsidian and Atlas: native channels only (decided, do not revisit)

Atlas VTT is an Obsidian community plugin, not a standalone app: install
and updates belong to Obsidian's plugin channel for the plugin's whole
life. We never fetch, pre-seed, or shadow it — pre-seeding would write
into `.obsidian/`, which the spine ignores everywhere (canon snapshots,
doctor, gitignore check), and would strand updates outside the plugin
updater. Obsidian itself is never chain-installed by us.

The installer stays **branch-free** — no checkboxes. The "table setup"
opt-in lives in the app's first-run surface as a card: register-vault
link (the World-tab button exists, `world.js:742`), "install the ICRPG
preset" button (spawns the bundled companion), read-only Atlas-plugin
detection with the four ATLAS.md steps when absent. Sequencing argument:
at install time no campaign exists — the question only makes sense after
first run.

## 6. Sequencing

```
companion 07 (pin + tags)  → core 53 can pin a release
core 51 (payloads + CI releases; gate = public flip + audit)
core 52 (update.cmd)       → after 51
core 53 (bundle + card)    → after companion 07; independent of 51/52
```

## 7. Non-goals

- No auto-update, ever: explicit check, visible release notes, snapshot
  before swap (nothing lands unreviewed — the studio's own rule).
- No git working copy on the friend's machine; no git requirement.
- No bootstrap-only installer (§2).
- No silent companion tracking of main; tags only.
- The avtt companion's own distribution stays `git clone` + `uv run` for
  developers; only the bundle path reaches friends.

## 8. Revisit log

**2026-10-17 — the installer question, examined again, still shelved.**
The bootstrap-installer rejection (§2) was stress-tested: an installer
that fetches versioned RELEASE ARTIFACTS (this repo's, never live
upstreams) genuinely escapes §2's original rationale — the objection was
to assembling from sources at install time, and a manifest of
hash-verified release assets is a different animal. Shelved anyway: the
big exe stays the first-install vehicle and `update.cmd` the update
channel — one channel, fewer first-install failure modes, and per-release
upload cost is not yet the pain. If it revives, the settled points:
artifacts never sources; the tools pack doubles as the pinned-upstream
insurance copy (official sources are a build-time concern only — the
install never touches official FMG, which no official source serves in
patched form); installer and update.cmd share one fetch-verify-swap
mechanism; prompted key ⇒ artifacts can sit on public GitHub, bundled key
⇒ Discord-only forever (§3 unchanged). Mirrors of pinned upstreams on our
own GitHub = insurance for BUILD time, not a distribution path.
