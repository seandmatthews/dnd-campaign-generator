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

import briefing
import knowledge
import prep
import pytest
import spine
import yaml

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


# ------------------------------------------------ ticket 14: the whole truth


def test_decisions_read_past_the_briefing_cap(client, tmp_spine_root):
    """>=10 week-bearing items crowd the week-less decision items out of
    the briefing's capped slice; prep reads the UNCAPPED decisions key,
    says what the events section left out, and never prints a false
    'Nothing awaiting a decision'."""
    d = tmp_spine_root / "testvale"
    state_path = d / "simulation" / "state.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    for w in range(10, 20):
        state["events"].append({"week": w, "actor": "wardens",
                                "text": f"escrow event {w}"})
    state_path.write_text(
        yaml.safe_dump(state, sort_keys=False, allow_unicode=True,
                       width=100), encoding="utf-8")
    # a pending review file = a week-less decision item
    (d / "lore").mkdir(exist_ok=True)
    (d / "lore" / "pending-9.yaml").write_text(
        "entries:\n- id: pw9-1\n  when: week 9\n  title: T\n  text: x\n"
        "  source: hand\n", encoding="utf-8")

    doc = prep.build("testvale")
    assert doc["events_omitted"] > 0, "the fixture must exceed the cap"
    assert doc["decisions"], "decisions must survive the cap (ticket 14)"
    text = prep.markdown(doc)
    assert "Nothing awaiting a decision" not in text
    assert "pending review" in text
    # the events section says what it left out instead of lying by omission
    assert "not shown" in text


def test_frozen_today_pins_quest_overdue_across_midnight(tmp_spine_root):
    """The determinism contract holds across a due-date midnight: the
    frozen date reaches the quests' overdue check, so rebuilding
    yesterday's file reproduces yesterday's bytes even though the due
    date flipped overdue under the real clock."""
    d = tmp_spine_root / "testvale"
    (d / "lore" / "entities").mkdir(parents=True, exist_ok=True)
    (d / "lore" / "entities" / "Due Tomorrow.md").write_text(
        "---\n" + yaml.safe_dump(
            {"kind": "quest", "name": "Due Tomorrow", "status": "active",
             "due": "2026-10-02"}, sort_keys=False) +
        "---\n\nBeat the clock.\n", encoding="utf-8")
    frozen = datetime.date(2026, 10, 1)   # 2026-10-02 is tomorrow: open
    t1 = prep.markdown(prep.build("testvale", today=frozen))
    t2 = prep.markdown(prep.build("testvale", today=frozen))
    assert t1 == t2
    assert "OVERDUE" not in t1, "not overdue on the frozen date"
    # a date past the due date DOES read overdue — the caller's date
    # decides, not the wall clock (strict < : due-day itself is the timer
    # running out, not missed)
    later = prep.markdown(prep.build("testvale",
                                     today=datetime.date(2026, 10, 5)))
    assert "OVERDUE" in later


def test_since_week_and_fact_week_bounds(client, tmp_spine_root):
    """since_week is bounded at the route boundary (>= 0), fact weeks are
    campaign weeks (>= 1), and the record path validates like the
    route."""
    r = client.get("/api/campaign/testvale/prep/markdown?since_week=-5")
    assert r.status_code == 422
    r = client.post("/api/campaign/testvale/prep?since_week=-1")
    assert r.status_code == 422
    r = client.post("/api/campaign/testvale/knowledge",
                    json={"text": "x", "visibility": "public", "week": 0})
    assert r.status_code == 422, r.text
    assert "campaign week" in r.json()["error"]
    doc = knowledge.load("testvale")
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.apply_fact_records(
            "testvale", doc, set(),
            [{"text": "x", "visibility": "public", "week": "3"}])


# ------------------------------------------------- ticket 23: honest degradation


def test_broken_sole_quest_page_named_not_declared_absent(client,
                                                          tmp_spine_root):
    """R5: when the ONLY quest page cannot be read, the prep page carries
    the briefing's error finding (naming the file) instead of dropping it
    and printing 'No quest pages yet' over a quest that exists but cannot
    be read."""
    d = tmp_spine_root / "testvale"
    (d / "lore" / "entities").mkdir(parents=True, exist_ok=True)
    (d / "lore" / "entities" / "The Only Quest.md").write_text(
        "---\nkind: quest\nname: [unclosed\n---\n\nProse.\n",
        encoding="utf-8")

    text = prep.markdown(prep.build("testvale"))
    assert "## Active quests" in text
    assert "The Only Quest.md" in text, "the finding names the unreadable file"
    assert "No quest pages yet" not in text
    # the finding rode the doc, not just the markdown
    assert any(f["level"] == "error"
               for f in prep.build("testvale")["quests"]["findings"])

    # the briefing side already rendered it (ticket 09) — and never
    # answers 'nothing to report' over an unreadable quest
    doc = briefing.build("testvale")
    md = doc["markdown"]
    assert "Quest finding" in md and "The Only Quest.md" in md
    assert "No quest pages yet" not in md
    assert "Nothing to report" not in md


def test_broken_knowledge_yaml_costs_only_its_sections(client, tmp_spine_root):
    """P3-17: a hand-mangled simulation/knowledge.yaml degrades the
    sections it feeds — the briefing's stale facts, prep's reveal list —
    and never kills either build. Both carry a finding naming the file."""
    (tmp_spine_root / "testvale" / "simulation" / "knowledge.yaml").write_text(
        "facts: [unclosed\n", encoding="utf-8")

    doc = briefing.build("testvale")
    assert any(i["kind"] == "event" for i in doc["items"]), \
        "the other briefing sections survive"
    assert [f["rule"] for f in doc["knowledge_findings"]] == \
        ["knowledge/unreadable"]
    assert doc["knowledge_findings"][0]["source"] == \
        "simulation/knowledge.yaml"
    assert "Knowledge finding" in doc["markdown"]

    r = client.get("/api/campaign/testvale/briefing")
    assert r.status_code == 200, r.text
    assert r.json()["knowledge_findings"]

    text = prep.markdown(prep.build("testvale"))
    assert "## Waiting to be revealed" in text
    assert "could not be read" in text
    assert "No public facts waiting" not in text, \
        "the reminder list must not claim 'nothing waiting' over an " \
        "unreadable store"
    # every other section still compiles
    assert "## Active quests" in text and "## Open decisions" in text


def test_since_week_above_the_clock_covers_no_weeks(client, tmp_spine_root):
    """P3-23: since_week above the clock is an over-narrow range, not an
    inverted one — 'covers weeks 11–3' on a week-3 clock becomes an empty
    range that says why (clamped, documented in prep.build)."""
    r = client.get("/api/campaign/testvale/prep/markdown?since_week=10")
    assert r.status_code == 200, r.text
    assert "covers_weeks: []" in r.text
    assert "11\u20133" not in r.text, "the range must never run backwards"
    assert "covers no weeks" in r.text
    # the boundary: changes AFTER the clock week cover nothing either;
    # one below the clock is the last real week
    assert "covers_weeks: []" in client.get(
        "/api/campaign/testvale/prep/markdown?since_week=3").text
    assert "covers_weeks: [3, 3]" in client.get(
        "/api/campaign/testvale/prep/markdown?since_week=2").text
