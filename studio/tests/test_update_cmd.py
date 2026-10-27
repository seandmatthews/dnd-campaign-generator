"""Ticket 52 pins: the friend-visible updater.

update.cmd is real cmd driving real native tools (powershell, curl,
certutil, Expand-Archive, robocopy), so the pins come in two layers:
text-shape asserts on the template (the contracts a glance cannot
prove — the robocopy guards, the keystroke gate, the hash compare),
and REAL end-to-end legs that run update.cmd against a scratch install
served by a local fake releases/latest — the exact friend flow, minus
the network. Windows-only: the updater is cmd by design.
"""

import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="update.cmd is a Windows batch file")

REPO = Path(__file__).resolve().parents[2]
UPDATE_CMD = REPO / "release" / "templates" / "update.cmd"
LAUNCH_CMD = REPO / "release" / "templates" / "launch.cmd"

OLD_STAMP = "0.1.0+20261001000000+v0.2.0+atlas-0.7.1"
NEW_STAMP = "0.1.0+20261027000000+v0.2.0+atlas-0.7.1"


# ---------------- text-shape pins (no execution) ----------------

def test_the_swap_never_touches_packs_data_or_key():
    text = UPDATE_CMD.read_text(encoding="utf-8")
    # the /MIR excludes, as FULL paths (the install.cmd lesson: a bare
    # name would exclude same-named dirs at ANY depth)
    assert '"%UNPACK%\\campaign-data"' in text
    assert '"%DEST%\\campaign-data"' in text, \
        "the dest-side exclude is what survives a seedless payload"
    assert '"%DEST%\\campaigns"' in text
    assert '"%DEST%\\runtime"' in text
    assert '"%DEST%\\dnd-mapping\\tools"' in text
    assert '"%BACKUP%"' in text
    # the friend's key file and the installer's log survive /MIR
    assert "/XF llm.env install-log.txt" in text


def test_the_explicitness_contract():
    text = UPDATE_CMD.read_text(encoding="utf-8")
    assert "choice /C YN" in text, "updating needs a keystroke"
    assert "Already at" in text, "up-to-date must say so and stop"
    assert "Nothing was changed" in text, "every refusal says so"
    assert "UPDATE_API_BASE" in text, "the test seam exists"
    assert "certutil -hashfile" in text and "SHA256" in text
    assert "Expand-Archive" in text
    assert "app-release:" in text, "the stamp block is what it parses"
    # the live e2e findings, pinned: Expand-Archive gets ONE bounded
    # retry (AV races fresh downloads), and a backup that survives the
    # cleanup retry is DISCLOSED, never silently claimed gone
    assert text.count("Expand-Archive -LiteralPath") == 2, \
        "the unpack retry must be exactly one bounded second try"
    assert "could not be removed just now" in text, \
        "a surviving backup must be disclosed in the success output"


def test_launch_cmd_never_invokes_the_updater():
    text = LAUNCH_CMD.read_text(encoding="utf-8")
    assert "update.cmd" not in text, "updating is the DM's gesture"


# ---------------- the fake releases/latest ----------------

@pytest.fixture()
def release_server():
    """A local stand-in for api.github.com
    /repos/.../releases/latest + the asset download."""
    state = {"tag": "v0.1.1", "zip_bytes": b"", "sha256": "",
             "asset_name": "", "bad_hash": False}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/releases/latest":
                sha = "0" * 64 if state["bad_hash"] else state["sha256"]
                body = json.dumps({
                    "tag_name": state["tag"],
                    "assets": [{"name": state["asset_name"],
                                "browser_download_url":
                                    f"http://127.0.0.1:{state['port']}/asset"}],
                    "body": f"asset: {state['asset_name']}\nsha256: {sha}\n",
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/asset":
                self.send_response(200)
                self.send_header("Content-Length",
                                 str(len(state["zip_bytes"])))
                self.end_headers()
                self.wfile.write(state["zip_bytes"])
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    state["port"] = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield state
    srv.shutdown()
    srv.server_close()


def version_block(stamp, release):
    return (f"{stamp}\napp-release: {release}\napp-commit: none\n"
            "tools-rev: fmg-1.153.1-d5cf359-sharp-0.33.5\n"
            "companion: v0.2.0 atlas-0.7.1\n")


def make_payload(stamp, release):
    """The 'latest release' app payload — new VERSION block, new app
    code, the dnd-mapping source (every real app payload carries it;
    its tools child is what the /XD guard protects), and no llm.env
    (app payloads never carry the key). Deliberately NO campaign-data
    seed: preservation must not depend on the payload carrying one."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("VERSION", (1980, 1, 1, 0, 0, 0)),
                   version_block(stamp, release))
        z.writestr(zipfile.ZipInfo("studio/server.py",
                                   (1980, 1, 1, 0, 0, 0)), "new app code\n")
        z.writestr(zipfile.ZipInfo("launch.cmd", (1980, 1, 1, 0, 0, 0)),
                   "rem new launcher\r\n")
        z.writestr(zipfile.ZipInfo("dnd-mapping/src/battlemap.py",
                                   (1980, 1, 1, 0, 0, 0)),
                   "new mapping code\n")
    return buf.getvalue()


def make_install(root, release, stamp=OLD_STAMP):
    """A synthetic older install: rotated key, campaign data, both
    once-shipped packs, an installer log, and a stray app file the new
    payload lacks (/MIR must delete it)."""
    (root / "studio").mkdir(parents=True)
    (root / "studio" / "server.py").write_text("old app code\n")
    (root / "studio" / "stray.txt").write_text("delete-me\n")
    (root / "runtime").mkdir()
    (root / "runtime" / "token.txt").write_text("keep-runtime\n")
    (root / "dnd-mapping" / "tools").mkdir(parents=True)
    (root / "dnd-mapping" / "tools" / "token.txt").write_text("keep-tools\n")
    (root / "campaign-data" / "campaigns" / "kept").mkdir(parents=True)
    (root / "campaign-data" / "campaigns" / "kept" / "campaign.yaml") \
        .write_text("name: kept\n")
    (root / "llm.env").write_bytes(b"LLM_API_KEY=rotated-by-the-friend\r\n")
    (root / ".install-complete").write_text(stamp + "\n")
    (root / "install-log.txt").write_text("installer was here\n")
    (root / "VERSION").write_text(version_block(stamp, release))


def run_update(install, server_state, keystroke="Y\r\n", api_base=None):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()
    env = {**os.environ,
           "CAMPAIGN_STUDIO_HOME": str(install),
           "UPDATE_API_BASE": api_base or
           f"http://127.0.0.1:{server_state['port']}/releases/latest",
           "STUDIO_PORT": str(free),
           "STUDIO_NO_BROWSER": "1"}
    return subprocess.run(["cmd", "/c", str(UPDATE_CMD)], input=keystroke,
                          capture_output=True, text=True, env=env,
                          timeout=300)


# ---------------- the real end-to-end legs ----------------

def test_update_lands_the_new_app_and_never_touches_the_guarded(
        tmp_path, release_server):
    install = tmp_path / "installed"
    make_install(install, "v0.1.0")
    release_server["zip_bytes"] = make_payload(NEW_STAMP, "v0.1.1")
    release_server["asset_name"] = "CampaignStudio-app-0.1.0+test.zip"
    release_server["sha256"] = hashlib.sha256(
        release_server["zip_bytes"]).hexdigest()

    r = run_update(install, release_server)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Updated to v0.1.1" in r.stdout, r.stdout
    # the swap landed
    assert (install / "VERSION").read_text(encoding="utf-8") == \
        version_block(NEW_STAMP, "v0.1.1")
    assert (install / "studio" / "server.py").read_text() == "new app code\n"
    assert (install / "dnd-mapping" / "src" / "battlemap.py").read_text() \
        == "new mapping code\n"
    # .install-complete is a byte-copy of the new VERSION block (the
    # same contract install.cmd's fast-path fc /b relies on)
    assert (install / ".install-complete").read_text(encoding="utf-8") == \
        version_block(NEW_STAMP, "v0.1.1")
    # the /MIR deletion ran on the app surface
    assert not (install / "studio" / "stray.txt").exists()
    # the guarded survivors: key, data, both packs, installer log
    assert (install / "llm.env").read_bytes() == \
        b"LLM_API_KEY=rotated-by-the-friend\r\n"
    assert (install / "campaign-data" / "campaigns" / "kept" /
            "campaign.yaml").is_file()
    assert (install / "runtime" / "token.txt").read_text() == "keep-runtime\n"
    assert (install / "dnd-mapping" / "tools" /
            "token.txt").read_text() == "keep-tools\n"
    assert "installer was here" in (install / "install-log.txt").read_text()
    assert "update complete - v0.1.1" in \
        (install / "install-log.txt").read_text()
    # the snapshot cleaned up after a clean swap
    assert not (install / ".update-backup").exists()


def test_hash_mismatch_keeps_the_old_tree(tmp_path, release_server):
    install = tmp_path / "installed"
    make_install(install, "v0.1.0")
    release_server["zip_bytes"] = make_payload(NEW_STAMP, "v0.1.1")
    release_server["asset_name"] = "CampaignStudio-app-0.1.0+test.zip"
    release_server["sha256"] = hashlib.sha256(
        release_server["zip_bytes"]).hexdigest()
    release_server["bad_hash"] = True

    r = run_update(install, release_server)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "does not match" in r.stdout, r.stdout
    assert (install / "VERSION").read_text(encoding="utf-8") == \
        version_block(OLD_STAMP, "v0.1.0")
    assert (install / "studio" / "server.py").read_text() == "old app code\n"
    assert (install / "llm.env").read_bytes().startswith(b"LLM_API_KEY=")
    zip_path = Path(os.environ["TEMP"]) / "cs-update-app.zip"
    assert not zip_path.exists(), "the unverifiable download is deleted"


def test_unreachable_release_list_changes_nothing(tmp_path, release_server):
    install = tmp_path / "installed"
    make_install(install, "v0.1.0")
    r = run_update(install, release_server,
                   api_base="http://127.0.0.1:9/releases/latest")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Nothing was changed" in r.stdout, r.stdout
    assert (install / "VERSION").read_text(encoding="utf-8") == \
        version_block(OLD_STAMP, "v0.1.0")


def test_already_current_is_a_polite_noop(tmp_path, release_server):
    install = tmp_path / "installed"
    make_install(install, "v0.1.1", stamp=NEW_STAMP)
    release_server["zip_bytes"] = make_payload(NEW_STAMP, "v0.1.1")
    release_server["asset_name"] = "CampaignStudio-app-0.1.0+test.zip"
    release_server["sha256"] = hashlib.sha256(
        release_server["zip_bytes"]).hexdigest()

    r = run_update(install, release_server)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Already at v0.1.1" in r.stdout, r.stdout
    assert (install / "studio" / "server.py").read_text() == "old app code\n"


def test_pre_stamp_install_gets_the_migration_message(tmp_path,
                                                      release_server):
    install = tmp_path / "installed"
    make_install(install, "v0.1.0")
    (install / "VERSION").write_text(OLD_STAMP + "\n")  # no stamp block
    r = run_update(install, release_server)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "predates the updater" in r.stdout, r.stdout
    assert "full exe" in r.stdout, r.stdout


def test_running_studio_refuses_before_anything(tmp_path, release_server):
    install = tmp_path / "installed"
    make_install(install, "v0.1.0")
    # occupy the probe port (STUDIO_PORT is chosen by run_update; take
    # a port and pass it occupied via a wrapper server)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    occupied = blocker.getsockname()[1]
    env_probe = occupied
    release_server["zip_bytes"] = make_payload(NEW_STAMP, "v0.1.1")
    release_server["asset_name"] = "CampaignStudio-app-0.1.0+test.zip"
    release_server["sha256"] = hashlib.sha256(
        release_server["zip_bytes"]).hexdigest()
    try:
        env = {**os.environ,
               "CAMPAIGN_STUDIO_HOME": str(install),
               "UPDATE_API_BASE":
                   f"http://127.0.0.1:{release_server['port']}"
                   "/releases/latest",
               "STUDIO_PORT": str(env_probe),
               "STUDIO_NO_BROWSER": "1"}
        r = subprocess.run(["cmd", "/c", str(UPDATE_CMD)], input="Y\r\n",
                           capture_output=True, text=True, env=env,
                           timeout=300)
    finally:
        blocker.close()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "still running" in r.stdout, r.stdout
    assert (install / "studio" / "server.py").read_text() == "old app code\n"
