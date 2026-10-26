"""Statblock fence convention (ticket 49): a pending fence entry approves
into an EXISTING entity page's body byte-surgically — every byte outside
the fence region survives (frontmatter, comments, unknown keys, CRLF);
re-approve replaces the last fence, never duplicates; a recorded
base_revision makes a hand edit between stage and approve an honest 409;
traversal targets refuse; the Review GET serves the statblock kind."""

import pytest
import yaml

import publication
import spine

FENCE_1 = "```statblock\nhearts: 4\neffort: d6\n```"
FENCE_2 = "```statblock\nhearts: 5\neffort: d6\n```"


def _write_pending(root, n, doc):
    rel = f"lore/pending-statblocks-{n}.yaml"
    path = root / "testvale" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # CRLF on disk deliberately (the fence-landing test pins the ticket-10
    # dominant-newline rule) — explicit bytes, because plain write_text
    # only translates \n to \r\n on Windows, leaving linux running a
    # different fixture than windows
    path.write_bytes(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
        .replace("\n", "\r\n").encode("utf-8"))
    return rel, path


def _pending_doc(target, fence, base_revisions=None):
    doc = {"mode": "statblocks", "created": "2026-10-10",
           "entries": [{"id": "sb-1", "target": target, "fence": fence,
                        "source": "authored"}]}
    if base_revisions:
        doc["base_revisions"] = base_revisions
    return doc


def _page(root):
    return root / "testvale" / "lore" / "entities" / "The Vane.md"


def _core(r):
    d = dict(r.json())
    for k in ("transaction", "snapshot", "noop", "entries", "pages",
              "pages_skipped", "stubs"):
        d.pop(k, None)
    return d


def test_approve_inserts_fence_at_end_of_body(client, tmp_spine_root):
    rel, path = _write_pending(tmp_spine_root, 1,
                               _pending_doc("The Vane.md", FENCE_1))
    # the page exists BEFORE the approval, CRLF on disk, explicit bytes —
    # the stub-page fallback inherits the platform's newline default
    # (CRLF only on Windows), which made this fixture platform-dependent
    page = _page(tmp_spine_root)
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(
        ("---\nkind: places\nname: The Vane\n---\n\n"
         "the river that makes the vale worth having\n\n")
        .replace("\n", "\r\n").encode("utf-8"))
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "all": True,
                          "expected_revision":
                              publication.file_revision(path)})
    assert r.status_code == 200, r.text
    assert _core(r) == {"approved": 1, "fences": 1, "fences_skipped": 0}
    text = _page(tmp_spine_root).read_bytes().decode("utf-8")
    # the page is CRLF on disk: the fence lands in the page's
    # dominant newline (by count — the ticket-10 rule)
    assert text.rstrip("\r\n").endswith(FENCE_1.replace("\n", "\r\n")), \
        "fence lands at the end"
    assert "\r\n\r\n```statblock" in text, "a blank line precedes the fence"
    assert not (tmp_spine_root / "testvale" / rel).exists(), \
        "an empty pending file is deleted by the approval"


def test_reapprove_replaces_last_fence_never_duplicates(client, tmp_spine_root):
    rel, _ = _write_pending(tmp_spine_root, 1,
                            _pending_doc("The Vane.md", FENCE_1))
    assert client.post("/api/campaign/testvale/review/approve",
                       json={"file": rel, "all": True}).status_code == 200
    rel2, _ = _write_pending(tmp_spine_root, 2,
                             _pending_doc("The Vane.md", FENCE_2))
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel2, "all": True})
    assert r.status_code == 200, r.text
    text = _page(tmp_spine_root).read_bytes().decode("utf-8")
    assert text.count("```statblock") == 1, "re-approve updates in place"
    assert "hearts: 5" in text and "hearts: 4" not in text


def test_byte_fidelity_on_a_decorated_crlf_page(client, tmp_spine_root):
    """Comments, unknown keys, empty-valued keys, and CRLF endings all
    survive: no byte outside the fence region changes (the ticket-10 bug
    class — an entity_page_text round-trip would have destroyed them)."""
    page = _page(tmp_spine_root)
    decorated = (
        "---\r\n"
        "id: the-vane\r\n"
        "kind: person\r\n"
        "name: The Vane\r\n"
        "resides_at: []          # a hand comment an rewrite would eat\r\n"
        "tavern_secret: the cellar floods\r\n"
        "---\r\n"
        "\r\n"
        "The Vane watches the harbour quay.\r\n"
        "\r\n"
        "Its ledger never balances.\r\n"
    )
    page.write_bytes(decorated.encode("utf-8"))
    rel, _ = _write_pending(tmp_spine_root, 1,
                            _pending_doc("The Vane.md", FENCE_1))
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "all": True})
    assert r.status_code == 200, r.text
    new = page.read_bytes().decode("utf-8")
    # exact full-page expectation: the old page + one blank line + the
    # fence in the page's CRLF — byte for byte, nothing else differs
    fence_crlf = FENCE_1.replace("\n", "\r\n")
    expected = decorated.rstrip("\r\n") + "\r\n\r\n" + fence_crlf + "\r\n"
    assert new == expected
    # parse: the fence is body prose, frontmatter exactly as it was
    parsed = spine.parse_entity_page(page)
    assert parsed["resides_at"] == [] and parsed["place_id"] == ""
    assert "```statblock" in parsed["body"]
    assert parsed["note"] == "The Vane watches the harbour quay."


def test_recorded_base_revision_makes_a_hand_edit_an_honest_409(
        client, tmp_spine_root):
    rel, _ = _write_pending(tmp_spine_root, 1, _pending_doc(
        "The Vane.md", FENCE_1,
        base_revisions={"lore/entities/The Vane.md":
                        publication.file_revision(_page(tmp_spine_root))}))
    page = _page(tmp_spine_root)
    page.write_bytes(page.read_bytes()
                     + "A hand edit after the draft was staged.\n".encode())
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "all": True})
    assert r.status_code == 409
    assert "stale review" in r.json()["error"]
    assert "```statblock" not in page.read_bytes().decode("utf-8"), \
        "nothing was written"


def test_hand_authored_doc_without_base_revisions_applies(
        client, tmp_spine_root):
    """A hand-staged fence records no source pins (sha256 revisions are
    generator-made): approval pins whatever it reads at approve time — the
    insertion is positional, so a newer page is safe to fence."""
    rel, _ = _write_pending(tmp_spine_root, 1,
                            _pending_doc("The Vane.md", FENCE_1))
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "all": True})
    assert r.status_code == 200, r.text
    assert "hearts: 4" in _page(tmp_spine_root).read_text(encoding="utf-8")


def test_same_batch_duplicate_target_refuses(client, tmp_spine_root):
    rel, _ = _write_pending(tmp_spine_root, 1, {
        "mode": "statblocks", "created": "2026-10-10",
        "entries": [
            {"id": "sb-1", "target": "The Vane.md", "fence": FENCE_1,
             "source": "authored"},
            {"id": "sb-2", "target": "The Vane.md", "fence": FENCE_2,
             "source": "authored"},
        ]})
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "all": True})
    assert r.status_code == 409
    assert "filename conflict" in r.json()["error"]


def test_traversal_target_refuses_loudly(client, tmp_spine_root):
    rel, _ = _write_pending(tmp_spine_root, 1,
                            _pending_doc("../campaign.yaml", FENCE_1))
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "all": True})
    assert r.status_code == 422
    assert "not an entity page filename" in r.json()["error"]


def test_missing_target_is_counted_not_fatal(client, tmp_spine_root):
    rel, _ = _write_pending(tmp_spine_root, 1,
                            _pending_doc("Ghost Page.md", FENCE_1))
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": rel, "all": True})
    assert r.status_code == 200, r.text
    assert _core(r) == {"approved": 1, "fences": 0, "fences_skipped": 1}


def test_review_serves_the_statblock_kind(client, tmp_spine_root):
    rel, _ = _write_pending(tmp_spine_root, 1,
                            _pending_doc("The Vane.md", FENCE_1))
    r = client.get("/api/campaign/testvale/review")
    assert r.status_code == 200
    item = next(i for i in r.json()["items"] if i["id"] == "sb-1")
    assert item["kind"] == "statblock"
    assert item["title"] == "The Vane.md"
    assert "hearts: 4" in item["body"]


# ----------------------------------------------------- apply_statblock_fence


def test_apply_replaces_only_within_the_fence_region():
    text = "---\nid: x\n---\n\nProse.\n\n```statblock\nhearts: 2\n```\n"
    out = spine.apply_statblock_fence(text, FENCE_2)
    assert out.count("```statblock") == 1
    assert out.startswith("---\nid: x\n---\n\nProse.\n\n")
    assert out.endswith("hearts: 5\neffort: d6\n```\n")


def test_apply_requires_a_fence():
    with pytest.raises(spine.CampaignError):
        spine.apply_statblock_fence("prose\n", "hearts: 4")


def test_append_uses_dominant_newline_even_after_one_pasted_crlf():
    text = "one\ntwo\r\nthree\n"
    out = spine.apply_statblock_fence(text, FENCE_1)
    assert "\r\n" not in out.split("```statblock")[0].replace(
        "two\r\n", ""), "the pasted CRLF line survives untouched"
    fence = out[out.index("```statblock"):]
    assert "\r\n" not in fence, "the fence follows the page's dominant LF"
