"""Quests as first-class entities (tickets/06-quests-first-class.md
acceptance list, the fast deterministic slice).

Covers: the frontmatter contract parses with everything-but-status
optional and extra keys ignored; unknown statuses degrade to hooked with
a finding; non-quest pages are invisible to the read model;
places/factions resolve through the links index (page place_id, unique
sim-name match, faction name/id); overdue detection for week numbers and
ISO dates with the resolved-status exemption; the PATCH route is a
reviewed short write (409 on lock, 409 on stale revision, snapshot
committed, body + unknown keys preserved); the briefing section lists
overdue first and filters active quests by intersection; the CLI
list/show/set round-trips.

Ticket 09 adds: one unparsable entity page costs only itself (panel
contract AND briefing both carry the error finding); abandoned exempts
from overdue; duplicate names are flagged and the CLI refuses them;
revision-omitted PATCH is a documented merge; CLI `set` finalizes like
the route (manifest records the snapshot)."""

import json

import briefing
import places
import quests
import pytest
import spine
import yaml


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _page(root, name, front, body="A quest of no consequence."):
    meta = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
    return _write(root / "lore" / "entities" / f"{name}.md",
                  f"---\n{meta}---\n\n{body}\n")


def _sim_state(clock=8, places=None, factions=None, events=None):
    return {
        "meta": {"clock": {"week": clock, "season": "spring"}},
        "places": places or [],
        "factions": factions or [],
        "events": events or [],
        "rumors": [],
    }


@pytest.fixture()
def camp(tmp_spine_root):
    """A writable campaign whose clock is week 8, with one linked place
    page, one sim-only place, two factions, and events in and out of the
    week-5..now range."""
    root = tmp_spine_root
    name = "questvale"
    d = root / name
    for sub in ("lore/entities", "simulation/chronicle"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    _write(d / "campaign.yaml",
           yaml.safe_dump({"name": name, "title": name}, sort_keys=False))
    _write(d / "simulation" / "state.yaml", yaml.safe_dump(_sim_state(
        clock=8,
        places=[
            {"id": "greywatch-hollow", "name": "Greywatch Hollow"},
            {"id": "greywatch-pass", "name": "Greywatch Pass"},
        ],
        factions=[
            {"id": "wardens", "name": "Stoneyvale Wardens",
             "goal": "collect the tithe"},
            {"id": "company", "name": "The Night Company",
             "goal": "move carts at night"},
        ],
        events=[
            {"week": 7, "actor": "wardens",
             "text": "The wardens swept the hollow.",
             "place_ids": ["greywatch-hollow"]},
            {"week": 2, "actor": "company",
             "text": "Night carts crossed the pass.",
             "place_ids": ["greywatch-pass"]},
            {"week": 8, "actor": "company",
             "text": "The company raised its prices."},
        ],
    ), sort_keys=False, allow_unicode=True, width=100))

    # the linked place page: its place_id is what quest `places:` names
    # resolve through the links index; its body wiki-links the quest, so
    # the quest's view has a backlink
    _page(root / name, "Greywatch Hollow",
          {"kind": "place", "name": "Greywatch Hollow",
           "place_id": "greywatch-hollow"},
          body="Tidy bone-heaps, then the deep passage. The tally-posts "
               "match [[The Trolls Tithe]].")
    # the full contract
    _page(root / name, "The Trolls Tithe",
          {"kind": "quest", "name": "The Troll's Tithe",
           "status": "active", "given_by": "Falburn",
           "places": ["Greywatch Hollow", "Greywatch Pass"],
           "factions": ["Stoneyvale Wardens"], "due": 9,
           "level": "2-4", "secret_note": "keep me"},
          body="Farms at the tree-line have lost livestock; someone is "
               "**counting the tithe**.")
    # minimal: everything except the kind is optional (status defaults)
    _page(root / name, "Word from the Bramblewood",
          {"kind": "quest", "name": "Word from the Bramblewood"})
    # unknown status degrades to hooked with a finding
    _page(root / name, "The Silence at Deephold",
          {"kind": "quest", "name": "The Silence at Deephold",
           "status": "in-progress"})
    # the overdue matrix: week number, ISO date, boundary, exemption
    _page(root / name, "Overdue Timer",
          {"kind": "quest", "name": "Overdue Timer", "status": "active",
           "due": 3})
    _page(root / name, "Date Timer",
          {"kind": "quest", "name": "Date Timer", "status": "active",
           "due": "2020-01-01"})
    _page(root / name, "Due This Week",
          {"kind": "quest", "name": "Due This Week", "status": "active",
           "due": 8})
    _page(root / name, "Future Date Timer",
          {"kind": "quest", "name": "Future Date Timer",
           "status": "active", "due": "2999-12-31"})
    _page(root / name, "Resolved Timer",
          {"kind": "quest", "name": "Resolved Timer", "status": "resolved",
           "due": 3})
    # active but intersecting nothing
    _page(root / name, "Unlinked Quest",
          {"kind": "quest", "name": "Unlinked Quest", "status": "active",
           "places": ["Nowhere At All"], "factions": ["Nobody"]})
    # active, intersecting by FACTION only
    _page(root / name, "Wardens Errand",
          {"kind": "quest", "name": "Warden's Errand", "status": "active",
           "factions": ["Stoneyvale Wardens"]})
    # non-quest pages are invisible to the read model
    _page(root / name, "Skarn", {"kind": "person", "name": "Skarn"})
    _page(root / name, "No Kind At All", {"name": "No Kind At All"})
    return name


# ------------------------------------------------------------------ loader


def test_frontmatter_contract_defaults_and_degradation(camp):
    doc = quests.load(camp)
    by_page = doc["by_page"]

    # the full contract parses; `level` rides along as free text and the
    # extra frontmatter key is ignored, never an error
    tithe = by_page["The Trolls Tithe"]
    assert tithe["status"] == "active"
    assert tithe["given_by"] == "Falburn"
    assert tithe["places"] == ["Greywatch Hollow", "Greywatch Pass"]
    assert tithe["factions"] == ["Stoneyvale Wardens"]
    assert tithe["due_week"] == 9 and tithe["due_date"] is None
    assert tithe["level"] == "2-4"
    assert "counting the tithe" in tithe["body"]
    assert tithe["revision"]  # the UI hands this back as expected_…

    # everything except status is optional: hooked is the default, silently
    assert by_page["Word from the Bramblewood"]["status"] == "hooked"
    assert by_page["Word from the Bramblewood"]["given_by"] == ""

    # unknown status degrades to hooked WITH a review finding
    silence = by_page["The Silence at Deephold"]
    assert silence["status"] == "hooked"
    assert silence["status_raw"] == "in-progress"
    rule = next(f for f in doc["findings"]
                if f["rule"] == "quests/unknown-status")
    assert "in-progress" in rule["message"]
    assert rule["source"] == "lore/entities/The Silence at Deephold.md"

    # non-quest pages are invisible
    assert "Skarn" not in by_page
    assert "No Kind At All" not in by_page
    assert "Greywatch Hollow" not in by_page

    # counts over the canonical four statuses
    assert doc["counts"] == {"hooked": 2, "active": 7, "resolved": 1,
                             "abandoned": 0}


def test_places_and_factions_resolve_through_the_links_index(camp):
    record = quests.load(camp)["by_page"]["The Trolls Tithe"]
    # Greywatch Hollow: a page whose place_id owns the identity
    assert "greywatch-hollow" in record["place_ids"]
    # Greywatch Pass: no page, but exactly one simulated place carries the
    # name — the SUGGESTED posture widens the intersection, merges nothing
    assert "greywatch-pass" in record["place_ids"]
    # the faction name resolves to the simulation's id
    assert record["faction_ids"] == ["wardens"]

    unlinked = quests.load(camp)["by_page"]["Unlinked Quest"]
    assert unlinked["places"] == ["Nowhere At All"]
    assert unlinked["place_ids"] == []
    assert unlinked["faction_ids"] == []


# ------------------------------------------------------------------ overdue


def test_overdue_weeks_dates_boundary_and_resolved_exemption(camp):
    """due in the past (week or date) while status is not resolved —
    listed as overdue; due week == the clock is the timer running out,
    not missed."""
    by_page = quests.load(camp)["by_page"]
    assert by_page["Overdue Timer"]["overdue"]        # week 3 < clock 8
    assert by_page["Date Timer"]["overdue"]           # 2020 < today
    assert not by_page["Due This Week"]["overdue"]    # week 8 == clock 8
    assert not by_page["Future Date Timer"]["overdue"]
    assert not by_page["Resolved Timer"]["overdue"]   # resolved exempts
    assert not by_page["The Trolls Tithe"]["overdue"]  # week 9 in the future


def test_unparsable_due_is_a_finding_never_a_crash(tmp_spine_root):
    root = tmp_spine_root / "dueweird"
    (root / "lore" / "entities").mkdir(parents=True)
    _write(root / "campaign.yaml",
           yaml.safe_dump({"name": "dueweird", "title": "dueweird"}))
    _page(root, "Weird Due",
          {"kind": "quest", "name": "Weird Due", "due": "sometime soon"})
    doc = quests.load("dueweird")
    record = doc["by_page"]["Weird Due"]
    assert record["due_week"] is None and record["due_date"] is None
    assert record["due_invalid"] == "sometime soon"
    assert any(f["rule"] == "quests/bad-due" for f in doc["findings"])
    assert not record["overdue"]


# ---------------------------------------------------------------- view


def test_quest_view_page_and_backlinks(camp):
    view = quests.quest_view(camp, "The Trolls Tithe")
    assert view["quest"]["name"] == "The Troll's Tithe"
    sources = {b["source_file"] for b in view["backlinks"]}
    assert "lore/entities/Greywatch Hollow.md" in sources
    with pytest.raises(quests.QuestNotFound):
        quests.quest_view(camp, "No Such Quest")


# ------------------------------------------------------------------ PATCH


def test_patch_route_is_a_reviewed_short_write(client, camp):
    stem = "The%20Trolls%20Tithe"
    url = f"/api/campaign/{camp}/quests/{stem}"
    page = spine.campaign_dir(camp) / "lore" / "entities" / \
        "The Trolls Tithe.md"

    r = client.get(url)
    assert r.status_code == 200
    revision = r.json()["quest"]["revision"]

    # stale revision -> 409, nothing written
    r = client.patch(url, json={"status": "resolved",
                                "expected_page_revision": "0" * 64})
    assert r.status_code == 409
    assert "status: resolved" not in page.read_text(encoding="utf-8")

    # campaign lock held -> 409 (a week job may hold it for its whole run)
    with spine.campaign_lock(camp):
        r = client.patch(url, json={"status": "resolved"})
        assert r.status_code == 409
    assert "status: resolved" not in page.read_text(encoding="utf-8")

    # the current revision passes: published, snapshot committed, page
    # merged (body + unknown frontmatter key preserved)
    r = client.patch(url, json={"status": "resolved",
                                "expected_page_revision": revision})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "published" and body["status"] == "resolved"
    assert body["snapshot"]["state"] in ("committed", "no_change")
    text = page.read_text(encoding="utf-8")
    assert "status: resolved" in text
    assert "secret_note: keep me" in text
    assert "counting the tithe" in text

    # a second flip with the fresh revision is a noop, not a new snapshot
    revision = quests.load(camp)["by_page"]["The Trolls Tithe"]["revision"]
    r = client.patch(url, json={"status": "resolved",
                                "expected_page_revision": revision})
    assert r.status_code == 200 and r.json()["state"] == "noop"

    # unknown status is a 422 that names the four values
    r = client.patch(url, json={"status": "cancelled"})
    assert r.status_code == 422 and "hooked" in r.json()["error"]
    # unknown page is a 404
    r = client.patch(f"/api/campaign/{camp}/quests/No%20Such%20Quest",
                     json={"status": "active"})
    assert r.status_code == 404
    # unknown campaign is a 404
    assert client.get("/api/campaign/nope/quests").status_code == 404


def test_quest_routes_surface_findings_and_view(client, camp):
    r = client.get(f"/api/campaign/{camp}/quests")
    assert r.status_code == 200
    doc = r.json()
    assert "quests/unknown-status" in {f["rule"] for f in doc["findings"]}
    assert doc["counts"]["hooked"] == 2

    r = client.get(f"/api/campaign/{camp}/quests/The%20Trolls%20Tithe")
    assert r.status_code == 200
    view = r.json()
    assert view["quest"]["status"] == "active"
    assert any(b["source_file"] == "lore/entities/Greywatch Hollow.md"
               for b in view["backlinks"])

    # a hostile page segment is a bounded 422, never a 500
    r = client.get(f"/api/campaign/{camp}/quests/foo*bar")
    assert r.status_code == 422


# ---------------------------------------------------------------- briefing


def test_briefing_quests_section_intersection_and_overdue_first(camp):
    doc = briefing.build(camp, since_week=5)
    section = doc["quests"]
    # overdue first, always: week-due timers, then date-due
    assert [q["page"] for q in section["overdue"]] == \
        ["Overdue Timer", "Date Timer"]
    # active quests only when places/factions intersect the covered range
    active = {q["page"] for q in section["active"]}
    assert active == {"The Trolls Tithe", "Wardens Errand"}
    # the night carts' pass event is week 2 — before the range — so the
    # pass alone would not qualify; the hollow event (week 7) does
    assert "Unlinked Quest" not in active
    # the hooked count is one honest line, not a list
    assert section["hooks_not_shown"] == 2

    md = doc["markdown"]
    assert "## Quests (open story)" in md
    assert "OVERDUE" in md and "due week 3" in md
    assert "2 hooks not shown" in md
    assert md.index("OVERDUE") < md.index("The Troll's Tithe"), \
        "overdue rows come before the active list"


def test_briefing_quests_section_survives_an_empty_range(camp):
    """An open timer does not care what week it is: with no events in the
    range the section still lists overdue quests instead of the
    'nothing to report' line."""
    doc = briefing.build(camp, since_week=100)
    assert doc["items"] == []
    assert [q["page"] for q in doc["quests"]["overdue"]] == \
        ["Overdue Timer", "Date Timer"]
    assert doc["quests"]["active"] == []
    md = doc["markdown"]
    assert "OVERDUE" in md
    assert "Nothing to report" not in md


# --------------------------------------------------------------------- CLI


def test_cli_list_show_set(camp, capsys):
    rc = quests._cli([camp, "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "active (7)" in out
    assert "hooked (2)" in out
    assert "OVERDUE" in out
    assert "The Troll's Tithe" in out
    assert "unknown status" in out  # findings are printed, not hidden

    rc = quests._cli([camp, "show", "The Trolls Tithe", "--json"])
    assert rc == 0
    view = json.loads(capsys.readouterr().out)
    assert view["quest"]["status"] == "active"
    assert view["quest"]["given_by"] == "Falburn"

    rc = quests._cli([camp, "set", "The Trolls Tithe", "resolved"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "status -> resolved" in out and "(published)" in out
    assert quests.load(camp)["by_page"]["The Trolls Tithe"]["status"] == \
        "resolved"

    # flipping to the status it already has is a noop, not a snapshot
    rc = quests._cli([camp, "set", "The Trolls Tithe", "resolved"])
    assert rc == 0
    assert "(noop)" in capsys.readouterr().out

    # an unknown status is refused by the same gate the route uses
    with pytest.raises(quests.QuestError):
        quests._cli([camp, "set", "The Trolls Tithe", "cancelled"])


# ------------------------------------------------------- ticket 09: hand-edits


def test_one_bad_entity_page_costs_only_itself(client, camp):
    """The read model's contract: a hand-edit must never make quests
    vanish. A broken NPC page (not even a quest) degrades to one error
    finding while every quest stays listed."""
    _write(
        spine.campaign_dir(camp) / "lore" / "entities" / "Broken NPC.md",
        "---\nname: [unclosed\n  bad: yaml: here\n---\n\nProse.\n")
    doc = quests.load(camp)
    assert doc["counts"]["active"] == 7          # every quest survived
    bad = [f for f in doc["findings"]
           if f["rule"] == "quests/lore-unreadable"]
    assert len(bad) == 1 and "Broken NPC.md" in bad[0]["message"]
    assert "Broken NPC" not in doc["by_page"]
    # the view route answers too — no 422/500 over one bad page
    r = client.get(f"/api/campaign/{camp}/quests")
    assert r.status_code == 200
    assert r.json()["counts"]["active"] == 7

    # a quest page whose frontmatter does not parse is caught at the entity
    # parse — the same lore-unreadable rule, still only one finding
    _write(spine.campaign_dir(camp) / "lore" / "entities" / "Broken Quest.md",
           "---\nkind: quest\nstatus: [oops\n---\n\nProse.\n")
    doc = quests.load(camp)
    assert doc["counts"]["active"] == 7
    assert any(f["rule"] == "quests/lore-unreadable"
               and "Broken Quest.md" in f["message"]
               for f in doc["findings"])
    assert "Broken Quest" not in doc["by_page"]


def test_page_vanishing_mid_read_costs_only_itself(camp, monkeypatch):
    """The race guard: a quest page that fails its OWN read (deleted or
    locked between the entity index and the per-page snapshot) degrades to
    quests/page-unreadable — the finding the (OSError, ValueError,
    PlaceError) tuple exists to catch."""
    _page(spine.campaign_dir(camp).parent / camp, "Vanishing Quest",
          {"kind": "quest", "name": "Vanishing Quest", "status": "active"})
    real = quests._page_snapshot

    def flaky(path, expected):
        if path.name == "Vanishing Quest.md":
            raise OSError("vanished mid-read")
        return real(path, expected)

    monkeypatch.setattr(quests, "_page_snapshot", flaky)
    doc = quests.load(camp)
    assert doc["counts"]["active"] == 7        # only the vanisher is gone
    gone = [f for f in doc["findings"]
            if f["rule"] == "quests/page-unreadable"]
    assert len(gone) == 1 and "Vanishing Quest.md" in gone[0]["message"]


def test_findings_surface_even_with_zero_quests(client, tmp_spine_root):
    """The empty campaign with one bad page: quests == [] but findings
    carry the reason — the route contract the panel's empty state now
    renders instead of 'create a quest' advice."""
    root = tmp_spine_root / "brokenvale"
    (root / "lore" / "entities").mkdir(parents=True)
    _write(root / "campaign.yaml",
           yaml.safe_dump({"name": "brokenvale", "title": "brokenvale"}))
    _write(root / "simulation" / "state.yaml",
           yaml.safe_dump(_sim_state(), sort_keys=False))
    _write(root / "lore" / "entities" / "Broken NPC.md",
           "---\nname: {nope\n---\n\nProse.\n")
    r = client.get("/api/campaign/brokenvale/quests")
    assert r.status_code == 200
    doc = r.json()
    assert doc["quests"] == []
    assert any(f["rule"] == "quests/lore-unreadable"
               for f in doc["findings"])

    # and the briefing renders the error finding — the section must not
    # vanish just because there are no quests to list
    md = briefing.build("brokenvale", since_week=0)["markdown"]
    assert "## Quests (open story)" in md
    assert "Quest finding" in md and "Broken NPC.md" in md


def test_abandoned_is_exempt_from_overdue(camp):
    """resolved ended the story; abandoned walked away — neither owes a
    timer, or an abandoned quest pollutes the overdue block forever."""
    _page(spine.campaign_dir(camp).parent / camp, "Abandoned Timer",
          {"kind": "quest", "name": "Abandoned Timer", "status": "abandoned",
           "due": 3})
    by_page = quests.load(camp)["by_page"]
    assert not by_page["Abandoned Timer"]["overdue"]
    assert by_page["Overdue Timer"]["overdue"]      # active still is
    section = quests.briefing_section(camp, since_week=5)
    assert "Abandoned Timer" not in {q["page"] for q in section["overdue"]}


# ------------------------------------------- ticket 23: degradation honesty


def test_broken_state_yaml_degrades_to_unknown_clock(client, camp):
    """P3-18: quests live in lore pages, not state.yaml — a YAML slip there
    costs only the clock/faction resolution. The Quests panel renders its
    rows with an unknown-clock finding instead of a 422 error panel; week
    timers read unknown (never overdue), date timers still run on the
    calendar."""
    (spine.campaign_dir(camp) / "simulation" / "state.yaml").write_text(
        "meta: {clock: [unclosed\n", encoding="utf-8")

    r = client.get(f"/api/campaign/{camp}/quests")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert len(doc["quests"]) == 10, "every quest row survives"
    unreadable = [f for f in doc["findings"]
                  if f["rule"] == "quests/state-unreadable"]
    assert len(unreadable) == 1
    assert unreadable[0]["source"] == "simulation/state.yaml"
    assert "state.yaml" in unreadable[0]["message"]
    assert not doc["by_page"]["Overdue Timer"]["overdue"], \
        "no clock -> a week timer cannot be judged"
    assert doc["by_page"]["Date Timer"]["overdue"], \
        "a date timer runs on the calendar, not the clock"
    # faction resolution degraded too — the names stay raw on the record
    assert doc["by_page"]["Wardens Errand"]["faction_ids"] == []

    # the briefing section degrades the same way when driven directly
    # (briefing.build itself needs state for events, so its OWN read still
    # raises there — out of this read model's hands)
    section = quests.briefing_section(camp, since_week=5)
    assert [q["page"] for q in section["overdue"]] == ["Date Timer"]
    assert any(f["rule"] == "quests/state-unreadable"
               for f in section["findings"])


def test_bomd_quest_page_reads_and_is_flagged(client, camp):
    """P3-19: a UTF-8 BOM before the frontmatter fence used to parse the
    page as frontmatter-less — the quest silently lost its kind and
    vanished from the read model with NO finding. The fence tolerates the
    BOM now, so the page reads, and the finding tells the GM their editor
    wrote 'UTF-8 with BOM'."""
    page = spine.campaign_dir(camp) / "lore" / "entities" / "Bombed Quest.md"
    page.write_bytes(
        "\ufeff---\nkind: quest\nname: Bombed Quest\nstatus: active\n"
        "due: 3\n---\n\nCounting the days.\n".encode("utf-8"))

    doc = quests.load(camp)
    record = doc["by_page"].get("Bombed Quest")
    assert record is not None, "the BOM'd page must appear in the read model"
    assert record["status"] == "active"
    assert record["due_week"] == 3
    assert record["overdue"]                # clock 8, due week 3
    bom = [f for f in doc["findings"] if f["rule"] == "quests/page-bom"]
    assert len(bom) == 1
    assert bom[0]["source"] == "lore/entities/Bombed Quest.md"
    assert "BOM" in bom[0]["message"]

    # the panel route serves it too — never a silent absence
    r = client.get(f"/api/campaign/{camp}/quests")
    assert r.status_code == 200, r.text
    assert "Bombed Quest" in {q["page"] for q in r.json()["quests"]}


def test_duplicate_names_flagged_and_cli_refuses(tmp_spine_root, capsys):
    root = tmp_spine_root / "dupevale"
    (root / "lore" / "entities").mkdir(parents=True)
    _write(root / "campaign.yaml",
           yaml.safe_dump({"name": "dupevale", "title": "dupevale"}))
    _write(root / "simulation" / "state.yaml",
           yaml.safe_dump(_sim_state(), sort_keys=False))
    _page(root, "Tithe A", {"kind": "quest", "name": "The Same Tithe"})
    _page(root, "Tithe B", {"kind": "quest", "name": "The Same Tithe"})

    doc = quests.load("dupevale")
    assert len(doc["quests"]) == 2
    dup = [f for f in doc["findings"]
           if f["rule"] == "quests/duplicate-name"]
    assert len(dup) == 1 and "Tithe A.md" in dup[0]["message"] \
        and "Tithe B.md" in dup[0]["message"]

    # the display name is refused — guessing would flip the wrong quest
    assert quests._cli(["dupevale", "show", "The Same Tithe"]) == 2
    err = capsys.readouterr().err
    assert "names 2 quests" in err and "Tithe A" in err and "Tithe B" in err
    assert quests._cli(["dupevale", "set", "The Same Tithe", "active"]) == 2
    # the page stem stays unambiguous and works
    assert quests._cli(["dupevale", "set", "Tithe B", "active"]) == 0
    assert "status -> active" in capsys.readouterr().out


def test_patch_revision_omitted_is_a_documented_merge(client, camp):
    """No expected_page_revision = merge semantics against the current
    bytes (the places PATCH contract) — pinned here so the behavior is a
    decision, not an accident."""
    url = f"/api/campaign/{camp}/quests/The%20Silence%20at%20Deephold"
    r = client.patch(url, json={"status": "active"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "published"
    assert quests.load(camp)["by_page"]["The Silence at Deephold"] \
        ["status"] == "active"


def test_cli_set_finalizes_like_the_route(camp):
    """CLI `set` runs snapshot_result + after_snapshot: on a committed
    snapshot the staging is pruned and the manifest's snapshot bookkeeping
    ran — canon.snapshot alone left the staged transaction dir behind
    forever (ticket 09)."""
    assert quests._cli([camp, "set", "Word from the Bramblewood",
                        "active"]) == 0
    page = spine.campaign_dir(camp) / "lore" / "entities" / \
        "Word from the Bramblewood.md"
    assert "status: active" in page.read_text(encoding="utf-8")
    tx_root = spine.campaign_dir(camp) / ".studio-transactions"
    leftovers = sorted(
        p.name for p in tx_root.iterdir()
        if p.is_dir() and p.name != "receipts") \
        if tx_root.is_dir() else []
    assert leftovers == [], \
        f"CLI set must prune committed staging like the route: {leftovers}"


# -------------------------------------------- ticket 10: byte-preserving writes

# the Obsidian-authored worst case: CRLF file-wide, an inline comment ON
# the status line, a blank-valued key, and body edge-whitespace
_HAND_EDITED_QUEST = (
    "---\r\n"
    "kind: quest\r\n"
    "name: The Trolls Tithe\r\n"
    "status: hooked   # hooked | active | resolved | abandoned\r\n"
    "given_by:\r\n"
    "secret_note: keep me\r\n"
    "---\r\n"
    "\r\n"
    "Farms at the tree-line.  \r\n"
    "Someone is counting.\r\n"
)


def test_status_flip_is_line_surgery_not_reserialization(camp):
    page = spine.campaign_dir(camp) / "lore" / "entities" / \
        "The Trolls Tithe.md"
    page.write_bytes(_HAND_EDITED_QUEST.encode("utf-8"))
    out = quests.set_status(camp, "The Trolls Tithe", "active")
    assert out["state"] == "published"
    after = page.read_bytes().decode("utf-8")
    expected = _HAND_EDITED_QUEST.replace(
        "status: hooked   # hooked | active | resolved | abandoned",
        "status: active # hooked | active | resolved | abandoned")
    assert after == expected, "only the status line may change"


def test_status_flips_round_trip_like_hand_edits(camp):
    """hooked -> active -> resolved leaves exactly the bytes the same two
    edits would leave in a text editor (CRLF endings intact file-wide)."""
    page = spine.campaign_dir(camp) / "lore" / "entities" / \
        "The Trolls Tithe.md"
    page.write_bytes(_HAND_EDITED_QUEST.encode("utf-8"))
    quests.set_status(camp, "The Trolls Tithe", "active")
    quests.set_status(camp, "The Trolls Tithe", "resolved")
    after = page.read_bytes().decode("utf-8")
    expected = _HAND_EDITED_QUEST.replace(
        "status: hooked   # hooked | active | resolved | abandoned",
        "status: resolved # hooked | active | resolved | abandoned")
    assert after == expected
    # and the read model still sees the page (the surgery parses back)
    record = quests.load(camp)["by_page"]["The Trolls Tithe"]
    assert record["status"] == "resolved"


# --------------------------------- ticket 22: page surgery completion (R3/R4)

# R3: a quoted scalar whose closing quote sits two lines down — valid YAML
# (parses 'hooked '), but not line-local. Rewriting only the first line
# used to leave the dangling '"' folding into the NEW value
# ('active "') — a silent wrong-write the read model degraded to hooked.
_MULTILINE_QUOTED_STATUS = (
    "---\n"
    "kind: quest\n"
    "name: The Toll Road\n"
    "status: \"hooked\n"
    "  \"\n"
    "---\n"
    "\n"
    "Villagers pay twice.\n"
)


def test_status_flip_on_multiline_quoted_value_never_leaves_a_dangling_quote(
        camp):
    page = spine.campaign_dir(camp) / "lore" / "entities" / \
        "The Toll Road.md"
    page.write_bytes(_MULTILINE_QUOTED_STATUS.encode("utf-8"))
    out = quests.set_status(camp, "The Toll Road", "active")
    assert out["state"] == "published"
    after = page.read_bytes().decode("utf-8")
    # the change is not line-local, so the write re-serializes — and the
    # re-serialized page re-parses to EXACTLY the asked-for status
    assert after == ("---\nkind: quest\nname: The Toll Road\n"
                     "status: active\n---\n\nVillagers pay twice.\n")
    meta, _body = places._frontmatter_of(after)
    assert meta["status"] == "active"
    # the read model agrees (the R3 symptom was: API said active, the
    # panel silently kept showing hooked)
    record = quests.load(camp)["by_page"]["The Toll Road"]
    assert record["status"] == "active"


# R4: a top-level comment line used to fail the surgery pre-scan and route
# the whole write to the lossy rebuild, which drops comments, blank-valued
# keys and body edge whitespace in one routine status flip.
_COMMENTED_QUEST = (
    "---\n"
    "kind: quest\n"
    "name: The Quiet Ledger\n"
    "status: hooked\n"
    "# Keep the timer secret.\n"
    "given_by:\n"
    "---\n"
    "\n"
    "  An indent opens the ledger.  \n"
)


def test_status_flip_keeps_comments_blank_keys_and_body_whitespace(camp):
    page = spine.campaign_dir(camp) / "lore" / "entities" / \
        "The Quiet Ledger.md"
    page.write_bytes(_COMMENTED_QUEST.encode("utf-8"))
    out = quests.set_status(camp, "The Quiet Ledger", "active")
    assert out["state"] == "published"
    after = page.read_bytes().decode("utf-8")
    # a comment line is safe unchanged bytes: only the status line changes
    expected = _COMMENTED_QUEST.replace("status: hooked",
                                        "status: active")
    assert after == expected
    assert "# Keep the timer secret." in after
    assert "\ngiven_by:\n" in after
    assert "  An indent opens the ledger.  \n" in after
