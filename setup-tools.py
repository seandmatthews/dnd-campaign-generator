#!/usr/bin/env python3
"""Bootstrap the third-party tool trees the mapping engines need.

  python setup-tools.py           clone + patch + install + build whatever is missing
  python setup-tools.py --check   report only; exit 1 if anything is missing
                                  (a pnpm-shaped tree, a drifted FMG pin or a
                                  stale vendor patch counts as missing)
  python setup-tools.py --force   re-apply patches and re-run installs

What it sets up (all under dnd-mapping/tools/, which is git-ignored):

  Fantasy-Map-Generator   upstream https://github.com/Azgaar/Fantasy-Map-Generator
                          at tag 1.153.1, pinned by commit SHA (FMG_REV — a
                          tag is movable server-side, the SHA is not), plus
                          OUR custom headless runner from
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

Stdlib only, list-form subprocess throughout (a rule kept from when the
parent path contained `&`),
and .cmd/.bat shims are run through cmd /c on Windows — the same discipline
as run-tests.py and studio/doctor.py. Idempotent: an existing, complete
tree is left alone unless --force — the one exception is a stale vendor
runner, which setup re-copies whenever the tracked copy disagrees
(ticket 60: a patch committed to vendor-patches/ used to change
regeneration behavior only on fresh clones).
"""

import argparse
import hashlib
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
# The tag's commit, resolved 2026-10-25 with `git ls-remote` (ticket 67):
# a tag is a movable pointer server-side, so the pin is the commit SHA and
# the tag is recorded beside it only for humans. Clones and existing
# checkouts verify against the SHA — a moved tag fails setup or --check
# instead of silently shipping a different world. (The stronger pattern
# settlemaker already used.)
FMG_REV = "bf32ac7230654361eedfc25985b4ecc3473889b1"  # tag 1.153.1
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
        # ticket 67: the tag is the human handle, the SHA is the pin — a
        # tag moved server-side between the pin and this clone must stop
        # setup, not seed a new machine with a different world.
        _verify_pinned_commit(d)
    _apply_headless_patch(d)
    (d / "src" / "generators").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PATCHES / "headless-export.test.ts",
                 d / "src" / "generators" / "headless-export.test.ts")
    # --ignore-scripts: FMG's only install-time script is "prepare":
    # simple-git-hooks (dev-only git hooks), and npm runs scripts through
    # cmd.exe — which cannot handle the `&` this repo's path carried until
    # the 2026-10-04 rename (the flag stays). Nothing the headless
    # pipeline needs is scripted:
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


# ------------------------------------------------- ticket 60: the pin, verified
#
# Marker files (.git, dist/index.js, vitest.mjs) prove a tree EXISTS; they
# never proved it is the tree we PINNED. Two findings could hide behind
# every green marker: a `git pull` moving the checkout off FMG_TAG (every
# same-seed regeneration silently becomes a different world), and a fix
# committed to vendor-patches/ that never re-applied to an existing clone
# (different machines, different worlds, same claimed version).
#
# Ticket 67 hardened the pin itself: FMG_TAG names a tag, and a tag is a
# movable pointer server-side — a checkout cloned after a tag move sits at
# a different commit that `git describe --tags` still reports by the
# tag's own name. So the pin is FMG_REV (the commit SHA) and the checkout
# must match it by SHA, not by version string.

def _checkout_commit(d):
    """HEAD's commit SHA for the clone at `d`: `git rev-parse HEAD`, falling
    back to reading .git/HEAD directly (a --branch tag clone sits on a
    detached HEAD whose file IS the raw SHA) so the check still works when
    git is missing or hung. None when neither resolves — including any tree
    whose .git does not even hold a HEAD file (the fake trees of the unit
    tests, or a leftover empty .git dir): running git there would let it
    walk up and answer with some OUTER repository's HEAD. List-form args,
    never a shell."""
    if not (d / ".git" / "HEAD").exists():
        return None
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(d),
                              capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().lower()
    except (OSError, subprocess.TimeoutExpired):
        pass  # no git / hung git: the raw HEAD file answers
    try:
        txt = (d / ".git" / "HEAD").read_text(encoding="utf-8").strip().lower()
    except (OSError, ValueError):
        return None
    hexdigits = "0123456789abcdef"
    if len(txt) == 40 and all(c in hexdigits for c in txt):
        return txt
    return None


def _verify_pinned_commit(d):
    """A fresh clone must sit at FMG_REV — the tag it was asked for may
    have moved server-side (ticket 67). sys.exit(1) with the evidence when
    it does not; returns the verified SHA."""
    head = _checkout_commit(d)
    if head != FMG_REV:
        print(f"  !! the clone is at commit {head or '<unresolvable>'}, "
              f"not the pinned commit {FMG_REV} (tag {FMG_TAG}) — the tag "
              "moved server-side; re-pin FMG_REV from the tag you trust")
        sys.exit(1)
    return head


def _checkout_version(d):
    """The FMG checkout's live version: `git describe --tags` (exact at the
    pin, tag-N-g<hash> once drifted), falling back to the clone's recorded
    package.json version; None when neither resolves. describe runs only
    when the tree itself holds .git — git searches parent directories for a
    repository, and a tmp cwd inside some OUTER repo must not leak its tags
    in as a fake answer. The studio's twin is engines.fmg_checkout_version
    (setup-tools stays stdlib-standalone, so the two are synced by hand).
    List-form args, never a shell."""
    if (d / ".git").exists():
        try:
            proc = subprocess.run(["git", "describe", "--tags"], cwd=str(d),
                                  capture_output=True, text=True, timeout=15)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass  # no git / hung git: the package record answers
    try:
        doc = json.loads((d / "package.json").read_text(encoding="utf-8"))
        v = doc.get("version")
        return v if isinstance(v, str) and v else None
    except (OSError, ValueError):
        return None


def _sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fmg_runner_stale(d):
    """True when the installed headless runner is not byte-identical to the
    tracked vendor-patches copy (sha256 mismatch). Before ticket 60 the
    runner was copied only on a fresh clone: once the file existed, content
    was never compared, so a committed fix reached no existing machine."""
    installed = d / "src" / "generators" / "headless-export.test.ts"
    tracked = PATCHES / "headless-export.test.ts"
    if not installed.is_file() or not tracked.is_file():
        return True  # absent on either side is not fresh (fmg_done names the marker)
    return _sha256(installed) != _sha256(tracked)


def fmg_freshness(d):
    """(drift_or_None, runner_stale) for an FMG tree whose marker files
    all say done. The pin is the commit SHA (ticket 67): a checkout can
    sit at a tag literally named FMG_TAG while being a different world
    (cloned after a server-side tag move), so HEAD must equal FMG_REV —
    the version compare only answers when no commit can be resolved
    (no usable git, no raw-SHA HEAD file). --check refuses on either
    finding; setup mode re-copies a stale runner (offline-safe, it is our
    own file) and prints the drift fix (a re-pin may need the network —
    advised, never attempted)."""
    live = _checkout_version(d)
    head = _checkout_commit(d)
    if head is not None:
        drift = None if head == FMG_REV else (
            f"checkout is at commit {head[:12]} "
            f"({live or 'an unresolvable version'}), not the pinned "
            f"commit {FMG_REV[:12]} (tag {FMG_TAG})")
    else:
        drift = None if live == FMG_TAG else (
            f"checkout is at {live or 'an unresolvable version'}, not the "
            f"pinned tag {FMG_TAG}")
    return drift, fmg_runner_stale(d)


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
     fmg_done, fmg_apply, fmg_freshness),
    ("settlemaker (cities + rasterizer)", "settlemaker",
     settle_done, settle_apply, None),
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
        print("\nthe studio still runs without the tools — maps/world "
              "generation needs them, and setup cannot proceed without "
              "git and node.")
        return 1
    print("prerequisites: git and node (with npm) present")

    incomplete = []
    for label, dirname, done, apply_step, freshness in STEPS:
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
            # ticket 60: marker files say "done"; the pin and the tracked
            # patches say "verified". A drifted tag or a stale vendor patch
            # lets same-seed regeneration silently produce a different
            # world while the provenance keeps claiming the pin.
            tag_drift, stale_runner = (freshness(tree) if freshness
                                       else (None, False))
            if args.check:
                if tag_drift:
                    print(f"[stale]    {label}: {tag_drift}")
                    print(f"           fix: git -C {tree} fetch origin "
                          f"&& git -C {tree} checkout {FMG_REV} "
                          f"(tag {FMG_TAG})")
                if stale_runner:
                    print(f"[stale]    {label}: the installed headless "
                          "runner does not match the tracked vendor-patches "
                          "copy (sha256 mismatch)")
                    print("           fix: python setup-tools.py  "
                          "(re-copies the runner)")
                if tag_drift or stale_runner:
                    incomplete.append(label)
                    continue
            elif stale_runner or tag_drift:
                if stale_runner:
                    (tree / "src" / "generators").mkdir(parents=True,
                                                        exist_ok=True)
                    shutil.copy2(PATCHES / "headless-export.test.ts",
                                 tree / "src" / "generators" /
                                 "headless-export.test.ts")
                    print(f"[refresh]  {label}: stale headless runner "
                          "re-copied from vendor-patches/")
                if tag_drift:
                    print(f"[drift]    {label}: {tag_drift}")
                    print(f"           fix: git -C {tree} fetch origin "
                          f"&& git -C {tree} checkout {FMG_REV} "
                          f"(tag {FMG_TAG})")
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
