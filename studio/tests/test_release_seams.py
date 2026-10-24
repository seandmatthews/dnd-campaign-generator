"""Release seams — the VERSION build stamp and install.cmd's robocopy
mirror (ticket 34), and the friend backup path (ticket 58).

build_release.py stamps a VERSION file that install.cmd compares
byte-for-byte (the fc /b fast path): the stamp must carry SECONDS, or two
rebuilds within one minute produce identical bytes and the second exe
takes the fast path without mirroring a changed payload. install.cmd's
robocopy /MIR must exclude ONLY the top-level campaigns/ data dir: /XD
with a bare name excludes same-named dirs at ANY depth (a future nested
campaigns/ would silently stop mirroring), so the exclusion carries the
full source path — proven here against the real robocopy on Windows,
with a nested campaigns/ that must mirror and a pre-installed data dir
that /MIR must leave alone.

Ticket 57 extends the same seams to the UPDATE contract: an installed
llm.env must survive a mirror, the fast path additionally requires a
completion stamp that a failed mirror never writes, a running studio
must stop the update before any mirroring, and the built exe is named
with the full VERSION stamp (pyproject's 0.1.0 never moved, so every
build used to ship under the same filename).

export.cmd (ticket 58) is executed END TO END against a fake bundle root
whose runtime\python is a junction to a real interpreter: the script must
resolve everything off %ROOT% — a system python on PATH is never
consulted, which is the friend-machine situation the real bundle is in.
"""

import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parents[2] / "release"
STUDIO = Path(__file__).resolve().parents[1]
INSTALL_CMD = RELEASE / "templates" / "install.cmd"
EXPORT_CMD = RELEASE / "templates" / "export.cmd"
README_TXT = RELEASE / "templates" / "README.txt"
BUILD_RELEASE = RELEASE / "build_release.py"


def _build_release():
    """Import release/build_release.py (it lives outside the studio tree).
    Its module level only resolves paths and reads pyproject.toml — no
    cache writes, no network, no staging side effects."""
    spec = importlib.util.spec_from_file_location(
        "build_release", RELEASE / "build_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ the stamp

def test_version_stamp_carries_seconds():
    """%Y%m%d%H%M%S: the stamp renders 14 digits (12 would be minute
    granularity), and two stamps >= 1s apart within the same minute
    differ — two rebuilds then produce different VERSION bytes, so the
    second exe re-mirrors instead of taking install.cmd's fast path with
    a changed payload."""
    fmt = _build_release().STAMP_FMT
    first = time.strftime(fmt)
    assert re.fullmatch(r"\d{14}", first), first
    time.sleep(1.05)  # strictly past the next second boundary
    assert time.strftime(fmt) != first, \
        "rebuilds a second apart must not share a stamp"


def test_install_cmd_fast_path_still_byte_compares_version():
    """The fast path itself is untouched: identical VERSION bytes (the
    same exe re-run, or a no-change rebuild inside one second, payload
    and stamp both identical) still skips the robocopy mirror."""
    text = INSTALL_CMD.read_text(encoding="ascii")
    assert 'fc /b "%DEST%\\VERSION" "%SRC%VERSION"' in text


# ------------------------------------------------- the data-dir exclusion

def test_install_cmd_excludes_only_the_top_level_data_dir():
    """/XD carries full paths (%SRC%/%DEST% end with a backslash), never
    the bare name: the exclusion is top-level-only by construction, so a
    nested campaign-data/ dir still mirrors. The payload's campaign-data/
    is excluded on the source side, and a pre-ticket-35 install's legacy
    campaigns\\ stays excluded on the dest side too — if the installer's
    one-time move could not run, /MIR must not destroy it."""
    text = INSTALL_CMD.read_text(encoding="ascii")
    mirror = next(ln for ln in text.splitlines()
                  if ln.startswith("robocopy"))
    assert '/XD "%SRC%campaign-data"' in mirror, mirror
    assert '/XD "%SRC%campaign-data" "%DEST%campaigns"' in mirror, mirror
    assert "/XD campaign-data" not in mirror.replace(
        '/XD "%SRC%campaign-data"', ""), mirror  # the any-depth form
    # the legacy data is carried into the new home BEFORE the mirror runs
    move_at = text.index('move "%DEST%\\campaigns"')
    mirror_at = text.index('robocopy "%SRC%." "%DEST%" /MIR')
    assert move_at < mirror_at, "the legacy move must precede /MIR"
    # ...and the data dir is still seeded on first install only
    assert ('if not exist "%DEST%\\campaign-data" robocopy '
            '"%SRC%campaign-data"') in text


# ------------------------------------------- friend backup path (ticket 58)


def test_export_cmd_runs_the_bundled_python_not_a_system_one():
    """The friend-runnable backup path: export.cmd must invoke the BUNDLED
    python by full path (a friend machine has no python on PATH), point at
    the same CAMPAIGN_DATA_ROOT launch.cmd resolves (so the export sees the
    installed campaigns), and stay headless-drivable for the build's smoke
    (the closing pause skipped under STUDIO_NO_BROWSER, exit code kept)."""
    text = EXPORT_CMD.read_text(encoding="ascii")
    assert '"%ROOT%runtime\\python\\python.exe" export_campaign.py' in text, \
        "the bundled python, by full path"
    # command lines only: rem/echo/title prose may say "python" — a bare
    # `python` COMMAND would need a system python the friend does not have
    code = "\n".join(
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lower().lstrip("@ ").startswith(
            ("rem ", "echo", "title ")))
    assert not re.search(r'(^|[\s"])python(\.exe)?(\s|")', code), \
        "a bare `python` command would need a system python"
    assert ('if not defined CAMPAIGN_DATA_ROOT set '
            '"CAMPAIGN_DATA_ROOT=%ROOT%campaign-data"') in text
    assert "if defined STUDIO_NO_BROWSER exit /b %RC%" in text


def test_build_release_stages_and_smokes_export_cmd():
    """export.cmd lands in the bundle beside launch.cmd, and the stage smoke
    actually drives it headless — the friend backup path is pinned by the
    release smoke, not by trust."""
    src = BUILD_RELEASE.read_text(encoding="utf-8")
    assert 'shutil.copy2(TEMPLATES / "export.cmd", STAGE / "export.cmd")' \
        in src
    assert '"cmd", "/c", "export.cmd", "--help"' in src


def test_readme_names_the_real_data_path_and_the_backup_line():
    """README.txt's data section names the real data root (campaign-data/,
    where launch.cmd points CAMPAIGN_DATA_ROOT — not a 'campaigns' folder
    next to the exe) and carries the backup instruction (copy the folder,
    export.cmd for one campaign as a zip)."""
    text = README_TXT.read_text(encoding="ascii")
    data = text[text.index("Your data"):text.index("Backing up")]
    assert '"campaign-data" folder' in data and "next to this" in data
    assert '"campaign-data\\campaigns"' in data
    assert '"campaigns" folder' not in data, "the wrong path is gone"
    backup = text[text.index("Backing up"):text.index("Disk space")]
    assert 'copy the whole "campaign-data" folder' in backup
    assert "export.cmd" in backup
    assert "no undo history" in backup, "the git-less caveat is said plainly"


@pytest.mark.skipif(sys.platform != "win32",
                    reason="export.cmd is a cmd script — Windows")
def test_export_cmd_executes_end_to_end_without_a_system_python(tmp_path):
    """The friend backup path actually RUNS: a fake bundle root shaped like
    the release stage (export.cmd + studio/ + runtime\\python + campaign
    data under campaign-data\\), where runtime\\python is a junction to a
    real interpreter playing the bundled one. The script must resolve the
    interpreter, the studio and the data root entirely off %ROOT% — PATH
    is stripped of python entirely (a friend machine has none), and the
    export still produces its zip with the exit code intact."""
    root = tmp_path / "bundle"
    shutil.copytree(STUDIO, root / "studio",
                    ignore=shutil.ignore_patterns("logs", "tests",
                                                  "__pycache__"))
    shutil.copy2(EXPORT_CMD, root / "export.cmd")
    (root / "runtime").mkdir()
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J",
         str(root / "runtime" / "python"), str(Path(sys.executable).parent)],
        capture_output=True, text=True)
    assert r.returncode == 0, f"junction failed: {r.stderr}"
    camp = root / "campaign-data" / "campaigns" / "testvale"
    camp.mkdir(parents=True)
    (camp / "campaign.yaml").write_text(
        "name: testvale\ntitle: Testvale\n", encoding="utf-8")
    (camp / "lore").mkdir()
    (camp / "lore" / "timeline.yaml").write_text("entries: []\n",
                                                 encoding="utf-8")

    # a friend machine's PATH: no python anywhere on it
    env = {k: v for k, v in os.environ.items()
           if k.upper() != "PATH" and k.upper() != "CAMPAIGN_DATA_ROOT"}
    env["STUDIO_NO_BROWSER"] = "1"  # the headless mode the build smoke uses
    out = tmp_path / "exports" / "testvale.zip"
    r = subprocess.run(
        ["cmd", "/c", "export.cmd", "testvale", "--out", str(out)],
        cwd=root, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
    assert out.is_file(), "the export zip landed where --out said"
    assert "exported 2 file(s)" in r.stdout


@pytest.mark.skipif(sys.platform != "win32"
                    or shutil.which("robocopy") is None,
                    reason="robocopy mirror semantics — Windows")
def test_robocopy_mirrors_nested_data_and_keeps_the_data_dirs(tmp_path):
    """The real mirror command against the real robocopy, with exactly
    install.cmd's argv shape (source spelled with the trailing dot, /XD as
    full paths, list-form args — no shell). A nested campaign-data/
    mirrors; the payload's top-level campaign-data/ is neither copied over
    nor deleted as an /MIR extra, and a legacy campaigns\\ dest survives a
    mirror whose move could not run (the installed user data survives)."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    (src / "campaign-data").mkdir(parents=True)
    (src / "campaign-data" / "payload.txt").write_text("new payload",
                                                       encoding="ascii")
    (src / "nested" / "campaign-data").mkdir(parents=True)
    (src / "nested" / "campaign-data" / "mirror-me.txt").write_text(
        "nested", encoding="ascii")
    (src / "app.txt").write_text("app", encoding="ascii")
    (dest / "campaign-data").mkdir(parents=True)
    (dest / "campaign-data" / "userdata.txt").write_text("user data",
                                                         encoding="ascii")
    (dest / "campaigns").mkdir(parents=True)
    (dest / "campaigns" / "legacy.txt").write_text("legacy data",
                                                   encoding="ascii")

    r = subprocess.run(
        ["robocopy", f"{src}.", str(dest), "/MIR",
         "/XD", str(src / "campaign-data"), str(dest / "campaigns"),
         "/R:1", "/W:1",
         "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
        capture_output=True, text=True, check=False)
    assert r.returncode < 8, r.stdout + r.stderr  # 0-7: robocopy success
    assert (dest / "app.txt").is_file()
    assert (dest / "nested" / "campaign-data" / "mirror-me.txt").is_file()
    assert not (dest / "campaign-data" / "payload.txt").exists()
    assert (dest / "campaign-data" / "userdata.txt").is_file()
    assert (dest / "campaigns" / "legacy.txt").is_file()


# ------------------------------------------ ticket 57: the update contract

def test_install_cmd_probes_a_running_studio_before_anything_else():
    """The netstat probe (the port launch.cmd boots on) comes FIRST —
    before the fast path and before the mirror — and its port match
    carries a trailing space so :8765 does not also match :87650. A
    live install holds python.exe/node.exe locked; mirroring over it
    dies half-way through and leaves the frankeninstall."""
    text = INSTALL_CMD.read_text(encoding="ascii")
    assert 'findstr /C:":%PROBE_PORT% "' in text, "trailing-space port match"
    assert 'set "PROBE_PORT=8765"' in text
    assert 'if defined STUDIO_PORT set "PROBE_PORT=%STUDIO_PORT%"' in text
    probe_at = text.index("netstat -ano | findstr")
    fast_at = text.index("fast path: same version already installed")
    mirror_at = text.index('robocopy "%SRC%." "%DEST%" /MIR')
    assert probe_at < fast_at < mirror_at, \
        "the refusal must precede the fast path and the mirror"
    assert text.index("exit /b 1", probe_at) < fast_at, \
        "the refusal must exit non-zero"


def test_install_cmd_gates_the_fast_path_on_a_completion_stamp():
    """Fast path = VERSION matches AND .install-complete (a copy of the
    payload's VERSION) matches. The stamp is written only in the else
    branch of `if errorlevel 8` — a mirror that failed never writes it,
    so the next run of the same exe re-mirrors instead of fast-pathing
    over the half-copied install."""
    text = INSTALL_CMD.read_text(encoding="ascii")
    assert 'if exist "%DEST%\\.install-complete"' in text
    assert 'fc /b "%DEST%\\.install-complete" "%SRC%VERSION"' in text
    mirror_at = text.index('robocopy "%SRC%." "%DEST%" /MIR')
    fail_at = text.index("if errorlevel 8")
    stamp_at = text.index('copy /y "%SRC%VERSION" "%DEST%\\.install-complete"')
    assert mirror_at < fail_at < stamp_at, \
        "the stamp is only written when robocopy exits below 8"


def test_install_cmd_preserves_an_installed_llm_env():
    """/XF must take the BARE name and exist only when an installed copy
    does. The forms that mirror /XD's full-path trick do not work for
    files: robocopy ignored a full DEST path (the payload's copy still
    overwrote the file in testing), and a full SOURCE path stops the
    copy but not the /MIR extra-DELETION when a keyless payload ships
    without an llm.env — both proven against the real robocopy below
    and in build_release.py's verify_update."""
    text = INSTALL_CMD.read_text(encoding="ascii")
    assert 'if exist "%DEST%\\llm.env" set "KEEP_KEY=/XF llm.env"' in text
    assert 'set "KEEP_KEY="' in text  # first install mirrors WITHOUT /XF
    mirror = next(ln for ln in text.splitlines()
                  if ln.startswith("robocopy"))
    assert "%KEEP_KEY%" in mirror, mirror


def test_make_sfx_names_the_exe_with_the_full_version_stamp(tmp_path,
                                                            monkeypatch):
    """The artifact filename carries the payload's full VERSION stamp,
    not just pyproject's APP_VERSION (which has not moved from 0.1.0 —
    every build used to ship as CampaignStudio-0.1.0.exe, so an old
    download silently downgraded and nothing looked different)."""
    mod = _build_release()
    stage = tmp_path / "stage"
    stage.mkdir()
    version = "0.1.0+20261023120000+v0.2.0+atlas-0.7.1"
    (stage / "VERSION").write_text(version + "\n", encoding="ascii")
    monkeypatch.setattr(mod, "STAGE", stage)
    monkeypatch.setattr(mod, "DIST", tmp_path / "dist")
    payload = tmp_path / "payload.7z"
    payload.write_bytes(b"payload")
    sfx = tmp_path / "7zSD.sfx"
    sfx.write_bytes(b"sfx")
    exe = mod.make_sfx(payload, sfx)
    assert exe.name == f"CampaignStudio-{version}.exe", exe.name


@pytest.mark.skipif(sys.platform != "win32"
                    or shutil.which("robocopy") is None,
                    reason="robocopy mirror semantics — Windows")
def test_robocopy_xf_llm_env_keeps_the_rotated_key_and_seeds_first_install(
        tmp_path):
    """The real mirror against the real robocopy, exactly install.cmd's
    two shapes: with an installed llm.env the bare-name /XF keeps the
    rotated key byte-for-byte and the payload's key never lands; with
    no installed copy (no /XF) the payload seeds it — first install
    still works."""
    src, dest = tmp_path / "src", tmp_path / "dest"

    def build():
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)

    src.mkdir()
    (src / "llm.env").write_text("LLM_API_KEY=payload-key\n",
                                 encoding="ascii")
    # the sizes must DIFFER: robocopy skips a same-size file whose dest
    # timestamp compares equal-or-newer, and these two writes are
    # microseconds apart — an equal-size pair makes the VERSION-copied
    # assert a timestamp lottery (seen red once on this machine)
    (src / "VERSION").write_text("0.1.0+new-payload\n", encoding="ascii")

    def mirror(extra=()):
        return subprocess.run(
            ["robocopy", f"{src}.", str(dest), "/MIR", *extra,
             "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
            capture_output=True, text=True, check=False)

    # update: the friend's rotated key survives, the payload's never lands
    build()
    (dest / "llm.env").write_text("LLM_API_KEY=rotated-by-friend\n",
                                  encoding="ascii")
    (dest / "VERSION").write_text("0.1.0+old\n", encoding="ascii")
    r = mirror(["/XF", "llm.env"])
    assert r.returncode < 8, r.stdout + r.stderr
    assert (dest / "llm.env").read_text(encoding="ascii") \
        == "LLM_API_KEY=rotated-by-friend\n"
    assert (dest / "VERSION").read_text(encoding="ascii") \
        == "0.1.0+new-payload\n"

    # first install: no /XF, the payload seeds the key file
    build()
    r = mirror()
    assert r.returncode < 8, r.stdout + r.stderr
    assert (dest / "llm.env").read_text(encoding="ascii") \
        == "LLM_API_KEY=payload-key\n"


@pytest.mark.skipif(sys.platform != "win32"
                    or shutil.which("robocopy") is None,
                    reason="robocopy mirror semantics — Windows")
def test_robocopy_xf_llm_env_survives_a_keyless_payload(tmp_path):
    """A --no-key build ships no llm.env: the bare-name /XF must keep
    /MIR from deleting the friend's installed copy as an extra file.
    (The full SOURCE path form fails exactly here — it stops the copy
    but not the extra-deletion — which is why install.cmd uses the bare
    name.)"""
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (src / "VERSION").write_text("0.1.0+new\n", encoding="ascii")
    (dest / "llm.env").write_text("LLM_API_KEY=rotated-by-friend\n",
                                  encoding="ascii")
    (dest / "VERSION").write_text("0.1.0+old\n", encoding="ascii")
    r = subprocess.run(
        ["robocopy", f"{src}.", str(dest), "/MIR", "/XF", "llm.env",
         "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
        capture_output=True, text=True, check=False)
    assert r.returncode < 8, r.stdout + r.stderr
    assert (dest / "llm.env").read_text(encoding="ascii") \
        == "LLM_API_KEY=rotated-by-friend\n"


@pytest.mark.skipif(sys.platform != "win32"
                    or shutil.which("robocopy") is None,
                    reason="install.cmd update contract — Windows")
def test_install_cmd_update_contract_end_to_end(tmp_path):
    """install.cmd itself, driven as the SFX drives it (copied into a
    payload dir, run by cmd with CAMPAIGN_STUDIO_HOME), over a mini
    payload: first install seeds llm.env; a same-payload rerun takes
    the fast path and copies NOTHING; a new payload updates while
    preserving the friend's rotated llm.env and campaigns; a listener
    on the studio port makes it refuse before mirroring; a forced
    robocopy failure (a share-none handle on a file the mirror must
    overwrite — the same "file in use" a running studio causes) writes
    no completion stamp; and the run after that re-mirrors even though
    VERSION already matches. No stamp, no fast path."""
    def free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def payload_at(path, version):
        shutil.copytree(payload_root, path)
        (path / "VERSION").write_text(version + "\n", encoding="ascii")
        return path

    def run_install(src):
        shutil.copy2(INSTALL_CMD, src / "install.cmd")
        # stdout to a FILE, never a pipe: the `start`ed launch.cmd chain
        # inherits the handle, and a pipe would make communicate() wait
        # for that parked chain forever (build_release.py's smoke does
        # the same).
        with (tmp_path / "install-cmd.out").open("w") as out:
            return subprocess.run(["cmd", "/c", "install.cmd"],
                                  env=env, cwd=src, stdout=out,
                                  stderr=subprocess.STDOUT,
                                  timeout=300, check=False).returncode

    def read(name):
        p = dest / name
        return p.read_text(encoding="ascii") if p.exists() else None

    v1 = "0.1.0+11111111111111+v0.2.0+atlas-0.7.1"
    v2 = "0.1.0+22222222222222+v0.2.0+atlas-0.7.1"
    payload_root = tmp_path / "payload-root"
    (payload_root / "studio").mkdir(parents=True)
    (payload_root / "campaign-data").mkdir()
    (payload_root / "llm.env").write_text("PAYLOAD-KEY\n", encoding="ascii")
    # `exit 0` (not exit /b): install.cmd start()s this in a cmd /K
    # wrapper — exit closes the wrapper, exit /b would park it.
    (payload_root / "launch.cmd").write_bytes(b"@echo off\r\nexit 0\r\n")
    (payload_root / "campaign-data" / "seed.txt").write_text(
        "seed\n", encoding="ascii")
    payload = payload_at(tmp_path / "payload", v1)
    (payload / "studio" / "app.py").write_text("new\n", encoding="ascii")
    dest = tmp_path / "home"
    dest.mkdir()
    env = {**os.environ, "CAMPAIGN_STUDIO_HOME": str(dest),
           "STUDIO_NO_BROWSER": "1", "STUDIO_PORT": str(free_port())}

    # first install: seeds llm.env, VERSION, stamp, campaign-data
    assert run_install(payload) == 0
    assert read("llm.env") == "PAYLOAD-KEY\n"
    assert read("VERSION") == v1 + "\n"
    assert read(".install-complete") == v1 + "\n"
    assert read("campaign-data/seed.txt") == "seed\n"

    # the friend rotates the key, gains a campaign, edits a file; the
    # SAME payload reruns -> fast path, nothing copied
    (dest / "llm.env").write_text("ROTATED-BY-FRIEND\n", encoding="ascii")
    (dest / "campaign-data" / "campaigns" / "mycamp").mkdir(parents=True)
    (dest / "campaign-data" / "campaigns" / "mycamp" / "canon.yaml") \
        .write_text("kept\n", encoding="ascii")
    (dest / "extra.txt").write_text("fast path keeps me\n", encoding="ascii")
    (dest / "studio" / "app.py").write_text("friend-hack\n", encoding="ascii")
    assert run_install(payload) == 0
    assert (dest / "extra.txt").is_file(), "fast path must not mirror"
    assert read("studio/app.py") == "friend-hack\n"
    assert read("llm.env") == "ROTATED-BY-FRIEND\n"

    # a NEW payload version updates: key + campaigns preserved, code
    # refreshed, stamp advanced, /MIR extras gone
    payload2 = payload_at(tmp_path / "payload2", v2)
    (payload2 / "llm.env").write_text("NEW-PAYLOAD-KEY\n", encoding="ascii")
    (payload2 / "studio" / "app.py").write_text("newer\n", encoding="ascii")
    assert run_install(payload2) == 0
    assert read("llm.env") == "ROTATED-BY-FRIEND\n", \
        "the update clobbered the friend's llm.env"
    assert read("studio/app.py") == "newer\n"
    assert read("VERSION") == v2 + "\n"
    assert read(".install-complete") == v2 + "\n"
    assert not (dest / "extra.txt").exists()
    assert read("campaign-data/campaigns/mycamp/canon.yaml") == "kept\n"

    # a running studio: refuse (exit 1) before mirroring anything
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", int(env["STUDIO_PORT"])))
    listener.listen(1)
    try:
        assert run_install(payload2) == 1
    finally:
        listener.close()
    assert read("VERSION") == v2 + "\n", "the refused run mirrored anyway"
    log = read("install-log.txt")
    assert "refused" in log, log

    # forced robocopy failure: no completion stamp is written. Start
    # from the state a dead mirror leaves (no stamp), age + lock a file
    # the mirror must overwrite.
    (dest / ".install-complete").unlink()
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    aged = dest / "studio" / "app.py"
    aged.write_text("# aged\nnewer\n", encoding="ascii")
    handle = k32.CreateFileW(str(aged), 0xC0000000, 0, None, 3, 0, None)
    try:
        run_install(payload2)  # "Trying to start anyway" — rc stays 0
    finally:
        k32.CloseHandle(handle)
    exits = [int(m) for m in re.findall(r"robocopy exit (\d+)",
                                        read("install-log.txt"))]
    assert exits and exits[-1] >= 8, exits
    assert not (dest / ".install-complete").exists(), \
        "a failed mirror wrote the completion stamp"

    # and the next run re-mirrors even though VERSION already matches
    # this payload — no stamp, no fast path
    (dest / "re-mirror-sentinel.txt").write_text("must be deleted\n",
                                                 encoding="ascii")
    assert run_install(payload2) == 0
    assert not (dest / "re-mirror-sentinel.txt").exists(), \
        "no-stamp run took the fast path"
    assert read(".install-complete") == v2 + "\n"
    assert read("studio/app.py") == "newer\n"
