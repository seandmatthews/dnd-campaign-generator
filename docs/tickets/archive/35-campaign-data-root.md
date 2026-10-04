# 35 — campaign-data root: one gitignored home for all runtime data

**Status:** completed — implemented + migrated + verified 2026-10-04 (see
Implementation notes).
**Priority:** P2 (layout/hygiene — no product behavior change, but it closes
the whole "data lands in tracked space" class)
**Area:** `studio/spine.py`, `studio/engines.py`, `studio/doctor.py`,
`studio/assemble.py`, `agentic-simulator/src/store.py` + `tick.py`,
`dnd-mapping/src/battlemap.py`, `dnd-mapping/src/import_fmg.py`,
`release/build_release.py`, `run-tests.py`, `.gitignore`, docs
**Found by:** user request, 2026-10-04 — motivating instance: the same-day
prune of ~25 MB of rejected `.map` saves that had sat tracked in
`dnd-mapping/worlds/raw/` for weeks (git history keeps their ~4 MB packed
blobs unless history is rewritten, which we chose not to do).

## Problem

Runtime campaign data is scattered across ~8 locations hardcoded off the
repo parent, each held out of git by its own `.gitignore` entry:

| location | contents | ignored via |
|---|---|---|
| `campaigns/` | the campaign workspace — a nested git repo per campaign (canon snapshots) | `campaigns/` |
| `agentic-simulator/world/` | engine home: `state.yaml`, `state.yaml.bak`, `.week-run-flag`, `.engine-home.lock`, `replay/` | dir + two file entries |
| `agentic-simulator/chronicle/` | engine-home chronicle output | dir |
| `studio/logs/` | server logs | dir |
| `dnd-mapping/worlds/sites/` | battlemap engine home (regenerated on demand) | dir |
| `dnd-mapping/out-gen-city-smoke/` | gen-city smoke output | dir |
| `dnd-mapping/worlds/candidates/` | **dual status**: deliberately tracked curated `geography.yaml`s + `index.md` + screenshots, AND the dir `import_fmg` drops new candidates into on every run ("a new curated one needs `git add -f`") | dir (tracked files by exception) |

Hardcoding sites (all derived independently — no shared seam):

- `studio/spine.py:26` — `CAMPAIGNS_ROOT = STUDIO_DIR.parent / "campaigns"`
  (read at call time via `campaigns_root()`; tests monkeypatch the module
  constant).
- `studio/doctor.py:276` — re-derives `PARENT / "campaigns"` on its own.
- `studio/assemble.py:716,735` — hardcodes `root / "campaigns"` twice.
- `studio/engines.py:44-48` — `PARENT`, `AGENTIC_SIMULATOR`,
  `DND_MAPPING`; the engine-home helpers at `:157-195` hardcode
  `AGENTIC_SIMULATOR / "world"` (week flag, engine-home lock, state,
  state.bak, replay dir).
- `agentic-simulator/src/store.py:25-26` — engine `ROOT` is its own repo
  dir, `STATE_FILE = ROOT / "world" / "state.yaml"`; `tick.py:78` —
  `CHRONICLE_DIR = ROOT / "chronicle"`.
- `dnd-mapping/src/battlemap.py` — sites home under its own tree.

Consequences:

- Every new kind of data artifact needs a fresh `.gitignore` entry, and
  forgetting one means data silently lands in tracked space.
- Scratch and curated content share a directory (`worlds/candidates/`),
  separated only by `git add -f` discipline.
- Nothing structural prevented the pollution this ticket exists to end:
  ~35 MB of regenerable binaries sat tracked for weeks and had to be
  pruned by hand (2026-10-04) — and the pack keeps those blobs forever.
- The release build already needs a data/code distinction (the
  `%LOCALAPPDATA%\Campaign Studio` install must keep campaign data across
  updates) and fakes it by special-casing the directory name `campaigns/`
  inside a layout that is otherwise a full copy of the repo.

## Proposal

One data root — `campaign-data/` — gitignored wholesale, resolved by one
rule everywhere. Data that is currently scattered becomes unfindable-in-git
by construction: a fresh clone contains zero campaign data, and nothing
that a run produces can ever be `git add`ed by accident.

### 1. Layout

```
campaign-data/
  campaigns/       # the campaign workspace (nested git repos) — contents unchanged
  engine-home/     # ex agentic-simulator/world/: state.yaml, .bak, flags, locks, replay/
  chronicle/       # ex agentic-simulator/chronicle/
  maps/sites/      # ex dnd-mapping/worlds/sites/ (battlemap home)
  logs/            # ex studio/logs/
  scratch/         # ex dnd-mapping/out-gen-city-smoke/ + import_fmg candidate drops
```

### 2. Resolution rule (one rule, every consumer)

`CAMPAIGN_DATA_ROOT` env var if set, else `<repo-root>/campaign-data`
(repo root = each component's existing notion of its parent repo —
`STUDIO_DIR.parent` for the studio side, the engine's and battlemap's
`ROOT.parent` equivalents).

- `spine.py` gains `data_root()` and `CAMPAIGNS_ROOT` becomes derived
  (`data_root() / "campaigns"`), so the existing `campaigns_root()` call
  sites — and most tests — keep working unchanged.
- `engines.py` must SET `CAMPAIGN_DATA_ROOT` explicitly in the env it
  hands every engine/map subprocess (`engines.py:223` already merges env):
  the engine and battlemap CLIs do not import spine, so inheritance from
  the studio process is the only thing that keeps them on the same root.
- The engine (`store.py` STATE_FILE/world bits, `tick.py` CHRONICLE_DIR),
  `battlemap.py` (sites home), and `import_fmg.py` (candidate drop dir →
  `campaign-data/scratch/map-candidates/`) read the env with the same
  default rule.
- `doctor.py` and `assemble.py` switch their hardcoded `campaigns` paths
  to `spine.campaigns_root()` / the shared rule.

### 3. Tracked content stays put

Curated candidates (`worlds/candidates/` YAMLs, `index.md`, screenshots),
`worlds/raw/` genesis dumps, and `worlds/ruravaqan/` outputs are
generation *records*, not runtime data — they stay exactly where they are,
tracked. The `worlds/candidates/` dual status ends by splitting roles: the
DROP site moves to `campaign-data/scratch/map-candidates/`; curation
becomes "copy into `worlds/candidates/` + `git add`" — no more
`add -f` against an ignore, and a fresh candidate drop can never show up
as untracked noise in a tracked dir.

### 4. `.gitignore`

Replaces the seven data entries with one line (`campaign-data/`).
`tools/`, `llm.env`, `.venv/`, `.zcode/`, `table-companion/` keep their
entries — they are not campaign data.

### 5. Migration (explicit, not magic)

- `doctor` gains a `migrate-data-root` action: plain renames of the legacy
  dirs into the new root (same volume → atomic-ish; nested campaign git
  repos and lock files move intact), refused while any campaign lock,
  engine-home lock, or week flag is held, idempotent (no-op when legacy
  dirs are absent).
- Studio boot shows the existing banner pattern when legacy dirs are
  detected, pointing at the doctor command. No silent auto-migration.
- **User-visible cost to document**: the Obsidian vault lives inside the
  campaign folder, so the vault path changes — the vault must be re-opened
  once at the new path after migrating. `obsidian://` URIs the app builds
  derive from `campaigns_root()` and follow the move automatically.

### 6. Release

`launch.cmd` sets `CAMPAIGN_DATA_ROOT` to the install dir's
`campaign-data/`; `build_release.py` stages that path and keeps its
"robocopy /MIR never touches campaign data" exclusion under the new name
(today it special-cases `campaigns/` at `:8-9,164-168,275-280`).

## Decisions baked in (rulings, not backlog)

- Default root is INSIDE the repo (`campaign-data/`), gitignored — not an
  out-of-repo location. The user asked for exactly this shape.
- Explicit doctor-run migration with a boot banner; no auto-migration on
  boot (a move touching every campaign must never happen silently
  mid-session).
- Curated/tracked generation records do not move.

## Acceptance criteria

- [x] `.gitignore` carries exactly one data-root entry
      (`campaign-data/`) and no per-artifact data entries remain.
      Pinned by `studio/tests/test_data_root.py` (layout + rule tests);
      `git check-ignore campaign-data` confirms.
- [x] A full loop — worldgen (real headless FMG), a live LLM week (real
      `tick.py --live`, 5 calls), battlemap gen (real gen/render/sharp/
      wrap), export — writes only under `campaign-data/`; `git status`
      stayed clean apart from intentional edits. Proven live on a
      throwaway campaign `ticket35-loop-proof` (deleted after).
- [x] `CAMPAIGN_DATA_ROOT` override is honored by the studio
      (`spine.data_root()`, call-time env read), the engine CLI (tick
      inspect read a state that existed only under the override home),
      and the battlemap CLI (gen-cave wrote only under the override).
      Pinned for machines with prerequisites by the three
      `test_env_override_*` tests in `studio/tests/test_integration.py`;
      verified live 2026-10-04 (the integration tier itself skips on this
      machine — see notes).
- [x] Doctor `migrate-data-root` moves legacy dirs with locks
      refused-if-held, is idempotent, and the nested campaign git history
      survives the move. Pinned by
      `test_migrate_moves_legacy_dirs_and_keeps_git_history`,
      `test_migrate_is_idempotent`,
      `test_migrate_refused_while_campaign_lock_held`,
      `test_migrate_refused_while_week_flag_left_behind`,
      `test_migrate_refused_when_target_exists` (all
      `studio/tests/test_data_root.py`); run for real on this checkout —
      `git log` intact in the moved campaigns repo.
- [x] Legacy-dir detection shows the boot banner; nothing migrates
      silently. Pinned by `test_boot_banner_fires_on_legacy_dirs` +
      `test_check_data_root_names_legacy_dirs`; the doctor `data root`
      check surfaces the same pointer in the GUI health strip.
- [x] Fresh-clone integration: fast tier green with data only under the
      new root; `run-tests.py` `INTEGRATION_NEEDS` updated to the new
      path. The integration tier itself still skips on this machine —
      its `campaigns/ruravaqan` + `campaigns/sample` prerequisites were
      removed from this checkout BEFORE this ticket (pre-existing skip,
      unchanged in kind by the rename).
- [ ] Release smoke: install → run → re-update keeps `campaign-data/`
      intact. NOT RE-RUN (a full ~20-min build was not spent here); the
      seam is covered by `test_release_seams.py`
      (exclusion shape, legacy-move-before-mirror order, first-install
      seed line, and a live robocopy /MIR semantics test). Next rebuild
      exercises it for real.
- [x] Docs updated (TUTORIAL/API/READMEs/obsidian-integration/AGENTS +
      dnd-mapping docs) including the vault-relocation note. The
      Obsidian vault was re-registered in place (obsidian.json path
      edit, Obsidian closed) so it reopens at
      `campaign-data/campaigns/brineharrow-coast/lore`.

## Implementation notes (2026-10-04)

Landed as one change on `master` (ticket file moved to `archive/` in the
same commit). The rule: `$CAMPAIGN_DATA_ROOT` if set, else
`<repo-root>/campaign-data`, resolved by `spine.data_root()` on the
studio side and independently in the engine (`store.DATA_ROOT`) and
dnd-mapping (`battlemap.DATA_ROOT`, `import_fmg.DATA_ROOT`);
`engines._run` stamps `CAMPAIGN_DATA_ROOT` onto EVERY subprocess so the
CLIs stay on the studio's root without importing spine.

Deviations from the proposal, with reasons:

1. **`tick.REPLAY_DIR` module constant.** The proposal listed the replay
   dir under the engine-home helpers; the engine tests relocate state,
   chronicle and replay per-module-global, so the replay trail got its
   own patchable constant beside `CHRONICLE_DIR` (tests patch it; two
   tests had silently been writing replays into the REAL engine home —
   fixed and the pollution removed).
2. **`--dir` argv stays dnd-mapping-relative.** The studio computes the
   sites dir absolutely but passes `os.path.relpath(...)` to battlemap
   render/wrap and render-scene-png.mjs: the rasterizer joins its arg
   onto the dnd-mapping root (`path.join` mangles absolute args). The
   copy-back reads the absolute path. Pinned by `test_sites_job.py`.
3. **Release: a visible one-time legacy move in install.cmd.** §6 said
   launch.cmd + robocopy exclusion; that alone would orphan (and /MIR
   would DELETE) an upgraded install's `campaigns\`. install.cmd now
   moves `%DEST%\campaigns` → `%DEST%\campaign-data\campaigns` BEFORE the
   mirror (error-visible, logged), and `%DEST%campaigns` stays in `/XD`
   as a safety net if the move could not run. Pinned by
   `test_release_seams.py`.
4. **`_move_dir` reports mid-move crashes honestly.** The REAL migration
   hit this live: a transient Windows handle on one `campaigns/.git`
   object made `shutil.move`'s rename fail and its copy+delete fallback
   leave the source partially deleted (the target already held the FULL
   copy — copy precedes delete). The failure now raises a readable
   "the target holds the full copy; delete the leftover and re-run"
   instead of a raw traceback; the real leftover was finished by hand and
   the resumed migration completed cleanly.
5. **The campaigns root is ONE git repo** spanning all campaigns (not
   one repo per campaign as the ticket's phrasing implied) — it moved as
   a unit; `git log` verified at the new path.
6. **`check_week_run`/`check_data_root`** both read through the engines
   helpers, so the doctor's flag check automatically follows the new
   engine home; `check_campaigns`/`check_vault` switched from re-derived
   `PARENT/"campaigns"` to `spine.campaigns_root()`.
7. **Integration-tier prerequisites**: still ruravaqan+tools, now under
   `campaign-data/campaigns/`. This machine lacks the campaigns (and had
   already lost them pre-ticket), so the tier skipped before and after;
   the env-override criteria were verified live via the CLIs instead.

Follow-ups (none blocking): the release build's next `--all` run should
execute the release smoke box above. The stray `campaigns/simulation/`
dir (empty all the way down — a misdirected mkdir, zero files) that had
ridden along in the old tree was deleted during verification.
