"""The /campaign-file mount: serves inside the tree, blocks traversal."""

import pytest


def test_campaign_file_serves_and_blocks_traversal(client):
    import spine
    root = spine.campaigns_root()  # the real mount target (created on import)
    probe = root / "_studio-traversal-probe.txt"
    probe.write_text("inside secret", encoding="utf-8")
    try:
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
    finally:
        probe.unlink(missing_ok=True)
