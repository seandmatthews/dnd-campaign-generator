# One-file release

One command produces the shareable exe plus the payload split (ticket
51):

```
python release/build_release.py
```

→ `release/dist/CampaignStudio-<version-stamp>.exe` (~155 MB — the full
`VERSION` stamp is the filename, so every build ships under its own
self-describing name and an old download is legible as old), plus a
`BEFORE-YOU-RUN.txt` staged beside it — the SmartScreen/antivirus
guidance the friend can read before the first double-click (the same
material inside README.txt, which the friend cannot open until the exe
has run). Send both files however you like (Google Drive, etc.); the
friend double-clicks the exe.

The same stage packs three payload archives beside the exe:

- `CampaignStudio-app-<stamp>.zip` — studio + engine + dnd-mapping
  source + docs + launch scripts + the pinned companion, **as a ZIP**:
  the friend's machine unpacks it with native `Expand-Archive`, so the
  updater never needs 7-Zip. No runtimes, no tools, **never a key**:
  this is what an update delivers, and an update must not touch the
  installed `llm.env`.
- `CampaignStudio-runtime-py<ver>-node-<ver>.7z` — the bundled Python
  and node. Ships once; updates ~never.
- `CampaignStudio-tools-<rev>.7z` — `dnd-mapping/tools/` with
  node_modules. Ships once; the rev (derived from `setup-tools.py`'s
  pins) bumps only when the vendor pins move.

These are the assets a `v*` tag push ships: `.github/workflows/
release.yml` builds the app payload on a Windows runner, attaches it to
a GitHub Release with its SHA256 in the body, and proves the public
gate by downloading it anonymously (no token) and hash-checking it.
The full exe never goes to CI — it stays the local, first-install
vehicle and offline fallback (design doc §2).

## The friend's updater (ticket 52)

Every install carries `update.cmd` in its root. It queries
`releases/latest` unauthenticated (the repo is public), compares the
release tag against the install's `app-release:` stamp block, asks
before doing anything, downloads the app payload, hash-verifies it
against the release body with `certutil`, snapshots the current app
files into `.update-backup\`, then `/MIR`s the app surface over the
install — never touching `campaign-data/`, `llm.env`, `runtime/`, or
`dnd-mapping\tools\`. Every refusal keeps the tree and says so. The
staged `VERSION` is a block: line 1 is the stamp install.cmd
byte-compares (shape frozen), the lines under it are what update.cmd
parses. Build a handout exe as `--release v0.1.1` to stamp it with the
release it belongs to (CI writes the tag itself); without either, the
block records `local` and the updater still works, offering the latest
with both tags printed.

## What the exe does on the friend's machine

1. Extracts its payload to a temp dir (7-Zip's `7zSD` installer stub; an
   embedded `asInvoker` manifest keeps UAC quiet — no admin needed).
2. Runs `install.cmd` from the temp dir, which refuses to run over a
   studio that is still open (it probes the port first), then
   `robocopy /MIR`s the app into `%LOCALAPPDATA%\Campaign Studio` —
   **never touching `campaign-data/` or an installed `llm.env`** — so
   version updates refresh code and keep data and the friend's key.
   A mirror that fails writes no completion stamp, so the next run
   recopies instead of fast-pathing over a half-installed mix.
3. Starts `launch.cmd` there: bundled Python boots the studio, browser
   opens at `http://127.0.0.1:8765`. A "Campaign Studio" desktop shortcut
   is created on first start; closing the console window stops the server.
4. Re-running the same-version exe skips the copy and just relaunches
   (the still-required temp extraction takes a minute or two; the desktop
   shortcut is the everyday path).

## What's inside

Bundled, nothing installed on the target (64-bit Windows 10/11 only):

- `runtime/python` — embeddable CPython 3.11 + `studio/requirements.txt`
- `runtime/node` — official node.exe v24 (the FMG tool tree wants ≥24)
- `studio/`, `agentic-simulator/`, `dnd-mapping/`, `verification/` —
  source in the repo's exact sibling layout (the code hard-resolves
  these paths off the bundle root; zero code changes for portability)
- `dnd-mapping/tools/` — FMG + settlemaker WITH `node_modules`, prebuilt;
  npm is never needed at runtime
- `llm.env` — your LLM key, resolved by the studio's own `resolve_config`
  (repo `llm.env`, else the ZCode provider config). **Anyone holding the
  exe holds the key** — share deliberately; `--no-key` ships without it.

## Build flags

- `--campaigns` — copy your `campaign-data/campaigns/` into the bundle (default: empty)
- `--no-key` — ship without `llm.env` (LLM features then need a key)
- `--release v0.1.1` — stamp the build as that release (the `app-release:`
  line update.cmd compares); CI injects the tag itself
- `--app-payload-only` — build just the app payload (the CI release
  job's mode): no runtimes, no tools, no exe, never a key; refuses
  `--campaigns`/`--no-key`/`--sfx-only` because they do not apply
- `--no-smoke` — skip the boot + installer self-tests (not recommended)
- `--sfx-only` — reuse `release/work/payload.7z`, redo only the SFX step
  (warns loudly when a tracked template is newer than the cached payload —
  the reuse would silently skip testing that edit); skips the payload
  emission, which belongs to full builds only
- `--keep-work` — keep the staging tree, payload, and installed test copy

Downloads (CPython embeddable, get-pip, node, 7zr, LZMA SDK) are cached
in `release/cache/` (git-ignored, ~52 MB); staging and payload live in
`release/work/` (git-ignored, wiped after a successful build).

## Self-verification (every build, unless `--no-smoke`)

1. Stage smoke: bundled python imports the deps, bundled node runs,
   `launch.cmd` boots the studio (`/` and `/tutorial` → 200, port 8791),
   and `export.cmd` (the friend backup path, ticket 58) runs on the
   bundled python with no system python involved.
2. Installer pass: the built exe is actually executed headlessly; the
   build waits for install, then `/` and `/tutorial` → 200 (port 8793).
3. Fast-path pass: a sentinel file at the install root must survive the
   second run — proving `robocopy /MIR` did not run again.
4. Cross-version update pass (ticket 57): the same exe run over a
   synthesized older install must preserve a rotated `llm.env` and a
   campaign marker while advancing `VERSION`; over a running studio it
   must refuse before mirroring; and a forced robocopy failure must
   leave no completion stamp, so the next run re-mirrors instead of
   fast-pathing (port 8795).
5. Payload-set pass (ticket 51): every packed archive is listed and
   compared against the plan — the three payloads are disjoint, cover
   every staged file, and their union is the exe's `payload.7z`; any
   gap, overlap, or stray file fails the build.
6. App-over-full equivalence (ticket 51): the app payload extracted
   fresh must equal the installed app surface byte for byte, and laid
   over a real install it must boot and serve `/`, `/tutorial`,
   `/atlas` (port 8797) — the update path result is the full-install
   result.

## Updating the release

Change code → rerun the build → send the new exe. No version bump needed:
the staged `VERSION` file carries a build stamp, so any rebuild differs
from the friend's install and the installer recopies (a few minutes,
campaigns untouched — close the friend's Studio first; the installer
refuses until they do). Rerunning a byte-identical exe stays on the fast
path. The exe's filename carries the same stamp, so "which build are you
running?" is answered by the file name itself.

## Known caveats

- **SmartScreen**: the exe is unsigned; first double-click shows
  "Windows protected your PC" → More info → Run anyway. A code-signing
  cert removes this.
- **Disk**: ~1.7 GB free during an install — the ~155 MB exe plus
  ~750 MB temp extraction plus ~750 MB installed (the temp copy goes
  away when it finishes).
- **`&` in the Windows user name** breaks cmd's batch quoting for the
  installed path (`%LOCALAPPDATA%` would contain `&`) — exotic, but the
  whole build pipeline is haunted by it because the dev repo lived in
  `Documents\D&D` until the 2026-10-04 rename; every path that crosses a
  cmd command line here is quoted by hand (see comments in
  `build_release.py` / `install.cmd`).
- Test ports: 8791 (stage smoke), 8793 (installer verify), 8795
  (cross-version update verify) — the launcher honors `STUDIO_PORT` and
  `STUDIO_NO_BROWSER` for headless runs.
