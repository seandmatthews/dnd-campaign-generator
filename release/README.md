# One-file release

One command produces one shareable file:

```
python release/build_release.py
```

→ `release/dist/CampaignStudio-<version>.exe` (~233 MB). Send that single
file however you like (Google Drive, etc.); the friend double-clicks it.

## What the exe does on the friend's machine

1. Extracts its payload to a temp dir (7-Zip's `7zSD` installer stub; an
   embedded `asInvoker` manifest keeps UAC quiet — no admin needed).
2. Runs `install.cmd` from the temp dir, which `robocopy /MIR`s the app
   into `%LOCALAPPDATA%\Campaign Studio` — **never touching `campaign-data/`**,
   so version updates refresh code and keep data.
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
- `--no-smoke` — skip the boot + installer self-tests (not recommended)
- `--sfx-only` — reuse `release/work/payload.7z`, redo only the SFX step
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

## Updating the release

Change code → rerun the build → send the new exe. No version bump needed:
the staged `VERSION` file carries a build stamp, so any rebuild differs
from the friend's install and the installer recopies (a few minutes,
campaigns untouched). Rerunning a byte-identical exe stays on the fast
path.

## Known caveats

- **SmartScreen**: the exe is unsigned; first double-click shows
  "Windows protected your PC" → More info → Run anyway. A code-signing
  cert removes this.
- **Disk**: ~1.6 GB installed, plus the same again transiently during
  install (temp extraction + robocopy).
- **`&` in the Windows user name** breaks cmd's batch quoting for the
  installed path (`%LOCALAPPDATA%` would contain `&`) — exotic, but the
  whole build pipeline is haunted by it because the dev repo lived in
  `Documents\D&D` until the 2026-10-04 rename; every path that crosses a
  cmd command line here is quoted by hand (see comments in
  `build_release.py` / `install.cmd`).
- Test ports: 8791 (stage smoke), 8793 (installer verify) — the launcher
  honors `STUDIO_PORT` and `STUDIO_NO_BROWSER` for headless runs.
