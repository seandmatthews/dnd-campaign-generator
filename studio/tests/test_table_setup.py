"""Ticket 53: the table-setup spawn seam.

The endpoint must spawn the companion's presets publisher with list-form
args and no shell, pointed ONLY at the server-resolved campaign dir; its
success path snapshots the campaign and its refusal comes back verbatim
as stdout with ok=false. Detection is existence-only and writes nothing.
The companion here is a stub on disk — the real one is another repo; the
spawn seam is the test surface, not the publisher's logic.
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
    "assert args[-2:] == ['presets', '--pack'] or "
    "'--pack' in args, args\n"
    "print('stub publisher ran for', args[0])\n"
)
STUB_REFUSE = (
    "import sys\n"
    "print('refused: this campaign does not carry system: icrpg')\n"
    "sys.exit(3)\n"
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


def test_status_reports_companion_and_plugin(client, tmp_spine_root,
                                             fake_companion):
    r = client.get(f"{C}/testvale/table-setup/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["companion_found"] is True
    assert body["atlas_plugin"] is False  # the fixture vault has no plugin

    plugin = tmp_spine_root / "testvale" / ".obsidian" / "plugins" / "atlas-vtt"
    plugin.mkdir(parents=True)
    body = client.get(f"{C}/testvale/table-setup/status").json()
    assert body["atlas_plugin"] is True


def test_detection_writes_nothing_under_obsidian(client, tmp_spine_root,
                                                 fake_companion, no_snapshot):
    """The card path is read-only on .obsidian/ — the spine ignores the
    directory everywhere, and detection must not be the exception."""
    obs = tmp_spine_root / "testvale" / ".obsidian"
    (obs / "plugins" / "atlas-vtt").mkdir(parents=True)
    (obs / "plugins" / "atlas-vtt" / "manifest.json").write_text(
        '{"id": "atlas-vtt", "version": "0.7.1"}', encoding="utf-8")

    def tree(root):
        return {str(p.relative_to(root)): p.is_file()
                for p in root.rglob("*")}

    before = tree(obs)
    assert client.get(f"{C}/testvale/table-setup/status").status_code == 200
    r = client.post(f"{C}/testvale/table-setup/publish-presets", json={})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert tree(obs) == before


def test_publish_spawns_list_form_no_shell_resolved_dir(
        client, tmp_spine_root, fake_companion, no_snapshot, monkeypatch):
    captured = {}

    def spy_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout="stub ok\n",
                                           stderr="")

    monkeypatch.setattr(table_router.subprocess, "run", spy_run)
    r = client.post(f"{C}/testvale/table-setup/publish-presets", json={})
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
    assert "presets" in cmd and "--pack" in cmd
    assert kw.get("cwd") == str(fake_companion)
    assert no_snapshot and "icrpg" in no_snapshot[0][1]


def test_publish_carries_the_refusal_verbatim_no_snapshot(
        client, tmp_spine_root, fake_companion, no_snapshot, monkeypatch):
    (fake_companion / "src" / "avtt" / "publish.py").write_text(
        STUB_REFUSE, encoding="utf-8")
    r = client.post(f"{C}/testvale/table-setup/publish-presets", json={})
    assert r.status_code == 200, r.text  # a refusal is a result, not an error
    body = r.json()
    assert body["ok"] is False and body["exit_code"] == 3
    assert "system: icrpg" in body["stdout"]
    assert no_snapshot == []          # nothing landed, nothing snapshotted


def test_publish_times_out_honestly(client, tmp_spine_root, fake_companion,
                                    monkeypatch):
    (fake_companion / "src" / "avtt" / "publish.py").write_text(
        STUB_SLOW, encoding="utf-8")
    monkeypatch.setattr(table_router, "PUBLISH_TIMEOUT_S", 0.3)
    r = client.post(f"{C}/testvale/table-setup/publish-presets", json={})
    assert r.status_code == 504 and "nothing was changed" in r.json()["error"]


def test_unknown_campaign_404s_before_any_spawn(client, tmp_spine_root,
                                                fake_companion, monkeypatch):
    spawned = []
    monkeypatch.setattr(table_router.subprocess, "run",
                        lambda cmd, **kw: spawned.append(cmd))
    assert client.get(f"{C}/no-such-campaign/table-setup/status").status_code == 404
    assert client.post(f"{C}/no-such-campaign/table-setup/publish-presets",
                       json={}).status_code == 404
    # a crafted escaping name never reaches the spawn either — the name is
    # validated by spine before the endpoint resolves any path (the exact
    # status depends on how the router layer normalizes the crafted URL;
    # any 4xx that is not our spawn is fine)
    r = client.post(f"{C}/..%2F..%2Fetc/table-setup/publish-presets", json={})
    assert r.status_code in (404, 307, 405, 422)
    assert spawned == []


def test_missing_companion_is_an_honest_409(client, tmp_spine_root,
                                            monkeypatch):
    monkeypatch.setattr(table_router, "companion_home", lambda: None)
    r = client.post(f"{C}/testvale/table-setup/publish-presets", json={})
    assert r.status_code == 409 and "ATLAS.md" in r.json()["error"]
    body = client.get(f"{C}/testvale/table-setup/status").json()
    assert body["companion_found"] is False


def test_companion_home_prefers_env_then_bundle_then_sibling(monkeypatch):
    monkeypatch.delenv("AVTT_INTEGRATION_HOME", raising=False)
    home = table_router.companion_home()
    # on a dev checkout the sibling clone resolves; in the bundle the
    # staged avtt-integration/ does. This machine is a dev checkout.
    if (table_router.STUDIO_DIR.parent.parent / "dcg-avtt-integration").is_dir():
        assert home is not None and home.name == "dcg-avtt-integration"
    else:
        assert home is None


def test_atlas_route_serves_the_real_file(client):
    """The card's links point at /atlas — the same one-file contract as
    /tutorial: the route reads the real file, never a copy."""
    from conftest import STUDIO_DIR
    r = client.get("/atlas")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    disk = (STUDIO_DIR.parent / "docs" / "for-new-gms" / "ATLAS.md").read_text(
        encoding="utf-8")
    assert r.text == disk
