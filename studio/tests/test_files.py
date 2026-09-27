"""The /campaign-file mount: serves inside the tree, blocks traversal.

The mount target is bound at server import (server.py mounts _NoDotGit with
a fixed directory string), so these tests build their own app around the
SAME _NoDotGit class pointed at a temp root — hermetic, no dependence on
campaigns/ existing as a git repo on this machine (it is gitignored and a
fresh clone never has one). The real mounted app still gets one write-free
.git denial check at the bottom.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def mount(tmp_path):
    root = tmp_path / "campaigns"
    root.mkdir()
    app = FastAPI()
    app.mount("/campaign-file", server_mount_class()(directory=str(root)))
    return TestClient(app), root


def server_mount_class():
    from server import _NoDotGit
    return _NoDotGit


def test_campaign_file_serves_and_blocks_traversal(mount):
    client, root = mount
    probe = root / "_studio-traversal-probe.txt"
    probe.write_text("inside secret", encoding="utf-8")
    ok = client.get("/campaign-file/_studio-traversal-probe.txt")
    assert ok.status_code == 200
    assert ok.text == "inside secret"

    for evil in ("/campaign-file/%2e%2e/_studio-traversal-probe.txt",
                 "/campaign-file/..%2F_studio-traversal-probe.txt",
                 "/campaign-file/foo/%2e%2e/%2e%2e/_studio-traversal-probe.txt",
                 "/campaign-file/%252e%252e/_studio-traversal-probe.txt"):
        r = client.get(evil)
        assert r.status_code == 404, evil
        assert "inside secret" not in r.text, evil

    assert client.get(
        "/campaign-file/does-not-exist.svg").status_code == 404


def test_campaign_file_never_serves_dot_git(mount):
    """campaigns/ is its own git repo: .git (config, packed history) must
    not be readable over HTTP even though it lives under the mount root.
    The deny is by path segment at any depth — no real repo needed."""
    client, root = mount
    git_dir = root / ".git"
    git_dir.mkdir()
    probe = git_dir / "studio-probe.txt"
    probe.write_text("git secret", encoding="utf-8")
    (root / "campaign" / "sub" / ".git").mkdir(parents=True)
    nested = root / "campaign" / "sub" / ".git" / "leak.txt"
    nested.write_text("nested git secret", encoding="utf-8")
    for evil in ("/campaign-file/.git/studio-probe.txt",
                 "/campaign-file/.git/config",
                 "/campaign-file/campaign/sub/.git/leak.txt"):
        r = client.get(evil)
        assert r.status_code == 404, evil
        assert "git secret" not in r.text, evil
        assert "nested git secret" not in r.text, evil


def test_real_mount_denies_dot_git(client):
    """The app actually serving /campaign-file denies .git too — asserted
    without writing anything (404 covers both 'denied' and 'absent', so a
    machine without a campaigns/.git still passes; the segment deny itself
    is pinned by the temp-mount tests above)."""
    for evil in ("/campaign-file/.git/config",
                 "/campaign-file/.git/HEAD"):
        r = client.get(evil)
        assert r.status_code == 404, evil
