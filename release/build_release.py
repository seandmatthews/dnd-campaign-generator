#!/usr/bin/env python3
r"""Build the one-file Windows release of Campaign Studio.

Output: release/dist/CampaignStudio-<version>.exe — a single
self-extracting installer (7-Zip's 7zSD stub + 7z payload + config,
concatenated). Double-clicking it extracts the payload to a temp dir and
runs install.cmd there, which mirrors the app into
"%LOCALAPPDATA%\Campaign Studio" (robocopy /MIR, but never touching the
campaign-data/ data dir) and starts launch.cmd from there; the browser
opens. Re-running a same-version exe just relaunches; a new version
refreshes the program in place and keeps the campaign data. The target
machine needs nothing installed:

  runtime/python      embeddable CPython 3.11 + requirements.txt installed
  runtime/node        official node.exe (the FMG tool tree wants >= 24)
  studio/, agentic-simulator/, dnd-mapping/, verification/
                      source, sibling layout identical to the repo (the
                      code hard-resolves these paths off the bundle root)
  dnd-mapping/tools/  FMG + settlemaker WITH node_modules — prebuilt here,
                      never rebuilt on the target (no npm at runtime)
  llm.env             LLM key resolved exactly the way the studio resolves
                      it on this machine (repo llm.env, else the ZCode
                      provider config) — drop with --no-key

Beside the exe the build stages BEFORE-YOU-RUN.txt (ticket 68): the
SmartScreen and antivirus guidance, readable BEFORE the first
double-click — README.txt has the same material but travels INSIDE the
exe, where the friend cannot open it yet. Share both files.

Usage:  python release/build_release.py [--no-key] [--no-smoke]
                                        [--campaigns] [--keep-work]
Downloads (CPython embeddable, node, 7-Zip tools) are cached in
release/cache and verified against the SHA256s recorded in CHECKSUMS —
before a cached copy is reused and after every fresh fetch (ticket 67).
Staging and payload live in release/work. Stdlib only.
"""

import argparse
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RELEASE = REPO / "release"
CACHE = RELEASE / "cache"
WORK = RELEASE / "work"
DIST = RELEASE / "dist"
STAGE = WORK / "bundle"
TEMPLATES = RELEASE / "templates"

PY_VERSION = "3.11.9"
PY_MM = "311"
# Node pinned to an EXACT release (ticket 67): the builder used to take
# the live index's latest v24 on every build, so two exes with adjacent
# stamps could carry different node runtimes with no trace. v24.22.1 was
# the current v24 LTS when the pin landed (the FMG tool tree wants >= 24);
# bump by hand, pin and CHECKSUMS entry together.
NODE_VERSION = "v24.22.1"
# The avtt companion (dcg-avtt-integration) rides in the bundle at a
# pinned RELEASE TAG — tags only, never main (the companion's own ticket
# 07 discipline; design doc §4). The stamp records the tag + its
# ATLAS_PIN so an installed copy can say what it carries.
COMPANION_REPO = "https://github.com/seandmatthews/dcg-avtt-integration.git"
COMPANION_TAG = "v0.2.0"  # the 0.7.1 Atlas pin bump (companion ticket 08) —
# v0.1.0 pinned Atlas 0.6.0 and would warn + _inbox-degrade on the table
# machine's 0.7.1 vaults; ticket 53's lean was always to follow this bump
# The tag's COMMIT, resolved 2026-10-25 with `git ls-remote` (ticket 67):
# "tags are immutable" was wrong — a tag is a movable pointer server-side,
# so the pin is the commit SHA and the tag rides beside it for humans.
# The build cache must sit at the SHA and every fresh clone verifies
# against it; a moved tag fails the build instead of silently shipping a
# different companion.
COMPANION_REV = "2669dd233ad434e2c7c5dadc133ac9b1e1838ef2"  # tag v0.2.0
COMPANION_CACHE = CACHE / "avtt-integration"
# Download pins (ticket 67): SHA256 of the exact bytes each cached
# artifact must carry, verified before the cache is reused and after
# every fetch. A mismatch deletes the cached copy, re-downloads ONCE, and
# fails the build if the bytes still disagree — a corrupted cache was
# trusted forever before, and a rotated upstream (get-pip.py rotates with
# pip releases) must force a re-pin, not float past silently. Resolved
# from the live sources 2026-10-25; bump pin+hash together. The LZMA SDK
# package carries no pin: it is a fallback chain of three versions (first
# one that answers), and only the 7zSD.sfx extracted FROM it ships.
CHECKSUMS = {
    f"python-{PY_VERSION}-embed.zip": (
        f"https://www.python.org/ftp/python/{PY_VERSION}/"
        f"python-{PY_VERSION}-embed-amd64.zip",
        "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"),
    "get-pip.py": (
        "https://bootstrap.pypa.io/get-pip.py",
        "f4448a01e3ab2914c65f4ca59d4cacba6b2dbde19a483fbac46587f8bbaff424"),
    f"node-{NODE_VERSION}-win-x64.zip": (
        f"https://nodejs.org/dist/{NODE_VERSION}/"
        f"node-{NODE_VERSION}-win-x64.zip",
        "d2046d28c2c3dcd211ac5145c0bf30e2047ef9dfb306693c8c6e2eccd8b95fa5"),
    "7zr.exe": (
        "https://www.7-zip.org/a/7zr.exe",
        "256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282"),
}
# The installer SFX module (bin/7zSD.sfx) ships in the LZMA SDK package
# (the -extra packages stopped carrying it). Igor's minimal module extracts
# to a TEMP dir and runs RunProgram from there — hence the install.cmd step.
LZMA_SDK_VERSIONS = ["2603", "2501", "2409"]
APP_VERSION = re.search(
    r'^version\s*=\s*"([^"]+)"', (REPO / "pyproject.toml").read_text(encoding="utf-8"), re.M
).group(1)
PORT_STAGE, PORT_SFX, PORT_UPDATE = 8791, 8793, 8795
# Second granularity on purpose: install.cmd's fast path compares VERSION
# byte-for-byte, and two rebuilds within one minute at %H%M granularity
# produced identical bytes — the second exe then skipped mirroring a
# CHANGED payload. With seconds, only the same exe (or a rebuild inside
# the same second, payload and stamp both identical) takes the fast path.
STAMP_FMT = "%Y%m%d%H%M%S"

# Never copied into the bundle, at any depth (name-based, per copy call).
IGNORE_BASE = [".git", ".venv", "__pycache__", "*.pyc", ".pytest_cache"]

# The tracked templates build_stage copies INTO the payload — the files a
# --sfx-only rebuild's cached payload.7z can silently predate (ticket 68):
# the sfx iteration loop exists to iterate on exactly these, and a stale
# cache turns the verify green over a payload that lacks the edit.
TEMPLATE_NAMES = ["install.cmd", "launch.cmd", "export.cmd", "README.txt"]


def stale_templates(payload):
    """Tracked templates modified after the cached payload was packed —
    the ones an --sfx-only rebuild would silently leave untested."""
    packed = payload.stat().st_mtime
    return [name for name in TEMPLATE_NAMES
            if (TEMPLATES / name).is_file()
            and (TEMPLATES / name).stat().st_mtime > packed]


def step(msg):
    print(f"\n=== {msg} ===", flush=True)


def run(cmd, **kw):
    """subprocess.run with list args, echoing failures verbosely."""
    r = subprocess.run([str(c) for c in cmd], **kw)
    if r.returncode != 0:
        raise SystemExit(f"FAILED ({r.returncode}): {' '.join(str(c) for c in cmd)}")
    return r


def _sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def download(url, dest, sha256=None):
    """Fetch url into dest (the cache). With a recorded sha256 the bytes
    are verified before REUSE and after every fetch (ticket 67 — a
    corrupted cached zip used to be trusted forever): one mismatch
    deletes the copy and re-downloads once; a second mismatch fails the
    build, because the pin is the provenance. Without a pin (the LZMA
    SDK fallback chain) the old trust-the-cache behavior stands."""
    if dest.exists():
        if sha256 is not None and _sha256(dest) != sha256:
            print(f"  cached  {dest.name} failed its sha256 check — "
                  "re-downloading once", flush=True)
            dest.unlink()
        else:
            print(f"  cached  {dest.name} ({dest.stat().st_size // 1024} KB)", flush=True)
            return dest
    print(f"  fetch   {url}", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 campaign-studio-build"})
    with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    if sha256 is not None and _sha256(tmp) != sha256:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"FAILED: the fresh {dest.name} does not match its "
                         "recorded sha256 — upstream rotated or the "
                         "download is broken; re-pin CHECKSUMS or retry")
    tmp.rename(dest)
    return dest


def ensure_cache():
    step("cache: python embeddable, get-pip, node, 7-zip sfx tools "
         "(every artifact sha256-verified against CHECKSUMS, ticket 67)")
    for name, (url, sha) in CHECKSUMS.items():
        download(url, CACHE / name, sha)

    sevenzr = CACHE / "7zr.exe"
    sdk = None
    for ver in LZMA_SDK_VERSIONS:
        try:
            sdk = download(f"https://www.7-zip.org/a/lzma{ver}.7z",
                           CACHE / f"lzma{ver}.7z")
            break
        except OSError:
            continue
    if sdk is None:
        raise SystemExit("could not download the LZMA SDK package (7zSD.sfx source)")
    sfx = CACHE / "7zSD.sfx"
    if not sfx.exists():
        run([sevenzr, "e", f"-o{CACHE}", "-y", sdk, "bin/7zSD.sfx"],
            stdout=subprocess.DEVNULL)
    if not sfx.exists():
        raise SystemExit("7zSD.sfx did not extract from the LZMA SDK package")
    if sfx.read_bytes().find(b"asInvoker") < 0:
        embed_as_invoker_manifest(sfx)
    return sevenzr, sfx, CACHE / f"node-{NODE_VERSION}-win-x64.zip"


def _clear_readonly_and_retry(func, path, exc_info):
    """rmtree onerror for git-owned trees: pack files carry the read-only
    attribute, and os.unlink refuses them until it is cleared."""
    import stat
    os.chmod(path, stat.S_IWRITE)
    func(path)


def copy_tree(src, dst, extra_ignores=()):
    shutil.copytree(src, dst,
                    ignore=shutil.ignore_patterns(*IGNORE_BASE, *extra_ignores))


def ensure_companion():
    """The pinned companion clone in the build cache: cloned once, reused
    while the cache sits exactly at the pin, re-cloned when the pin moves
    (the pin is COMPANION_REV, the tag's commit — a tag is a movable
    pointer server-side, so the old "tags are immutable" was wrong and the
    local `rev-parse <tag>^{commit}` check could bless a moved tag it had
    itself cloned; ticket 67) — same hygiene as setup-tools.py: an
    existing dir that is not a git clone is refused, never clobbered."""
    step(f"companion: dcg-avtt-integration pinned at {COMPANION_TAG} "
         f"({COMPANION_REV[:12]})")
    if COMPANION_CACHE.exists():
        head = subprocess.run(["git", "-C", str(COMPANION_CACHE),
                               "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        if head.returncode == 0 \
                and head.stdout.strip().lower() == COMPANION_REV:
            print("  cache is at the pin")
            return
        if head.returncode != 0:
            raise SystemExit(f"FAILED: {COMPANION_CACHE} exists but is not a "
                             "git clone — move it aside first")
        # git marks pack files read-only; a plain rmtree dies on them
        # with WinError 5 (seen for real on the v0.1.0→v0.2.0 re-clone).
        shutil.rmtree(COMPANION_CACHE, onerror=_clear_readonly_and_retry)
    run(["git", "clone", "--depth", "1", "--branch", COMPANION_TAG,
         COMPANION_REPO, str(COMPANION_CACHE)])
    # ticket 67: the tag named here may have moved server-side between the
    # pin and this clone — HEAD must be the pinned commit, by SHA.
    head = subprocess.run(["git", "-C", str(COMPANION_CACHE),
                           "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode != 0 \
            or head.stdout.strip().lower() != COMPANION_REV:
        raise SystemExit(
            f"FAILED: the fresh companion clone is at "
            f"{head.stdout.strip() or '<unresolvable>'}, not the pinned "
            f"commit {COMPANION_REV} (tag {COMPANION_TAG}) — the tag moved "
            "server-side; re-pin COMPANION_REV from the tag you trust")


def build_stage(node_zip):
    step("stage: copy sources (preserving the repo's sibling layout)")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    copy_tree(REPO / "studio", STAGE / "studio", ("logs", "tests"))
    copy_tree(REPO / "agentic-simulator", STAGE / "agentic-simulator",
              ("tests",))
    copy_tree(REPO / "dnd-mapping", STAGE / "dnd-mapping", ("docs",))
    worlds = STAGE / "dnd-mapping" / "worlds"
    # tracked generation records only: runtime data lives under
    # campaign-data/ (ticket 35) and is staged fresh below
    shutil.rmtree(worlds / "sites", ignore_errors=True)
    if (worlds / "candidates").is_dir():
        for p in (worlds / "candidates").iterdir():  # build-machine scratch
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    (worlds / "raw").mkdir(exist_ok=True)
    copy_tree(REPO / "verification", STAGE / "verification")
    step("stage: avtt companion (pinned release: source + LICENSE, no .git)")
    # IGNORE_BASE strips .git — the bundle carries the pinned snapshot,
    # not a working copy (design doc §4: source + AGPL LICENSE intact,
    # the settlemaker precedent).
    copy_tree(COMPANION_CACHE, STAGE / "avtt-integration")
    # /tutorial reads <bundle root>/docs/for-new-gms/TUTORIAL.md — stage it
    # exactly there so the help modal's link works on installed copies too.
    # ATLAS.md travels with it (ticket 46): the at-the-table tutorial's
    # audience is installed copies.
    (STAGE / "docs" / "for-new-gms").mkdir(parents=True)
    shutil.copy2(REPO / "docs" / "for-new-gms" / "TUTORIAL.md",
                 STAGE / "docs" / "for-new-gms" / "TUTORIAL.md")
    shutil.copy2(REPO / "docs" / "for-new-gms" / "ATLAS.md",
                 STAGE / "docs" / "for-new-gms" / "ATLAS.md")
    shutil.copy2(REPO / "llm.env.example", STAGE / "llm.env.example")

    # the data home: seeded empty (launch.cmd points CAMPAIGN_DATA_ROOT
    # here; install.cmd keeps it across updates and seeds it on first
    # install). --campaigns copies your campaigns into it.
    (STAGE / "campaign-data" / "campaigns" / ".locks").mkdir(parents=True)
    if ARGS.campaigns:
        for camp in (REPO / "campaign-data" / "campaigns").iterdir():
            if camp.name != ".locks" and camp.is_dir():
                copy_tree(camp,
                          STAGE / "campaign-data" / "campaigns" / camp.name)

    step("stage: bundled python (embeddable + pip + requirements.txt)")
    pydir = STAGE / "runtime" / "python"
    with zipfile.ZipFile(CACHE / f"python-{PY_VERSION}-embed.zip") as z:
        z.extractall(pydir)
    (pydir / f"python{PY_MM}._pth").write_text(
        f"python{PY_MM}.zip\n.\nLib/site-packages\nimport site\n", encoding="ascii")
    py = pydir / "python.exe"
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    pip_log = (WORK / "pip.log").open("w")
    try:
        run([py, CACHE / "get-pip.py", "--no-warn-script-location"],
            stdout=pip_log, stderr=subprocess.STDOUT, env=env)
        run([py, "-m", "pip", "install", "--no-warn-script-location",
             "-r", REPO / "studio" / "requirements.txt"],
            stdout=pip_log, stderr=subprocess.STDOUT, env=env)
    finally:
        pip_log.close()

    step("stage: bundled node")
    with zipfile.ZipFile(node_zip) as z:
        info = next(n for n in z.namelist() if n.endswith("/node.exe"))
        (STAGE / "runtime" / "node").mkdir(parents=True, exist_ok=True)
        (STAGE / "runtime" / "node" / "node.exe").write_bytes(z.read(info))

    step("stage: llm.env")
    if ARGS.no_key:
        print("  skipped (--no-key): the bundle's sim/worldgen features "
              "will ask for a key")
    else:
        sys.path.insert(0, str(REPO / "studio"))
        from llm_client import resolve_config  # the studio's own resolution
        cfg = resolve_config()
        if not cfg["key"]:
            print("  WARNING: no LLM key resolvable on this machine — "
                  "bundling WITHOUT llm.env")
        else:
            base = cfg["url"].rsplit("/chat/completions", 1)[0]
            (STAGE / "llm.env").write_text(
                "# Campaign Studio LLM settings (baked in at build time).\n"
                f"LLM_BASE_URL={base}\n"
                f"LLM_API_KEY={cfg['key']}\n"
                f"LLM_MODEL={cfg['model']}\n", encoding="utf-8")
            print(f"  key ...{cfg['key'][-4:]}  base {base}  model {cfg['model']}")

    shutil.copy2(TEMPLATES / "launch.cmd", STAGE / "launch.cmd")
    shutil.copy2(TEMPLATES / "install.cmd", STAGE / "install.cmd")
    # the friend backup path (ticket 58): export.cmd beside launch.cmd,
    # driving the bundled python — README.txt's "Backing up" names it
    shutil.copy2(TEMPLATES / "export.cmd", STAGE / "export.cmd")
    shutil.copy2(TEMPLATES / "README.txt", STAGE / "README.txt")
    # Build stamp included: install.cmd's fast path compares this file
    # byte-for-byte, so every rebuild is an update — no version bump to
    # remember when re-sharing. STAMP_FMT carries seconds; see its note.
    # The stamp also names the bundled companion's tag + its ATLAS_PIN
    # (ticket 53) — read from the staged companion by the bundled python,
    # so the stamp cannot drift from what actually shipped.
    r = run([py, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); "
             "from avtt._version import VERSION, ATLAS_PIN; "
             "print(VERSION, ATLAS_PIN)",
             str(STAGE / "avtt-integration" / "src")],
            capture_output=True, text=True)
    try:
        companion_version, atlas_pin = r.stdout.split()
    except ValueError:
        raise SystemExit(f"FAILED: could not read the companion's version "
                         f"report: {r.stdout!r} / {r.stderr!r}")
    if f"v{companion_version}" != COMPANION_TAG:
        raise SystemExit(f"FAILED: the staged companion reports "
                         f"{companion_version} but the pin is {COMPANION_TAG}")
    stamp = time.strftime(STAMP_FMT)
    (STAGE / "VERSION").write_text(
        f"{APP_VERSION}+{stamp}+{COMPANION_TAG}+atlas-{atlas_pin}",
        encoding="ascii")


def wait_http(url, timeout_s):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return
        except OSError as e:
            last = e
        time.sleep(1)
    raise SystemExit(f"FAILED: {url} never came up ({last})")


def kill_port(port):
    """Kill whatever process is listening on the port (taskkill tree)."""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    pids = {ln.split()[-1] for ln in out.splitlines()
            if f":{port}" in ln and "LISTENING" in ln}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
    return pids


def smoke(port):
    step(f"smoke: boot the staged bundle on port {port}")
    py = STAGE / "runtime" / "python" / "python.exe"
    node = STAGE / "runtime" / "node" / "node.exe"
    run([py, "-c", "import fastapi, uvicorn, yaml, httpx; print('python deps ok')"])
    run([node, "--version"])
    env = {**os.environ, "STUDIO_PORT": str(port), "STUDIO_NO_BROWSER": "1"}
    log = (WORK / "smoke.log").open("w")
    # cmd /c mangles quoted absolute paths containing "&" — the repo's own
    # path did until the 2026-10-04 rename, and users' paths still can.
    # Invoke launch.cmd by RELATIVE name with the stage as cwd — no
    # absolute path ever reaches cmd's parser.
    proc = subprocess.Popen(["cmd", "/c", "launch.cmd"], env=env,
                            cwd=STAGE, stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_http(f"http://127.0.0.1:{port}/", 60)
        wait_http(f"http://127.0.0.1:{port}/tutorial", 30)
        wait_http(f"http://127.0.0.1:{port}/atlas", 30)
        print("  /, /tutorial and /atlas -> 200; killing server")
    finally:
        kill_port(port)
        if proc.poll() is None:
            proc.kill()
        log.close()
    if kill_port(port):
        raise SystemExit(f"FAILED: port {port} still busy after kill")
    step("smoke: the friend backup path (export.cmd, no system python)")
    # relative name + stage cwd, the launch.cmd rule: no absolute path ever
    # crosses cmd's parser (paths with "&" break its quoting)
    r = subprocess.run(["cmd", "/c", "export.cmd", "--help"], cwd=STAGE,
                       env={**os.environ, "STUDIO_NO_BROWSER": "1"},
                       capture_output=True, text=True)
    if r.returncode != 0 or "export_campaign.py" not in (r.stdout + r.stderr):
        raise SystemExit(f"FAILED: export.cmd did not run on the bundled "
                         f"python (rc={r.returncode}): "
                         f"{(r.stdout + r.stderr)[-400:]}")
    print("  export.cmd ran on the bundled python (ticket 58)")
    step("smoke: the avtt companion rides the bundle (ticket 53)")
    if not (STAGE / "avtt-integration" / "LICENSE").is_file():
        raise SystemExit("FAILED: the avtt companion's LICENSE did not land "
                         "in the stage (the bundle ships license + source — "
                         "the settlemaker precedent)")
    run([py, str(STAGE / "avtt-integration" / "src" / "avtt" / "publish.py"),
         "--help"], stdout=subprocess.DEVNULL)
    print("  LICENSE present; publish.py runs on the bundled python")
    step("smoke: clean test artifacts out of the stage before packing")
    # the smoke boot ran with CAMPAIGN_DATA_ROOT pointed at the stage's
    # campaign-data/ (launch.cmd) — reset it wholesale to the seeded state
    shutil.rmtree(STAGE / "campaign-data", ignore_errors=True)
    (STAGE / "campaign-data" / "campaigns" / ".locks").mkdir(parents=True)
    if ARGS.campaigns:  # smoke traffic may have touched campaign dirs
        for camp in (REPO / "campaign-data" / "campaigns").iterdir():
            if camp.name != ".locks" and camp.is_dir():
                dst = STAGE / "campaign-data" / "campaigns" / camp.name
                shutil.rmtree(dst, ignore_errors=True)
                copy_tree(camp, dst)


def kill_sfx_tree(proc, exe_name, port):
    """/T tree-kill on the stub takes the whole chain down — install.cmd,
    the `start`ed launch.cmd, the python server. Killing only the stub
    leaves launch.cmd parked at its final `pause` with its cwd inside
    the test install, which then can't be deleted (winerror 183 on the
    next verify run)."""
    if proc is not None and proc.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    kill_port(port)
    subprocess.run(["taskkill", "/F", "/T", "/IM", exe_name],
                   capture_output=True)


def bundle_version():
    """The payload's full VERSION string (build_stage wrote it into the
    stage). Names the artifact (ticket 57): pyproject's 0.1.0 has not
    moved, so naming the exe after APP_VERSION alone shipped every
    build under the same filename and an old download was silently,
    invisibly old. With the full stamp (seconds included) each build is
    a distinct, legible file — the friend reporting a bug can quote the
    exe name."""
    try:
        return (STAGE / "VERSION").read_text(encoding="ascii").strip()
    except OSError:
        raise SystemExit(f"FAILED: {STAGE / 'VERSION'} is missing — the "
                         "exe is named from it (build the stage first, or "
                         "--sfx-only on a kept release/work)")


def pack(sevenzr):
    step("pack: payload.7z (LZMA)")
    payload = WORK / "payload.7z"
    payload.unlink(missing_ok=True)
    run([sevenzr, "a", "-t7z", "-mx=7", "-xr!__pycache__", "-xr!*.pyc",
         str(payload), "."], cwd=STAGE, stdout=subprocess.DEVNULL)
    print(f"  payload: {payload.stat().st_size // (1024 * 1024)} MB")
    return payload


AS_INVOKER_MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges xmlns="urn:schemas-microsoft-com:asm.v3">
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
</assembly>
"""


def embed_as_invoker_manifest(image):
    """The 7zSD stub is a 32-bit exe with no manifest, so UAC's Installer
    Detection heuristic elevates it (double-click would show a UAC prompt).
    Any embedded manifest disables the heuristic — add an explicit
    asInvoker one (RT_MANIFEST, id 1). MUST run on the bare module, before
    the 7z payload is appended: the resource updater rewrites the image
    and silently drops any overlay (which is where the payload lives)."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.BeginUpdateResourceW.restype = ctypes.c_void_p
    k32.BeginUpdateResourceW.argtypes = [ctypes.c_wchar_p, ctypes.c_bool]
    k32.UpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_ushort,
                                    ctypes.c_char_p, ctypes.c_uint]
    k32.EndUpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    h = k32.BeginUpdateResourceW(str(image), False)
    if not h:
        raise OSError(ctypes.get_last_error(), "BeginUpdateResourceW")
    data = AS_INVOKER_MANIFEST.encode("utf-8")
    if not k32.UpdateResourceW(h, ctypes.c_void_p(24), ctypes.c_void_p(1),
                               0, data, len(data)):
        k32.EndUpdateResourceW(h, True)
        raise OSError(ctypes.get_last_error(), "UpdateResourceW")
    if not k32.EndUpdateResourceW(h, False):
        raise OSError(ctypes.get_last_error(), "EndUpdateResourceW")


def make_sfx(payload, sfx_module):
    step("sfx: concatenate module + config + payload")
    config = (
        ";!@Install@!UTF-8!\r\n"
        f'Title="Campaign Studio {APP_VERSION}"\r\n'
        # Bare filename: the module prefixes RunProgram with ".\" and runs
        # it with cwd set to the extraction dir, so "cmd /c ..." would seek
        # an exe named "cmd" in the temp dir. CreateProcess routes a .cmd
        # through the command interpreter on its own.
        'RunProgram="install.cmd"\r\n'
        ";!@InstallEnd@!\r\n"
    )
    DIST.mkdir(exist_ok=True)
    # "setup" in the filename triggers UAC's Installer Detection heuristic
    # (32-bit exe without a manifest) — keep every such word out of the
    # name. The FULL VERSION stamp (not just APP_VERSION — ticket 57) so
    # every build ships under its own, self-describing filename.
    exe = DIST / f"CampaignStudio-{bundle_version()}.exe"
    exe.write_bytes(sfx_module.read_bytes()
                    + config.encode("utf-8-sig") + payload.read_bytes())
    # the friend-facing sidecar (ticket 68): the SmartScreen/AV guidance
    # must reach the friend BEFORE the first double-click, and README.txt
    # — which carries it today — travels inside the payload the exe
    # extracts, i.e. inside the file the friend cannot open yet.
    shutil.copy2(TEMPLATES / "BEFORE-YOU-RUN.txt", DIST / "BEFORE-YOU-RUN.txt")
    print(f"  {exe.name}: {exe.stat().st_size // (1024 * 1024)} MB")
    return exe


def verify_sfx(exe, port):
    """Two passes against the real exe: install + boot, then a second run
    that must take the already-installed fast path (a sentinel file at the
    install root proves robocopy /MIR never ran again)."""
    step(f"verify: run the setup exe (install pass) on port {port}")
    tdir = WORK / "sfx-test"
    # a locked leftover must not kill the verify: rmtree with the
    # read-only-clearing onerror (a parked launch.cmd or a read-only git
    # pack survives ignore_errors), then exist_ok on the mkdir — seen for
    # real when a previous session's sfx-test outlived its process
    shutil.rmtree(tdir, onerror=_clear_readonly_and_retry)
    tdir.mkdir(parents=True, exist_ok=True)
    exe2 = tdir / exe.name
    shutil.copy2(exe, exe2)
    # CS_TEST_HOME lets the verify run against an install home with a
    # "normal" (&-free, but the repo path itself contains &) spelling.
    dest = Path(os.environ.get("CS_TEST_HOME") or str(tdir / "installed"))
    env = {**os.environ, "STUDIO_PORT": str(port), "STUDIO_NO_BROWSER": "1",
           "CAMPAIGN_STUDIO_HOME": str(dest)}

    def kill_all(sfx_proc):
        kill_sfx_tree(sfx_proc, exe.name, port)

    proc = subprocess.Popen([str(exe2)], env=env, cwd=tdir)
    try:
        deadline = time.monotonic() + 1200
        while not (dest / "VERSION").exists():
            if time.monotonic() > deadline or proc.poll() is not None:
                raise SystemExit("FAILED: the setup exe never installed the app")
            time.sleep(2)
        print(f"  installed into {dest}")
        wait_http(f"http://127.0.0.1:{port}/", 600)
        wait_http(f"http://127.0.0.1:{port}/tutorial", 30)
        wait_http(f"http://127.0.0.1:{port}/atlas", 30)
        if not (dest / "studio" / "run.py").exists():
            raise SystemExit("FAILED: bundle contents did not land in the install home")
        print("  install pass: /, /tutorial and /atlas -> 200")
    finally:
        kill_all(proc)

    step("verify: second run of the exe (already-installed fast path)")
    sentinel = dest / "sentinel.txt"
    sentinel.write_text("delete-me-if-you-must", encoding="ascii")
    proc = subprocess.Popen([str(exe2)], env=env, cwd=tdir)
    try:
        wait_http(f"http://127.0.0.1:{port}/", 300)
        if not sentinel.exists():
            raise SystemExit("FAILED: second run re-copied the payload "
                             "(fast path not taken)")
        print("  fast path: server up, payload copy skipped")
    finally:
        kill_all(proc)

    if ARGS.keep_work:
        print(f"  installed copy kept at {dest}")
    else:
        shutil.rmtree(tdir, ignore_errors=True)


def verify_update(exe, sevenzr, port):
    """Cross-version update leg (ticket 57). The exe a friend already
    owns, run again over an OLDER install, is the update path — and it
    used to break its own promises: llm.env clobbered, a half-dead
    mirror fast-pathed over forever, a running studio mirrored anyway.
    Three legs, all inside the release smoke:

    1. The real exe over a synthesized older install (old VERSION stamp,
       a rotated llm.env, a campaign marker): the key file must survive
       byte-for-byte, the campaign data intact, VERSION advanced.
    2. With that install's studio RUNNING: install.cmd must refuse
       (exit 1) before mirroring anything.
    3. A forced robocopy failure (a share-none handle on a deep file the
       mirror must overwrite): no completion stamp is written, and the
       next run re-mirrors even though VERSION already matches — no
       stamp, no fast path.

    Legs 2 and 3 run install.cmd directly from the extracted payload —
    the exact script the exe runs, without paying another temp
    extraction."""
    step(f"verify: cross-version update over an older install (port {port})")
    tdir = WORK / "update-test"
    shutil.rmtree(tdir, ignore_errors=True)
    tdir.mkdir(parents=True)
    dest = Path(os.environ.get("CS_TEST_HOME") or str(tdir / "installed"))
    new_version = bundle_version()
    old_version = re.sub(r"\+\d{14}\+", "+00000000000000+", new_version)
    if old_version == new_version:
        raise SystemExit("FAILED: could not synthesize an older VERSION "
                         f"from {new_version!r}")

    def dest_version():
        try:
            return (dest / "VERSION").read_text(encoding="ascii").strip()
        except OSError:
            return ""

    def install_log():
        log = dest / "install-log.txt"
        return log.read_text(encoding="utf-8", errors="replace") \
            if log.exists() else ""

    # A synthesized previous install: completed (old stamp present), with
    # a key the friend rotated and a campaign that must survive.
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "VERSION").write_text(old_version, encoding="ascii")
    (dest / ".install-complete").write_text(old_version, encoding="ascii")
    rotated = ("LLM_BASE_URL=http://127.0.0.1:1/v1\r\n"
               "LLM_API_KEY=rotated-by-the-friend-ticket-57\r\n"
               "LLM_MODEL=rotated-model\r\n").encode("utf-8")
    # bytes on both ends: text-mode write/read would translate the \r\n
    # and the comparison would lie about byte-for-byte survival
    (dest / "llm.env").write_bytes(rotated)
    campaign = dest / "campaign-data" / "campaigns" / "kept-campaign"
    campaign.mkdir(parents=True)
    (campaign / "campaign.yaml").write_text("name: kept-campaign\n",
                                            encoding="utf-8")
    env = {**os.environ, "STUDIO_PORT": str(port), "STUDIO_NO_BROWSER": "1",
           "CAMPAIGN_STUDIO_HOME": str(dest)}

    # leg 1: the real exe updates the older install in place.
    exe2 = tdir / exe.name
    shutil.copy2(exe, exe2)
    proc = subprocess.Popen([str(exe2)], env=env, cwd=tdir)
    try:
        deadline = time.monotonic() + 1200
        while dest_version() != new_version:
            if time.monotonic() > deadline or proc.poll() is not None:
                raise SystemExit(f"FAILED: the exe never updated the older "
                                 f"install (VERSION is {dest_version()!r})")
            time.sleep(2)
        wait_http(f"http://127.0.0.1:{port}/", 600)
        wait_http(f"http://127.0.0.1:{port}/tutorial", 30)
        wait_http(f"http://127.0.0.1:{port}/atlas", 30)
        if (dest / "llm.env").read_bytes() != rotated:
            raise SystemExit("FAILED: the update clobbered the installed "
                             "llm.env (the friend's rotated key is gone)")
        if not (campaign / "campaign.yaml").is_file():
            raise SystemExit("FAILED: the update destroyed campaign data")
        if dest_version() != new_version or (dest / ".install-complete") \
                .read_text(encoding="ascii").strip() != new_version:
            raise SystemExit("FAILED: the update did not complete "
                             "(VERSION/stamp not advanced)")
        print("  update pass: llm.env + campaigns preserved, VERSION advanced")
    finally:
        kill_sfx_tree(proc, exe.name, port)

    # legs 2 and 3 run install.cmd straight from the payload — the same
    # script the SFX extracted and ran in leg 1.
    payload_dir = tdir / "payload"
    payload_dir.mkdir()
    run([sevenzr, "x", f"-o{payload_dir}", "-y", WORK / "payload.7z"],
        stdout=subprocess.DEVNULL)
    # install.cmd's stdout goes to a FILE, never a pipe: the launch.cmd
    # chain it `start`s inherits the handle, and a pipe would make this
    # run() wait for that (parked or server-running) chain forever.
    install_out = (WORK / "update-install.out").open("w")

    def run_install_cmd():
        return subprocess.run(["cmd", "/c", "install.cmd"], env=env,
                              cwd=payload_dir, stdout=install_out,
                              stderr=subprocess.STDOUT, check=False,
                              timeout=900).returncode

    # leg 2: a running studio must stop the update BEFORE any mirroring.
    boot_log = (WORK / "update-boot.log").open("w")
    boot = subprocess.Popen(["cmd", "/c", "launch.cmd"], env=env, cwd=dest,
                            stdout=boot_log, stderr=subprocess.STDOUT)
    try:
        wait_http(f"http://127.0.0.1:{port}/", 600)
        sentinel = dest / "refusal-sentinel.txt"
        sentinel.write_text("a running studio must stop the mirror",
                            encoding="ascii")
        rc = run_install_cmd()
        if rc != 1:
            raise SystemExit(f"FAILED: install.cmd over a running studio "
                             f"exited {rc}, expected refusal (1)")
        if not sentinel.exists() or dest_version() != new_version:
            raise SystemExit("FAILED: the refused run still mirrored")
        if "refused" not in install_log():
            raise SystemExit("FAILED: the refusal was not logged")
        print("  refusal pass: exit 1 before mirroring, install untouched")
    finally:
        kill_sfx_tree(boot, exe.name, port)
        boot_log.close()

    # leg 3: a forced robocopy failure must leave no completion stamp, so
    # the NEXT run re-mirrors instead of fast-pathing over the wreckage.
    # Start from the state a dead mirror leaves (no stamp — the in-flight
    # /MIR had already deleted it as an extra file); the share-none handle
    # is the same "file in use" robocopy dies on when a studio was running
    # mid-mirror, and the aged bytes force the copy.
    import ctypes
    (dest / ".install-complete").unlink(missing_ok=True)
    aged = dest / "studio" / "server.py"
    aged.write_text("# aged by the verify to force the copy\n"
                    + aged.read_text(encoding="utf-8"), encoding="utf-8")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.CreateFileW(str(aged), 0xC0000000, 0, None, 3, 0, None)
    try:
        run_install_cmd()  # "Trying to start anyway" + a booting server
    finally:
        k32.CloseHandle(handle)
    wait_http(f"http://127.0.0.1:{port}/", 300)   # it half-started anyway
    kill_sfx_tree(None, exe.name, port)
    exits = [int(m) for m in re.findall(r"robocopy exit (\d+)", install_log())]
    if not exits or exits[-1] < 8:
        raise SystemExit(f"FAILED: the forced lock did not fail the mirror "
                         f"(last robocopy exits {exits[-3:]})")
    stamp = dest / ".install-complete"
    if stamp.exists() and stamp.read_text(encoding="ascii").strip() \
            == new_version:
        raise SystemExit("FAILED: a failed mirror wrote the completion stamp")
    # The frankeninstall trap: VERSION may already say the new payload (the
    # real partial mirrors copied it early), but the stamp does not — the
    # next run must MIRROR anyway, not fast-path.
    shutil.copy2(payload_dir / "VERSION", dest / "VERSION")
    sentinel = dest / "re-mirror-sentinel.txt"
    sentinel.write_text("no stamp means the mirror must run again",
                        encoding="ascii")
    run_install_cmd()
    wait_http(f"http://127.0.0.1:{port}/", 300)
    kill_sfx_tree(None, exe.name, port)
    if sentinel.exists():
        raise SystemExit("FAILED: no completion stamp still took the fast "
                         "path (the sentinel survived)")
    if stamp.read_text(encoding="ascii").strip() != new_version:
        raise SystemExit("FAILED: the recovery mirror did not write its "
                         "completion stamp")
    if aged.read_text(encoding="utf-8") != \
            (payload_dir / "studio" / "server.py").read_text(encoding="utf-8"):
        raise SystemExit("FAILED: the recovery mirror did not restore the "
                         "aged file")
    print("  failed-mirror pass: no stamp -> re-mirror, not the fast path")
    install_out.close()

    if ARGS.keep_work:
        print(f"  updated copy kept at {dest}")
    else:
        shutil.rmtree(tdir, ignore_errors=True)


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-key", action="store_true",
                    help="do not bake the LLM key into the bundle")
    ap.add_argument("--no-smoke", action="store_true",
                    help="skip the boot smoke tests (not recommended)")
    ap.add_argument("--campaigns", action="store_true",
                    help="copy your campaign-data/campaigns/ into the "
                         "bundle (empty by default)")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep release/work (stage, payload, sfx test copy)")
    ap.add_argument("--sfx-only", action="store_true",
                    help="reuse release/work/payload.7z; redo only the sfx "
                         "step + verification (for iterating on the sfx "
                         "layer); warns when tracked templates are newer "
                         "than the cached payload")
    ARGS = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    sevenzr, sfx_module, node_zip = ensure_cache()
    if ARGS.sfx_only:
        payload = WORK / "payload.7z"
        if not payload.exists():
            raise SystemExit("--sfx-only: release/work/payload.7z not found")
        # the cached payload predates any template edited after it was
        # packed — warn LOUDLY (ticket 68): the whole point of the sfx
        # loop is iterating on these files, and the verify passes stay
        # green over a payload that does not contain the edit.
        stale = stale_templates(payload)
        if stale:
            print("!" * 66, flush=True)
            print("WARNING: --sfx-only reuses the cached payload.7z, but "
                  "these tracked")
            print("templates are NEWER than it — this exe is built WITHOUT "
                  "them, and")
            print("its verify passes will not test them:")
            for name in stale:
                print(f"    {TEMPLATES / name}")
            print("Run a full build (drop --sfx-only) to pick the edits up.")
            print("!" * 66, flush=True)
    else:
        ensure_companion()
        build_stage(node_zip)
        if not ARGS.no_smoke:
            smoke(PORT_STAGE)
        payload = pack(sevenzr)
    exe = make_sfx(payload, sfx_module)
    if not ARGS.no_smoke:
        verify_sfx(exe, PORT_SFX)
        verify_update(exe, sevenzr, PORT_UPDATE)

    step("done")
    h = hashlib.sha256(exe.read_bytes()).hexdigest()
    print(f"  {exe}")
    print(f"  size: {exe.stat().st_size / (1024 * 1024):.0f} MB  "
          f"sha256: {h[:16]}…")
    # the share message names BOTH files (ticket 68): the sidecar is the
    # only thing that reaches the friend before the first double-click.
    print(f"  share BOTH files: {exe.name} + BEFORE-YOU-RUN.txt (beside it "
          "— the friend reads it before the first double-click)")
    if not ARGS.keep_work:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
