# D&D tooling

Three engines and one app under one parent. Every project is runnable and
tested on its own; the studio is the only one that invokes the others.

Not affiliated with or endorsed by Wizards of the Coast — "D&D" here names
the kind of table the tool prepares for, nothing more.

| project | what it is | entry point |
|---|---|---|
| [`dnd-mapping/`](dnd-mapping/) | worlds → cities → battlemaps; everything regenerated from a recorded seed | [`AGENTS.md`](dnd-mapping/AGENTS.md) |
| [`agentic-simulator/`](agentic-simulator/) | LLM faction sandbox on dice: proposals, resolution, rumors, chronicle | [`AGENTS.md`](agentic-simulator/AGENTS.md) |
| [`verification/`](verification/) | lint + adversarial-review kit for generated prose (findings, never verdicts) | [`README.md`](verification/README.md) |
| [`studio/`](studio/) | Campaign Studio: one localhost app over the three engines — maps, world building, agent simulation, one review queue | [`README.md`](studio/README.md) |
| `campaign-data/` | ALL runtime data in one git-ignored home: `campaigns/` (the campaign workspace — one directory per campaign, files only, no database; its own nested git repo), `engine-home/`, `chronicle/`, `maps/`, `logs/`, `scratch/` — nothing a run produces can be `git add`ed by accident; `$CAMPAIGN_DATA_ROOT` overrides the location | — |

## Start here

- **New here? Everything a human needs lives in
  [`docs/for-new-gms/`](docs/for-new-gms/). Start with
  [`the tutorial`](docs/for-new-gms/TUTORIAL.md)** — from zero to a
  living campaign (world, maps, lore, a simulated week): days or weeks of
  world history, compressed into a couple of hours at the keyboard, with
  a "you should see" checkpoint at every step and each long step's
  expected duration stated before it runs.
- **Running the table? [`ATLAS.md`](docs/for-new-gms/ATLAS.md)** — the
  same campaign on a screen at the table with
  [Atlas VTT](https://github.com/atlas-vtt/atlas-vtt): the generated
  battlemaps run as-is, and the session's story comes home through the
  review queue.
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
- **The design doc:** [`docs/design/campaign-studio-design.md`](docs/design/campaign-studio-design.md)
  — the spine (campaign = a directory of files), the cross-tab contracts,
  and the guardrails (CLI equivalence, fed-or-refused, nothing lands
  unreviewed).

## Layout notes

- `dnd-mapping/tools/` is git-ignored (third-party clones) — but the custom
  FMG headless runner is NOT vanilla; tracked copies live in
  [`dnd-mapping/vendor-patches/fmg/`](dnd-mapping/vendor-patches/fmg/).
- `run-tests.py` is stdlib-only, list-form subprocess throughout — nothing
  ever goes through a shell (a rule kept from when the parent path
  contained `&`).

## License

AGPL-3.0-only — see [`LICENSE`](LICENSE), the same license its Atlas seam
companion ([dcg-avtt-integration](https://github.com/seandmatthews/dcg-avtt-integration))
carries. Third-party trees keep their own licenses: the vendored Fantasy
Map Generator under `dnd-mapping/tools/` is MIT and settlemaker there is
GPL-3.0-only; the release bundle ships both with their license files and
sources intact.
