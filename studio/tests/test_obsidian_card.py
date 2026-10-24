"""The Obsidian-household onboarding (ticket 04): the resolved lore path +
its obsidian:// deep link, and the frontend affordances that point at it.

The URI is built SERVER-side from the resolved campaign folder — the browser
only knows the campaign name — so the exact-URI test is a route test run
against a campaigns root whose own path carries a space AND an ampersand
(the real checkout lives at Documents\\D&D; a bland tmp path would prove
nothing about URL encoding). The card/pointer checks assert the served tab
scripts carry the ticket's three affordances and the Review one-liner.

No engine calls, no LLM, no writes: the endpoint is read-only by design
(no in-app lore editor, ever — ticket 04's ruling).
"""

from pathlib import Path
from urllib.parse import unquote

import pytest

import spine


@pytest.fixture()
def spacey_root(tmp_path, monkeypatch):
    """A campaigns root whose absolute path contains a space and an `&`,
    mirroring the real `Documents\\D&D` checkout."""
    root = tmp_path / "Documents D&D" / "campaigns"
    camp = root / "thornroad"
    (camp / "lore" / "entities").mkdir(parents=True)
    (camp / "campaign.yaml").write_text("name: thornroad\n", encoding="utf-8")
    monkeypatch.setattr(spine, "CAMPAIGNS_ROOT", root)
    return root


def test_lore_path_uri_is_built_from_the_resolved_folder(
        client, spacey_root):
    """The named criterion: the URI comes from the RESOLVED campaign path,
    and every awkward byte of `Documents D&D` survives URL encoding."""
    r = client.get("/api/campaign/thornroad/lore/path")
    assert r.status_code == 200, r.text
    body = r.json()

    expected = str((spacey_root / "thornroad" / "lore").resolve())
    assert body["path"] == expected
    assert Path(body["path"]).is_absolute()
    assert " " in body["path"] and "&" in body["path"]

    assert body["obsidian_uri"].startswith("obsidian://open?path=")
    encoded = body["obsidian_uri"].split("path=", 1)[1]
    assert "%20" in encoded  # the space rides, encoded
    assert "%26" in encoded  # so does the ampersand
    assert "&" not in encoded  # a bare & would end the query parameter
    assert unquote(encoded) == body["path"]  # byte-for-byte round trip


def test_lore_path_unknown_campaign_404(client, spine_root):
    assert client.get("/api/campaign/nonesuch/lore/path").status_code == 404
    # traversal-shaped names never resolve (the resolver refuses them)
    r = client.get("/api/campaign/..%2F..%2Fcampaign.yaml/lore/path")
    assert r.status_code in (404, 422)


def test_world_tab_serves_the_authoring_card(client):
    """The served World Building script carries the card: the household
    pointer, the per-campaign localStorage dismissal, the permanent 🔗 way
    back in, the copy button, and the two links (server URI + download)."""
    js = client.get("/js/world.js")
    assert js.status_code == 200
    text = js.text
    assert "Authoring lore" in text
    assert "Obsidian household" in text
    assert "authoring-dismissed" in text  # localStorage key, one per campaign
    assert "world-authoring-link" in text  # the permanent 🔗 button
    assert "navigator.clipboard" in text  # the path copy button
    assert "/lore/path" in text  # resolved path + URI come from the server
    assert "https://obsidian.md/download" in text  # "Get Obsidian"


def test_review_tab_serves_the_page_draft_pointer(client):
    """Page-draft cards carry the compact one-line pointer."""
    js = client.get("/js/review.js")
    assert js.status_code == 200
    text = js.text
    assert "lore-page" in text
    assert "open the folder in Obsidian" in text
