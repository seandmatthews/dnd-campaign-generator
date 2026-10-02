"""Ticket 34: release seams — the VERSION build stamp and install.cmd's
robocopy mirror.

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
"""

import importlib.util
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parents[2] / "release"
INSTALL_CMD = RELEASE / "templates" / "install.cmd"


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


# ------------------------------------------------- the campaigns exclusion

def test_install_cmd_excludes_only_the_top_level_campaigns_dir():
    """/XD carries the full source path (%SRC% is %~dp0 and ends with a
    backslash), never the bare name: the exclusion is top-level-only by
    construction, so a nested campaigns/ dir still mirrors."""
    text = INSTALL_CMD.read_text(encoding="ascii")
    mirror = next(ln for ln in text.splitlines()
                  if "robocopy" in ln and "/MIR" in ln)
    assert '/XD "%SRC%campaigns"' in mirror, mirror
    assert "/XD campaigns" not in mirror, mirror  # the any-depth form
    # ...and the data dir is still seeded on first install only
    assert 'if not exist "%DEST%\\campaigns" robocopy "%SRC%campaigns"' in text


@pytest.mark.skipif(sys.platform != "win32"
                    or shutil.which("robocopy") is None,
                    reason="robocopy mirror semantics — Windows")
def test_robocopy_mirrors_nested_campaigns_and_keeps_the_data_dir(tmp_path):
    """The real mirror command against the real robocopy, with exactly
    install.cmd's argv shape (source spelled with the trailing dot, /XD
    as the full source path, list-form args — no shell). A nested
    campaigns/ mirrors; the top-level campaigns/ is neither copied over
    nor deleted as an /MIR extra (the installed user data survives)."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    (src / "campaigns").mkdir(parents=True)
    (src / "campaigns" / "payload.txt").write_text("new payload",
                                                   encoding="ascii")
    (src / "nested" / "campaigns").mkdir(parents=True)
    (src / "nested" / "campaigns" / "mirror-me.txt").write_text(
        "nested", encoding="ascii")
    (src / "app.txt").write_text("app", encoding="ascii")
    (dest / "campaigns").mkdir(parents=True)
    (dest / "campaigns" / "userdata.txt").write_text("user data",
                                                     encoding="ascii")

    r = subprocess.run(
        ["robocopy", f"{src}.", str(dest), "/MIR",
         "/XD", str(src / "campaigns"), "/R:1", "/W:1",
         "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
        capture_output=True, text=True)
    assert r.returncode < 8, r.stdout + r.stderr  # 0-7: robocopy success
    assert (dest / "app.txt").is_file()
    assert (dest / "nested" / "campaigns" / "mirror-me.txt").is_file()
    assert not (dest / "campaigns" / "payload.txt").exists()
    assert (dest / "campaigns" / "userdata.txt").is_file()
