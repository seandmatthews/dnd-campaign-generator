"""Ticket 07: next session prep — one Obsidian file (the fast slice).

Covers the acceptance list: the write route creates
lore/prep/<date>-next-session.md and never overwrites (the same date's
re-runs take -2, -3 …); every event/quest/rumor item carries a link whose
target actually exists in the vault (relative links resolved from
lore/prep/, wiki-links against lore/entities/); the compile is
deterministic — same canon, byte-identical content, written twice; an
empty campaign yields the honest skeleton with every section present and
nothing fabricated; the write is snapshotted, the campaign lock gates it,
and the CLI equivalent writes the same file. All deterministic — no LLM
pass anywhere.
"""

import datetime
import re

import prep
import spine
import yaml
from conftest import client  # noqa: F401

# markdown link forms prep writes: relative targets (optionally in angle
# brackets) and wiki-links with an optional |label
_MD_ANGLE = re.compile(r"\[[^\]]*\]\(<([^>]+)>\)")
_MD_PLAIN = re.compile(r"\[[^\]]*\]\(([^)<]+)\)")
_WIKI = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def _relative_targets(text):
    return _MD_ANGLE.findall(text) + _MD_PLAIN.findall(text)


def _assert_links_resolve(text, campaign_dir):
    """A link is only working if its target exists: every relative link is
    resolved FROM lore/prep/ (the page's own directory), every wiki-link
    against lore/entities/ (the only place prep writes wiki-links to)."""
    prep_dir = campaign_dir / "lore" / "prep"
    for target in _relative_targets(text):
        assert (prep_dir / target).is_file(), f"dangling relative link: {target}"
    for stem in _WIKI.findall(text):
        assert (campaign_dir / "lore" / "entities" / f"{stem}.md").is_file(), \
            f"dangling wiki-link: [[{stem}]]"


# ---------------------------------------------------------------- the write


def test_knowledge_tab_carries_the_button(client):
    """The Knowledge tab's briefing card carries the Next session prep
    button, wired to the write route with the panel's since-week control
    (served-bytes assertions, the rumor-handout tests' pattern)."""
    r = client.get("/js/knowledge.js")
    assert r.status_code == 200, r.text
    js = r.text
    assert "Next session prep" in js
    assert '"btn btn-primary briefing-prep"' in js
    assert "campUrl('/prep?since_week=" in js
    assert "never overwrites" in js  # the affordance says the rule


def test_write_route_next_free_never_overwrites(client, tmp_spine_root):
    url = "/api/campaign/testvale/prep"
    today = datetime.date.today().isoformat()
    r = client.post(url)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file"] == f"lore/prep/{today}-next-session.md"
    assert body["snapshot"]["state"] == "committed", body["snapshot"]
    d = tmp_spine_root / "testvale"
    first = (d / "lore" / "prep" / f"{today}-next-session.md").read_text(
        encoding="utf-8")
    assert first.startswith("---\nkind: prep\n")

    # the same date again: the re-run lands BESIDE it (-2) — regeneration
    # never overwrites; the GM deletes what they don't want, in Obsidian
    r2 = client.post(url)
    assert r2.status_code == 200, r2.text
    assert r2.json()["file"] == f"lore/prep/{today}-next-session-2.md"
    assert (d / "lore" / "prep" / f"{today}-next-session.md").read_text(
        encoding="utf-8") == first, "the first file was touched"


def test_write_route_honors_the_campaign_lock(client, tmp_spine_root):
    with spine.campaign_lock("testvale"):
        r = client.post("/api/campaign/testvale/prep")
    assert r.status_code == 409, r.text
    assert not (tmp_spine_root / "testvale" / "lore" / "prep").exists()


# ------------------------------------------------------------------- links


def test_every_item_links_back_to_its_containing_doc(client, tmp_spine_root):
    d = tmp_spine_root / "testvale"
    chron = d / "simulation" / "chronicle"
    (chron / "0001-the-toll.md").write_text("# Week 1 — The Toll\n",
                                            encoding="utf-8")
    (chron / "0002-the-bridge-burned.md").write_text(
        "# Week 2 — The Bridge Burned\n", encoding="utf-8")

    # an event whose actor IS an entity page (the fixture's events use the
    # faction id, which no page is named) — that item gets the page link
    state_path = d / "simulation" / "state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["events"].append({"week": 3, "actor": "The Bridge Wardens",
                            "text": "posted the reward for the arsonists"})
    state_path.write_text(
        yaml.safe_dump(state, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8")

    # one overdue timer, one active quest touching the covered range
    def quest_page(stem, front):
        (d / "lore" / "entities" / f"{stem}.md").write_text(
            "---\n" + yaml.safe_dump(front, sort_keys=False,
                                     allow_unicode=True) + "---\n\n"
            "Find it before the timer runs out.\n", encoding="utf-8")

    quest_page("The Tolls Tithe",
               {"kind": "quest", "name": "The Toll's Tithe",
                "status": "active", "due": 1})
    quest_page("Ferry Debt",
               {"kind": "quest", "name": "Ferry Debt", "status": "active",
                "factions": ["The Ferrymen"]})

    # a public fact with a source file, not yet revealed
    rk = client.post("/api/campaign/testvale/knowledge", json={
        "text": "The Vane ferry now runs only at dawn.",
        "visibility": "public", "source_file": "lore/style/voice.md"})
    assert rk.status_code == 200, rk.text

    r = client.post("/api/campaign/testvale/prep")
    assert r.status_code == 200, r.text
    text = (d / r.json()["file"]).read_text(encoding="utf-8")

    # events: chronicle relative links (outside lore/ — ../../) and the
    # actor's page as a wiki-link
    assert "](../../simulation/chronicle/0001-the-toll.md)" in text
    assert "](../../simulation/chronicle/0002-the-bridge-burned.md)" in text
    assert "posted the reward for the arsonists" in text
    assert "[[The Bridge Wardens]]" in text
    # the actor with no page of record stays plain text — no invented link
    assert "raised the toll on the Vane crossing" in text

    # rumors: the freshest week's (born_week 2) with its chronicle
    assert '"the bridge was insured twice over" (hearsay)' in text
    assert "[week 2 — The Bridge Burned]" in text

    # quests: overdue FIRST, then active; wiki-linked to their pages
    overdue_at = text.index("OVERDUE")
    active_at = text.index("[[The Tolls Tithe|The Toll's Tithe]]")
    ferry_at = text.index("[[Ferry Debt|Ferry Debt]]")
    assert overdue_at < active_at < ferry_at, "overdue timers come first"
    assert "due week 1" in text

    # the unrevealed public fact, linked to its fact source file
    assert "The Vane ferry now runs only at dawn." in text
    assert "](../style/voice.md)" in text

    # and every single link written actually resolves in the vault
    _assert_links_resolve(text, d)

    # revealing the fact moves it out of the reminder list
    fact_id = rk.json()["fact"]["id"]
    rr = client.post(
        f"/api/campaign/testvale/knowledge/{fact_id}/reveal", json={})
    assert rr.status_code == 200, rr.text
    after = prep.markdown(prep.build("testvale"))
    assert "runs only at dawn" not in after


# ------------------------------------------------------------ determinism


def test_deterministic_same_canon_byte_identical(tmp_spine_root):
    t1 = prep.markdown(prep.build("testvale",
                                  today=datetime.date(2026, 10, 1)))
    t2 = prep.markdown(prep.build("testvale",
                                  today=datetime.date(2026, 10, 1)))
    assert t1 == t2
    out1 = prep.write("testvale", today=datetime.date(2026, 10, 1))
    out2 = prep.write("testvale", today=datetime.date(2026, 10, 1))
    assert out1["file"] == "lore/prep/2026-10-01-next-session.md"
    assert out2["file"] == "lore/prep/2026-10-01-next-session-2.md"
    d = spine.campaign_dir("testvale")
    p1, p2 = d / out1["file"], d / out2["file"]
    # on-disk determinism: the two files are byte-identical (the spine's
    # atomic writer owns the exact disk bytes; both rides of the same
    # compile produce the same ones)
    assert p1.read_bytes() == p2.read_bytes()
    # and the file content is the renderer's output exactly
    assert p1.read_text(encoding="utf-8") == t1


def test_covers_weeks_and_since_week_range(client, tmp_spine_root):
    r = client.get("/api/campaign/testvale/prep/markdown")
    assert r.status_code == 200
    assert "covers_weeks: [1, 3]" in r.text  # clock week 3, since 0
    narrowed = client.get(
        "/api/campaign/testvale/prep/markdown?since_week=2")
    assert narrowed.status_code == 200
    assert "covers_weeks: [3, 3]" in narrowed.text
    assert "raised the toll on the Vane crossing" not in narrowed.text


# ----------------------------------------------------------- empty campaign


def test_empty_campaign_honest_skeleton(client, tmp_spine_root):
    d = tmp_spine_root / "barevale"
    d.mkdir()
    (d / "campaign.yaml").write_text(
        yaml.safe_dump({"name": "barevale", "title": "Bare Vale"},
                       sort_keys=False), encoding="utf-8")
    r = client.get("/api/campaign/barevale/prep/markdown")
    assert r.status_code == 200
    text = r.text
    assert "kind: prep" in text
    assert f"date: {datetime.date.today().isoformat()}" in text
    assert "covers_weeks: []" in text, "no clock — no range is claimed"
    for section in ("## Where we left off", "## Rumors worth dropping",
                    "## Active quests", "## Waiting to be revealed",
                    "## Open decisions"):
        assert section in text
    assert "No approved changes recorded yet" in text
    assert "No rumors in the world yet" in text
    assert "No quest pages yet" in text
    assert "No public facts waiting" in text
    assert "Nothing awaiting a decision" in text
    # nothing fabricated: with nothing in the canon there is nothing to
    # link to, and the skeleton writes no links at all
    assert not _relative_targets(text), _relative_targets(text)
    assert not _WIKI.findall(text)


# ------------------------------------------------------ route + CLI parity


def test_markdown_route_matches_the_written_file(client, tmp_spine_root):
    r = client.post("/api/campaign/testvale/prep")
    assert r.status_code == 200, r.text
    written = (spine.campaign_dir("testvale")
               / r.json()["file"]).read_text(encoding="utf-8")
    md = client.get("/api/campaign/testvale/prep/markdown")
    assert md.status_code == 200
    assert md.text == written, "the printable copy drifted from the vault page"


def test_cli_equivalent_writes_next_free(tmp_spine_root, capsys):
    rc = prep._cli(["testvale", "--date", "2026-10-01"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "lore/prep/2026-10-01-next-session.md"
    assert (spine.campaign_dir("testvale") / out).is_file()

    rc = prep._cli(["testvale", "--date", "2026-10-01"])
    assert rc == 0
    out2 = capsys.readouterr().out.strip()
    assert out2 == "lore/prep/2026-10-01-next-session-2.md"

    # --stdout prints the same bytes without writing anything
    rc = prep._cli(["testvale", "--date", "2026-10-01", "--stdout"])
    assert rc == 0
    printed = capsys.readouterr().out
    assert printed == (spine.campaign_dir("testvale")
                       / out2).read_text(encoding="utf-8")
    assert not (spine.campaign_dir("testvale") / "lore" / "prep" /
                "2026-10-01-next-session-3.md").exists()
