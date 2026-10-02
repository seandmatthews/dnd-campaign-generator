# D&D tooling

Four engines and one app under one parent. Every project is runnable and
tested on its own; the studio is the only one that invokes the others.

| project | what it is | entry point |
|---|---|---|
| [`dnd-mapping/`](dnd-mapping/) | worlds → cities → battlemaps; everything regenerated from a recorded seed | [`AGENTS.md`](dnd-mapping/AGENTS.md) |
| [`agentic-simulator/`](agentic-simulator/) | LLM faction sandbox on dice: proposals, resolution, rumors, chronicle | [`AGENTS.md`](agentic-simulator/AGENTS.md) |
| [`verification/`](verification/) | lint + adversarial-review kit for generated prose (findings, never verdicts) | [`README.md`](verification/README.md) |
| [`studio/`](studio/) | Campaign Studio: one localhost app over the three engines — maps, world building, agent simulation, one review queue | [`README.md`](studio/README.md) |
| [`campaigns/`](campaigns/) | the campaign workspace — one directory per campaign, files only, no database; **its own nested git repo, git-ignored here: campaign data never reaches the parent repo's remotes** | — |

## Start here

- **New here? Follow [`TUTORIAL.md`](TUTORIAL.md)** — from zero to a
  living campaign (world, maps, lore, a simulated week): days or weeks of
  world history, compressed into a couple of hours at the keyboard, with
  a "you should see" checkpoint at every step and each long step's
  expected duration stated before it runs.
- **Just run the app:** `python studio/run.py` (or `studio/start.bat`) —
  if anything is missing, the doctor script (`python studio/doctor.py`)
  checks every prerequisite and tells you the exact command that fixes it.
  With [uv](https://docs.astral.sh/uv) installed, `uv run python studio/run.py`
  is the whole story: it sets up the environment and the locked packages
  (`uv.lock`) by itself.
- **Full setup (maps + world gen):** `python setup-tools.py` — clones the
  two third-party map tools into `dnd-mapping/tools/` (git-ignored), pins
  and installs them with plain npm, and applies our FMG headless-runner
  patches from `dnd-mapping/vendor-patches/fmg/`. `--check` reports
  readiness only.
  Read-only use (browsing lore, chronicles, the review queue) works
  without it.
- **Run every test:** `python run-tests.py` (fast tier, seconds) or
  `python run-tests.py --all` (adds the real-engine integration tier).
  CI runs the fast tier on push (`.github/workflows/ci.yml`).
- **The design doc:** [`campaign-studio-design.md`](campaign-studio-design.md)
  — the spine (campaign = a directory of files), the cross-tab contracts,
  and the guardrails (CLI equivalence, fed-or-refused, nothing lands
  unreviewed).

## Layout notes

- `dnd-mapping/tools/` is git-ignored (third-party clones) — but the custom
  FMG headless runner is NOT vanilla; tracked copies live in
  [`dnd-mapping/vendor-patches/fmg/`](dnd-mapping/vendor-patches/fmg/).
- `run-tests.py` is stdlib-only, list-form subprocess throughout (the parent
  path contains `&` — nothing ever goes through a shell).
