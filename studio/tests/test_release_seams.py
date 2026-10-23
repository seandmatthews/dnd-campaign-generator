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
