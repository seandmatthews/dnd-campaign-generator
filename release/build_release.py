#!/usr/bin/env python3
r"""Build the one-file Windows release of Campaign Studio.

Output: release/dist/CampaignStudio-<version>.exe — a single
self-extracting installer (7-Zip's 7zSD stub + 7z payload + config,
concatenated). Double-clicking it extracts the payload to a temp dir and
runs install.cmd there, which mirrors the app into
"%LOCALAPPDATA%\Campaign Studio" (robocopy /MIR, but never touching the
campaigns/ data) and starts launch.cmd from there; the browser opens.
Re-running a same-version exe just relaunches; a new version refreshes
the program in place and keeps the campaigns. The target machine needs
nothing installed:

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

Usage:  python release/build_release.py [--no-key] [--no-smoke]
                                        [--campaigns] [--keep-work]
Downloads (CPython embeddable, node, 7-Zip tools) are cached in
release/cache; staging and payload live in release/work. Stdlib only.
"""

import argparse
import hashlib
import json
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
NODE_MAJOR = 24
# The installer SFX module (bin/7zSD.sfx) ships in the LZMA SDK package
# (the -extra packages stopped carrying it). Igor's minimal module extracts
# to a TEMP dir and runs RunProgram from there — hence the install.cmd step.
LZMA_SDK_VERSIONS = ["2603", "2501", "2409"]
APP_VERSION = re.search(
    r'^version\s*=\s*"([^"]+)"', (REPO / "pyproject.toml").read_text(encoding="utf-8"), re.M
).group(1)
PORT_STAGE, PORT_SFX = 8791, 8793
# Second granularity on purpose: install.cmd's fast path compares VERSION
# byte-for-byte, and two rebuilds within one minute at %H%M granularity
# produced identical bytes — the second exe then skipped mirroring a
# CHANGED payload. With seconds, only the same exe (or a rebuild inside
# the same second, payload and stamp both identical) takes the fast path.
STAMP_FMT = "%Y%m%d%H%M%S"

# Never copied into the bundle, at any depth (name-based, per copy call).
IGNORE_BASE = [".git", ".venv", "__pycache__", "*.pyc", ".pytest_cache"]


def step(msg):
    print(f"\n=== {msg} ===", flush=True)


def run(cmd, **kw):
    """subprocess.run with list args, echoing failures verbosely."""
    r = subprocess.run([str(c) for c in cmd], **kw)
    if r.returncode != 0:
        raise SystemExit(f"FAILED ({r.returncode}): {' '.join(str(c) for c in cmd)}")
    return r


def download(url, dest):
    if dest.exists():
        print(f"  cached  {dest.name} ({dest.stat().st_size // 1024} KB)", flush=True)
        return dest
    print(f"  fetch   {url}", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 campaign-studio-build"})
    with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.rename(dest)
    return dest


def ensure_cache():
    step("cache: python embeddable, get-pip, node, 7-zip sfx tools")
    download(f"https://www.python.org/ftp/python/{PY_VERSION}/"
             f"python-{PY_VERSION}-embed-amd64.zip", CACHE / f"python-{PY_VERSION}-embed.zip")
    download("https://bootstrap.pypa.io/get-pip.py", CACHE / "get-pip.py")

    req = urllib.request.Request("https://nodejs.org/dist/index.json",
                                 headers={"User-Agent": "Mozilla/5.0 campaign-studio-build"})
    index = json.loads(urllib.request.urlopen(req, timeout=60).read())
    node_ver = next(e["version"] for e in index if e["version"].startswith(f"v{NODE_MAJOR}."))
    download(f"https://nodejs.org/dist/{node_ver}/node-{node_ver}-win-x64.zip",
             CACHE / f"node-{node_ver}-win-x64.zip")

    sevenzr = download("https://www.7-zip.org/a/7zr.exe", CACHE / "7zr.exe")
    sdk = None
    for ver in LZMA_SDK_VERSIONS:
        try:
            sdk = download(f"https://www.7-zip.org/a/lzma{ver}.7z", CACHE / f"lzma{ver}.7z")
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
    return sevenzr, sfx, CACHE / f"node-{node_ver}-win-x64.zip"


def copy_tree(src, dst, extra_ignores=()):
    shutil.copytree(src, dst,
                    ignore=shutil.ignore_patterns(*IGNORE_BASE, *extra_ignores))


def build_stage(node_zip):
    step("stage: copy sources (preserving the repo's sibling layout)")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    copy_tree(REPO / "studio", STAGE / "studio", ("logs", "tests"))
    (STAGE / "studio" / "logs").mkdir()
    copy_tree(REPO / "agentic-simulator", STAGE / "agentic-simulator",
              ("world", "chronicle", "tests"))
    for d in ("world/replay", "chronicle"):
        (STAGE / "agentic-simulator" / d).mkdir(parents=True, exist_ok=True)
    copy_tree(REPO / "dnd-mapping", STAGE / "dnd-mapping",
              ("docs", "out-gen-city-smoke"))
    worlds = STAGE / "dnd-mapping" / "worlds"
    shutil.rmtree(worlds / "sites", ignore_errors=True)
    (worlds / "sites").mkdir()
    for p in (worlds / "candidates").glob("*"):  # build-machine scratch
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    (worlds / "raw").mkdir(exist_ok=True)
    copy_tree(REPO / "verification", STAGE / "verification")
    shutil.copy2(REPO / "docs" / "for-new-gms" / "TUTORIAL.md", STAGE / "TUTORIAL.md")
    shutil.copy2(REPO / "llm.env.example", STAGE / "llm.env.example")

    (STAGE / "campaigns" / ".locks").mkdir(parents=True)
    if ARGS.campaigns:
        for camp in (REPO / "campaigns").iterdir():
            if camp.name != ".locks" and camp.is_dir():
                copy_tree(camp, STAGE / "campaigns" / camp.name)

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
    shutil.copy2(TEMPLATES / "README.txt", STAGE / "README.txt")
    # Build stamp included: install.cmd's fast path compares this file
    # byte-for-byte, so every rebuild is an update — no version bump to
    # remember when re-sharing. STAMP_FMT carries seconds; see its note.
    stamp = time.strftime(STAMP_FMT)
    (STAGE / "VERSION").write_text(f"{APP_VERSION}+{stamp}", encoding="ascii")


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
    # The repo path contains "&" (Documents\D&D) and cmd /c mangles quoted
    # absolute paths containing it. Invoke launch.cmd by RELATIVE name with
    # the stage as cwd — no absolute path ever reaches cmd's parser.
    proc = subprocess.Popen(["cmd", "/c", "launch.cmd"], env=env,
                            cwd=STAGE, stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_http(f"http://127.0.0.1:{port}/", 60)
        wait_http(f"http://127.0.0.1:{port}/tutorial", 30)
        print("  / and /tutorial -> 200; killing server")
    finally:
        kill_port(port)
        if proc.poll() is None:
            proc.kill()
        log.close()
    if kill_port(port):
        raise SystemExit(f"FAILED: port {port} still busy after kill")
    step("smoke: clean test artifacts out of the stage before packing")
    shutil.rmtree(STAGE / "studio" / "logs", ignore_errors=True)
    (STAGE / "studio" / "logs").mkdir()
    shutil.rmtree(STAGE / "campaigns", ignore_errors=True)
    (STAGE / "campaigns" / ".locks").mkdir(parents=True)
    if ARGS.campaigns:  # smoke traffic may have touched campaign dirs
        for camp in (REPO / "campaigns").iterdir():
            if camp.name != ".locks" and camp.is_dir():
                dst = STAGE / "campaigns" / camp.name
                shutil.rmtree(dst, ignore_errors=True)
                copy_tree(camp, dst)


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
    # (32-bit exe without a manifest) — keep every such word out of the name.
    exe = DIST / f"CampaignStudio-{APP_VERSION}.exe"
    exe.write_bytes(sfx_module.read_bytes()
                    + config.encode("utf-8-sig") + payload.read_bytes())
    print(f"  {exe.name}: {exe.stat().st_size // (1024 * 1024)} MB")
    return exe


def verify_sfx(exe, port):
    """Two passes against the real exe: install + boot, then a second run
    that must take the already-installed fast path (a sentinel file at the
    install root proves robocopy /MIR never ran again)."""
    step(f"verify: run the setup exe (install pass) on port {port}")
    tdir = WORK / "sfx-test"
    shutil.rmtree(tdir, ignore_errors=True)
    tdir.mkdir(parents=True)
    exe2 = tdir / exe.name
    shutil.copy2(exe, exe2)
    # CS_TEST_HOME lets the verify run against an install home with a
    # "normal" (&-free, but the repo path itself contains &) spelling.
    dest = Path(os.environ.get("CS_TEST_HOME") or str(tdir / "installed"))
    env = {**os.environ, "STUDIO_PORT": str(port), "STUDIO_NO_BROWSER": "1",
           "CAMPAIGN_STUDIO_HOME": str(dest)}

    def kill_all(sfx_proc):
        if sfx_proc.poll() is None:
            sfx_proc.kill()
        kill_port(port)
        subprocess.run(["taskkill", "/F", "/T", "/IM", exe.name], capture_output=True)

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
        if not (dest / "studio" / "run.py").exists():
            raise SystemExit("FAILED: bundle contents did not land in the install home")
        print("  install pass: / and /tutorial -> 200")
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


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-key", action="store_true",
                    help="do not bake the LLM key into the bundle")
    ap.add_argument("--no-smoke", action="store_true",
                    help="skip the boot smoke tests (not recommended)")
    ap.add_argument("--campaigns", action="store_true",
                    help="copy your campaigns/ into the bundle (empty by default)")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep release/work (stage, payload, sfx test copy)")
    ap.add_argument("--sfx-only", action="store_true",
                    help="reuse release/work/payload.7z; redo only the sfx "
                         "step + verification (for iterating on the sfx layer)")
    ARGS = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    sevenzr, sfx_module, node_zip = ensure_cache()
    if ARGS.sfx_only:
        payload = WORK / "payload.7z"
        if not payload.exists():
            raise SystemExit("--sfx-only: release/work/payload.7z not found")
    else:
        build_stage(node_zip)
        if not ARGS.no_smoke:
            smoke(PORT_STAGE)
        payload = pack(sevenzr)
    exe = make_sfx(payload, sfx_module)
    if not ARGS.no_smoke:
        verify_sfx(exe, PORT_SFX)

    step("done")
    h = hashlib.sha256(exe.read_bytes()).hexdigest()
    print(f"  {exe}")
    print(f"  size: {exe.stat().st_size / (1024 * 1024):.0f} MB  "
          f"sha256: {h[:16]}…")
    if not ARGS.keep_work:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
