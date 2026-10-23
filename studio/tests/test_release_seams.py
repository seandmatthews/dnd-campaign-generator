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

export.cmd (ticket 58) is executed END TO END against a fake bundle root
whose runtime\python is a junction to a real interpreter: the script must
resolve everything off %ROOT% — a system python on PATH is never
consulted, which is the friend-machine situation the real bundle is in.
"""

import importlib.util
import os
import re
import shutil
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
                  if "robocopy" in ln and "/MIR" in ln)
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
        capture_output=True, text=True)
    assert r.returncode < 8, r.stdout + r.stderr  # 0-7: robocopy success
    assert (dest / "app.txt").is_file()
    assert (dest / "nested" / "campaign-data" / "mirror-me.txt").is_file()
    assert not (dest / "campaign-data" / "payload.txt").exists()
    assert (dest / "campaign-data" / "userdata.txt").is_file()
    assert (dest / "campaigns" / "legacy.txt").is_file()
