"""Ticket 54: the scenes half of the table-setup spawn seam.

Mirror of test_table_setup.py for the scenes publisher: list-form args,
no shell, pointed ONLY at the server-resolved campaign dir; the optional
site travels as ONE joined ``--site=<slug>`` argument so a crafted value
can never smuggle an option flag of its own; refusals come back verbatim
as stdout with ok=false and no snapshot; the honest 504/409. The
companion here is a stub on disk — the spawn seam is the test surface,
not the publisher's logic.
"""

import subprocess
import sys

import pytest

import canon
from routers import table as table_router

C = "/api/campaign"

STUB_OK = (
    "import sys\n"
    "args = sys.argv[1:]\n"
    "assert 'scenes' in args, args\n"
    "print('stub scenes publisher ran for', args[0])\n"
)
STUB_REFUSE = (
    "import sys\n"
    "print('refused: no sites under maps/sites — nothing to publish')\n"
    "sys.exit(2)\n"
)
STUB_SLOW = "import sys, time\nsys.stdout.write('working...')\nsys.stdout.flush()\ntime.sleep(5)\n"


@pytest.fixture()
def fake_companion(tmp_path, monkeypatch):
    """A companion-shaped tree that answers $AVTT_INTEGRATION_HOME."""
    home = tmp_path / "avtt-integration"
    (home / "src" / "avtt").mkdir(parents=True)
    (home / "src" / "avtt" / "publish.py").write_text(STUB_OK,
                                                      encoding="utf-8")
    (home / "LICENSE").write_text("AGPL-3.0-only", encoding="utf-8")
    monkeypatch.setenv("AVTT_INTEGRATION_HOME", str(home))
    return home


@pytest.fixture()
def no_snapshot(monkeypatch):
    """The endpoint's success snapshot is canon's business, not this
    suite's — record calls instead of running git in the tmp campaign."""
    calls = []
    monkeypatch.setattr(canon, "snapshot",
                        lambda name, msg: calls.append((name, msg)))
    return calls


def test_scenes_spawn_list_form_no_shell_resolved_dir(
        client, tmp_spine_root, fake_companion, no_snapshot, monkeypatch):
    captured = {}

    def spy_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout="stub ok\n",
                                           stderr="")

    monkeypatch.setattr(table_router.subprocess, "run", spy_run)
    r = client.post(f"{C}/testvale/table-setup/publish-scenes",
                    json={"site": "the-drowned-crypt"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["exit_code"] == 0

    cmd, kw = captured["cmd"], captured["kw"]
    assert isinstance(cmd, list)                      # list-form, never a string
    assert not kw.get("shell")                        # never a shell
    assert cmd[0] == sys.executable                   # the studio's python
    assert cmd[1].replace("\\", "/").endswith("src/avtt/publish.py")
    # the campaign dir is the SERVER-RESOLVED one, never client-supplied
    assert str(tmp_spine_root / "testvale") in cmd
    assert "scenes" in cmd
    assert "--site=the-drowned-crypt" in cmd
    assert kw.get("cwd") == str(fake_companion)
    assert no_snapshot and "scene" in no_snapshot[0][1]


def test_scenes_without_site_publishes_every_site(
        client, tmp_spine_root, fake_companion, no_snapshot, monkeypatch):
    captured = {}

    def spy_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="stub ok\n",
                                           stderr="")

    monkeypatch.setattr(table_router.subprocess, "run", spy_run)
    r = client.post(f"{C}/testvale/table-setup/publish-scenes", json={})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "scenes" in captured["cmd"]
    assert not [a for a in captured["cmd"] if a.startswith("--site")]


def test_scenes_site_cannot_smuggle_an_option_flag(
        client, tmp_spine_root, fake_companion, no_snapshot, monkeypatch):
    """A crafted site starting with '-' must arrive as the argument of
    --site, never as a flag of its own."""
    captured = {}

    def spy_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="stub ok\n",
                                           stderr="")

    monkeypatch.setattr(table_router.subprocess, "run", spy_run)
    r = client.post(f"{C}/testvale/table-setup/publish-scenes",
                    json={"site": "--force"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "--site=--force" in captured["cmd"]
    assert "--force" not in captured["cmd"]


def test_scenes_carries_the_refusal_verbatim_no_snapshot(
        client, tmp_spine_root, fake_companion, no_snapshot):
    (fake_companion / "src" / "avtt" / "publish.py").write_text(
        STUB_REFUSE, encoding="utf-8")
    r = client.post(f"{C}/testvale/table-setup/publish-scenes",
                    json={"site": "nowhere"})
    assert r.status_code == 200, r.text  # a refusal is a result, not an error
    body = r.json()
    assert body["ok"] is False and body["exit_code"] == 2
    assert "nothing to publish" in body["stdout"]
    assert no_snapshot == []          # nothing landed, nothing snapshotted


def test_scenes_times_out_honestly(client, tmp_spine_root, fake_companion,
                                   monkeypatch):
    """The timeout KILLS the publisher mid-write: the 504 must say the
    partial writes are uncommitted, never "nothing was changed"
    (ticket 69 — same copy as the presets twin in test_table_setup.py)."""
    (fake_companion / "src" / "avtt" / "publish.py").write_text(
        STUB_SLOW, encoding="utf-8")
    monkeypatch.setattr(table_router, "PUBLISH_TIMEOUT_S", 0.3)
    r = client.post(f"{C}/testvale/table-setup/publish-scenes", json={})
    assert r.status_code == 504
    detail = r.json()["error"]
    assert "nothing was changed" not in detail
    assert "killed at the timeout" in detail
    assert "uncommitted" in detail
    assert "atlas-vtt/" in detail


def test_unknown_campaign_404s_before_any_spawn(client, tmp_spine_root,
                                                fake_companion, monkeypatch):
    spawned = []
    monkeypatch.setattr(table_router.subprocess, "run",
                        lambda cmd, **kw: spawned.append(cmd))
    assert client.post(f"{C}/no-such-campaign/table-setup/publish-scenes",
                       json={}).status_code == 404
    r = client.post(f"{C}/..%2F..%2Fetc/table-setup/publish-scenes", json={})
    assert r.status_code in (404, 307, 405, 422)
    assert spawned == []


def test_missing_companion_is_an_honest_409(client, tmp_spine_root,
                                            monkeypatch):
    monkeypatch.setattr(table_router, "companion_home", lambda: None)
    r = client.post(f"{C}/testvale/table-setup/publish-scenes", json={})
    assert r.status_code == 409 and "ATLAS.md" in r.json()["error"]
