#!/usr/bin/env python3
"""Bootstrap the third-party tool trees the mapping engines need.

  python setup-tools.py           clone + patch + install + build whatever is missing
  python setup-tools.py --check   report only; exit 1 if anything is missing
  python setup-tools.py --force   re-apply patches and re-run installs

What it sets up (all under dnd-mapping/tools/, which is git-ignored):

  Fantasy-Map-Generator   upstream https://github.com/Azgaar/Fantasy-Map-Generator
                          at tag 1.153.1, plus OUR custom headless runner from
                          dnd-mapping/vendor-patches/fmg/ (the clone is not
                          vanilla without those four files)
  settlemaker             upstream https://github.com/barrulus/settlemaker at
                          d5cf3590 (v3.0.1), sharp pinned to 0.33.5 (the
                          tracked pin: render-scene-png.mjs refuses other
                          versions), dist/ built with its bundled TypeScript

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


# ------------------------------------------------------------- the steps

def fmg_done():
    d = TOOLS / "Fantasy-Map-Generator"
    return (d / ".git").is_dir() \
        and (d / "src" / "generators" / "headless-export.test.ts").is_file() \
        and (d / "node_modules" / "vitest" / "vitest.mjs").is_file()


def fmg_apply(check):
    d = TOOLS / "Fantasy-Map-Generator"
    if d.exists() and not (d / ".git").is_dir():
        print(f"  !! {d} exists but is not a git clone — move it aside first")
        sys.exit(1)
    if not (d / ".git").is_dir():
        print(f"  cloning Fantasy-Map-Generator at tag {FMG_TAG} ...")
        _run(["git", "clone", "--depth", "1", "--branch", FMG_TAG, FMG_URL, str(d)])
    print("  applying the vendor patches (headless runner) ...")
    _run(["git", "apply", str(PATCHES / "package-json.patch")], cwd=d)
    for name in ("npmrc", "pnpm-workspace.yaml"):
        shutil.copy2(PATCHES / name, d / name)
    (d / "src" / "generators").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PATCHES / "headless-export.test.ts",
                 d / "src" / "generators" / "headless-export.test.ts")
    print("  pnpm install (this can take a few minutes) ...")
    _run(["pnpm", "install"], cwd=d, env_extra={"CI": "true"})


def settle_done():
    d = TOOLS / "settlemaker"
    return (d / ".git").is_dir() \
        and (d / "dist" / "index.js").is_file() \
        and (d / "node_modules" / "sharp" / "package.json").is_file()


def settle_apply(check):
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
    print("  pnpm install (this can take a few minutes) ...")
    _run(["pnpm", "install"], cwd=d, env_extra={"CI": "true"})
    print("  building dist/ with the bundled TypeScript ...")
    _run(["node", "node_modules/typescript/bin/tsc"], cwd=d)


STEPS = [
    ("Fantasy-Map-Generator (world gen)", fmg_done, fmg_apply),
    ("settlemaker (cities + rasterizer)", settle_done, settle_apply),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report readiness only; exit 1 if anything is missing")
    ap.add_argument("--force", action="store_true",
                    help="re-apply patches and re-run installs even if a step looks done")
    args = ap.parse_args()

    print(f"setup-tools: tools live under {TOOLS}\n")

    missing_prereqs = [n for n in ("git", "node", "pnpm") if not _have(n)]
    if missing_prereqs:
        print("missing prerequisites: " + ", ".join(missing_prereqs))
        for n in missing_prereqs:
            if n == "node":
                print("  fix: https://nodejs.org  (LTS)")
            elif n == "pnpm":
                print("  fix: winget install pnpm.pnpm   (or: npm install -g pnpm)")
            elif n == "git":
                print("  fix: https://git-scm.com/download")
        print("\nthe studio still runs without the tools — maps/world generation needs them.")
        return 1 if args.check else 1
    print("prerequisites: git, node, pnpm all present")

    incomplete = []
    for label, done, apply_step in STEPS:
        if done():
            print(f"[done]     {label}")
            if args.force and not args.check:
                apply_step(args.check)
                print(f"[refreshed] {label}")
            continue
        if args.check:
            print(f"[missing]  {label}")
            incomplete.append(label)
            continue
        print(f"[setting up] {label}")
        apply_step(args.check)
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
