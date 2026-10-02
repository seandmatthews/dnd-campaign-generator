#!/usr/bin/env python3
"""Bootstrap the third-party tool trees the mapping engines need.

  python setup-tools.py           clone + patch + install + build whatever is missing
  python setup-tools.py --check   report only; exit 1 if anything is missing
  python setup-tools.py --force   re-apply patches and re-run installs

What it sets up (all under dnd-mapping/tools/, which is git-ignored):

  Fantasy-Map-Generator   upstream https://github.com/Azgaar/Fantasy-Map-Generator
                          at tag 1.153.1, plus OUR custom headless runner from
                          dnd-mapping/vendor-patches/fmg/ (the clone is not
                          vanilla without those files)
  settlemaker             upstream https://github.com/barrulus/settlemaker at
                          d5cf3590 (v3.0.1), sharp pinned to 0.33.5 (the
                          tracked pin: render-scene-png.mjs refuses other
                          versions), dist/ built with its bundled TypeScript

Installs are plain `npm install` (npm ships with Node; both upstreams keep
a package-lock.json, so installs are reproducible). No pnpm anywhere —
pnpm's symlinked store was the source of the junction-breaks-when-moved
fragility the doctor used to have to repair. That contract is checked, not
assumed: --check (and setup) detect a pnpm-shaped tree (a .pnpm store,
.modules.yaml or pnpm-lock.yaml) even when every marker file is present,
and setup removes it for a clean npm reinstall. Engines invoke binaries as
`node node_modules/<pkg>/...` directly, so the package manager is needed
only here and in doctor --fix.

Stdlib only, list-form subprocess throughout (the parent path contains `&`),
and .cmd/.bat shims are run through cmd /c on Windows — the same discipline
as run-tests.py and studio/doctor.py. Idempotent: an existing, complete
tree is left alone unless --force. Network needed for the first run only.
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TOOLS = ROOT / "dnd-mapping" / "tools"
PATCHES = ROOT / "dnd-mapping" / "vendor-patches" / "fmg"

FMG_URL = "https://github.com/Azgaar/Fantasy-Map-Generator.git"
FMG_TAG = "1.153.1"
SETTLE_URL = "https://github.com/barrulus/settlemaker"
SETTLE_REV = "d5cf3590cf59e1d382110d25a6910f12507299e2"  # v3.0.1
SHARP_PIN = "0.33.5"  # must match PINNED_SHARP in dnd-mapping/scripts/render-scene-png.mjs


def _run(cmd, cwd=None, env_extra=None, check=True):
    """List-form subprocess; Windows .cmd/.bat shims go through cmd /c."""
    exe = shutil.which(cmd[0]) if not pathlib.Path(cmd[0]).exists() else cmd[0]
    if exe is None:
        print(f"  !! {cmd[0]} not found on PATH")
        return 127, ""
    if exe.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", exe, *cmd[1:]]
    else:
        cmd = [exe, *cmd[1:]]
    env = None
    if env_extra:
        env = {**os.environ, **env_extra}
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    out = (proc.stdout + proc.stderr).strip()
    if check and proc.returncode != 0:
        print(f"  !! failed (rc={proc.returncode}): {' '.join(cmd[:4])}...")
        print("  " + out[-1500:])
        sys.exit(1)
    return proc.returncode, out


def _have(name):
    return shutil.which(name) is not None


# ------------------------------------------------------- the npm-only contract

# pnpm leftovers that void the movability invariant (npm's flat
# node_modules has no junctions). Presence-only guardrails — marker files
# like node_modules/sharp/package.json — cannot see these: a pnpm install
# has every marker AND symlinks everything into node_modules/.pnpm.
PNPM_ARTIFACTS = ("node_modules/.pnpm", "node_modules/.modules.yaml",
                  "pnpm-lock.yaml")


def pnpm_drift(tree):
    """The pnpm artifacts found in `tree` ([] when npm-shaped). A cheap
    presence check — no npm run, no lockfile parse."""
    found = []
    for rel in PNPM_ARTIFACTS:
        p = tree / rel
        if p.exists() or p.is_symlink():
            found.append(rel)
    return found


def remove_pnpm_install(tree):
    """Delete a pnpm-shaped install so the next `npm install` starts from a
    clean slate (npm over a symlinked store keeps the junctions in place —
    found 2026-10-02: both tool trees had quietly drifted to pnpm)."""
    nm = tree / "node_modules"
    if nm.is_symlink() or nm.is_file():
        nm.unlink()
    elif nm.is_dir():
        shutil.rmtree(nm)
    lock = tree / "pnpm-lock.yaml"
    if lock.is_symlink() or lock.exists():
        lock.unlink()


# ------------------------------------------------------------- the steps

def fmg_done():
    d = TOOLS / "Fantasy-Map-Generator"
    return (d / ".git").is_dir() \
        and (d / "src" / "generators" / "headless-export.test.ts").is_file() \
        and (d / "node_modules" / "vitest" / "vitest.mjs").is_file()


def fmg_apply():
    d = TOOLS / "Fantasy-Map-Generator"
    if d.exists() and not (d / ".git").is_dir():
        print(f"  !! {d} exists but is not a git clone — move it aside first")
        sys.exit(1)
    if not (d / ".git").is_dir():
        print(f"  cloning Fantasy-Map-Generator at tag {FMG_TAG} ...")
        _run(["git", "clone", "--depth", "1", "--branch", FMG_TAG, FMG_URL, str(d)])
    _apply_headless_patch(d)
    (d / "src" / "generators").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PATCHES / "headless-export.test.ts",
                 d / "src" / "generators" / "headless-export.test.ts")
    # --ignore-scripts: FMG's only install-time script is "prepare":
    # simple-git-hooks (dev-only git hooks), and npm runs scripts through
    # cmd.exe — which cannot handle the `&` in this repo's default path
    # (Documents\D&D). Nothing the headless pipeline needs is scripted:
    # esbuild/rollup ship platform binaries as optional deps.
    print("  npm install (this can take a few minutes) ...")
    _run(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
         cwd=d)


def _apply_headless_patch(d):
    """The one vendor patch (package.json: +d3-quadtree). Idempotent: the
    content check, not `git apply`'s exit code, decides — --force re-runs
    land here with the patch already applied."""
    pkg = d / "package.json"
    if "d3-quadtree" in pkg.read_text(encoding="utf-8"):
        return
    print("  applying the vendor patch (package.json: +d3-quadtree) ...")
    _run(["git", "apply", str(PATCHES / "package-json.patch")], cwd=d)


def settle_done():
    d = TOOLS / "settlemaker"
    return (d / ".git").is_dir() \
        and (d / "dist" / "index.js").is_file() \
        and (d / "node_modules" / "sharp" / "package.json").is_file()


def settle_apply():
    d = TOOLS / "settlemaker"
    if d.exists() and not (d / ".git").is_dir():
        print(f"  !! {d} exists but is not a git clone — move it aside first")
        sys.exit(1)
    if not (d / ".git").is_dir():
        print(f"  cloning settlemaker (v3.0.1) ...")
        _run(["git", "clone", SETTLE_URL, str(d)])
        _run(["git", "checkout", SETTLE_REV], cwd=d)
    pkg = d / "package.json"
    doc = json.loads(pkg.read_text(encoding="utf-8"))
    deps = doc.get("dependencies", {})
    if deps.get("sharp") != SHARP_PIN:
        print(f"  pinning sharp to {SHARP_PIN} in settlemaker's package.json ...")
        deps["sharp"] = SHARP_PIN
        pkg.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print("  npm install (this can take a few minutes) ...")
    _run(["npm", "install", "--no-audit", "--no-fund"], cwd=d)
    print("  building dist/ with the bundled TypeScript ...")
    _run(["node", "node_modules/typescript/bin/tsc"], cwd=d)


STEPS = [
    ("Fantasy-Map-Generator (world gen)", "Fantasy-Map-Generator",
     fmg_done, fmg_apply),
    ("settlemaker (cities + rasterizer)", "settlemaker",
     settle_done, settle_apply),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report readiness only; exit 1 if anything is missing "
                         "(a pnpm-shaped tool tree counts as missing)")
    ap.add_argument("--force", action="store_true",
                    help="re-apply patches and re-run installs even if a step looks done")
    args = ap.parse_args(argv)

    print(f"setup-tools: tools live under {TOOLS}\n")

    missing_prereqs = [n for n in ("git", "node") if not _have(n)]
    if missing_prereqs:
        print("missing prerequisites: " + ", ".join(missing_prereqs))
        for n in missing_prereqs:
            if n == "node":
                print("  fix: https://nodejs.org  (LTS — npm comes with it)")
            elif n == "git":
                print("  fix: https://git-scm.com/download")
        print("\nthe studio still runs without the tools — maps/world generation needs them.")
        return 1 if args.check else 1
    print("prerequisites: git and node (with npm) present")

    incomplete = []
    for label, dirname, done, apply_step in STEPS:
        tree = TOOLS / dirname
        drift = pnpm_drift(tree)
        if drift:
            # markers can all be present and the tree still pnpm-shaped:
            # the contract is checked, not assumed
            if args.check:
                print(f"[pnpm]     {label}: installed with pnpm "
                      f"({', '.join(drift)}) — the npm-only movability "
                      f"contract is void")
                incomplete.append(label)
                continue
            print(f"[reinstall] {label}: pnpm install detected "
                  f"({', '.join(drift)}) — removing it for a clean npm install")
            remove_pnpm_install(tree)
        if done():
            print(f"[done]     {label}")
            if args.force and not args.check:
                apply_step()
                print(f"[refreshed] {label}")
            continue
        if args.check:
            print(f"[missing]  {label}")
            incomplete.append(label)
            continue
        print(f"[setting up] {label}")
        apply_step()
        print(f"[ready]    {label}" if done() else f"[warning]  {label} still incomplete")

    if args.check:
        if incomplete:
            print("\nnot ready — run:  python setup-tools.py")
            return 1
        print("\nall tool trees ready")
        return 0

    print("\nnext: python studio/doctor.py  (verifies every engine end to end)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
