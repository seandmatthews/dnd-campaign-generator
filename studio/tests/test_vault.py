"""The vault (obsidian-integration.md stages 1-3): entity pages, the link
index, the lazy migrations, companion notes, and where the findings land.

No engine calls: the city job runs against a fake gen-city (same shape as
test_maps.py); the link index and migrations are pure file work on writable
fixture copies.
"""

from pathlib import Path

import pytest
import yaml

import doctor
import engines
import links
import spine
from conftest import wait_for_job


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fake_gen_city(tmp_spine_root, monkeypatch):
    """A gen-city.mjs that writes one svg and nothing else."""
    (tmp_spine_root / "testvale" / "world" / "source.map").write_text(
        "fmg", encoding="utf-8")

    def fake_run_node(engine_root, *args, timeout=600, on_line=None):
        out = args[list(args).index("--out") + 1]
        _write(f"{out}/dunmore.svg", "<svg/>")
        return 0, []

    monkeypatch.setattr(engines, "run_node", fake_run_node)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")


# ------------------------------------------------------------- the parser


def test_parse_entity_page_frontmatter_and_note(spine_root):
    page = spine.parse_entity_page(
        spine_root / "testvale" / "lore" / "entities" / "The Vane.md")
    assert page["name"] == "The Vane"
    assert page["kind"] == "places"
    assert page["note"] == "the river that makes the vale worth having"
    assert page["file"] == "lore/entities/The Vane.md"


def test_parse_entity_page_without_frontmatter_degrades_to_stem(tmp_spine_root):
    _write(tmp_spine_root / "testvale" / "lore" / "entities" / "Rustwig.md",
           "A bridge troll of no fixed abode.\n\n## Wants\nTolls, but fair ones.\n")
    page = spine.parse_entity_page(
        tmp_spine_root / "testvale" / "lore" / "entities" / "Rustwig.md")
    assert page["name"] == "Rustwig"  # the stem IS the name
    assert page["note"].startswith("A bridge troll")
    assert "## Wants" in page["body"]


def test_parse_entity_page_bad_frontmatter_is_readable_4xx(tmp_spine_root):
    _write(tmp_spine_root / "testvale" / "lore" / "entities" / "Broken.md",
           "---\nname: [unclosed\n---\n\nbody\n")
    try:
        spine.parse_entity_page(
            tmp_spine_root / "testvale" / "lore" / "entities" / "Broken.md")
    except spine.CampaignFileError as e:
        assert "Broken.md" in str(e)
    else:
        raise AssertionError("bad frontmatter must raise CampaignFileError")


def test_entity_page_filename_rules():
    assert spine.entity_page_filename("House Ferrand") == "House Ferrand.md"
    assert spine.entity_page_filename("Mt. Vane") == "Mt. Vane.md"
    assert spine.entity_page_filename("ends ") == "ends.md"  # sanitized
    assert spine.entity_page_filename("a/b") == "a b.md"     # sanitized
    for bad in ("con", "ends."):  # unnameable even sanitized
        try:
            spine.entity_page_filename(bad)
        except spine.CampaignError:
            continue
        raise AssertionError(f"{bad!r} must be refused")


# ---------------------------------------------------------- legacy loading


def test_legacy_grouped_yaml_still_loads(tmp_spine_root):
    """The shapes migration retires stay readable — a campaign zipped off an
    old install degrades gracefully instead of vanishing."""
    lore = tmp_spine_root / "testvale" / "lore"
    entities_md = lore / "entities"
    for p in entities_md.glob("*.md"):
        p.unlink()  # tmp copy; the shared fixture stays untouched
    _write(lore / "entities.yaml",
           "people:\n  - name: Corvin Dane\n    note: sells rope\n"
           "places:\n  - name: The Vane\n    alias: the river\n    note: x\n")
    by_name = {e["name"]: e for e in spine.load_entities("testvale")}
    assert by_name["Corvin Dane"]["kind"] == "people"  # the group key IS the kind
    assert by_name["The Vane"]["aliases"] == ["the river"]


def test_legacy_degenerate_shapes_never_explode(tmp_spine_root):
    lore = tmp_spine_root / "testvale" / "lore"
    for p in (lore / "entities").glob("*.md"):
        p.unlink()  # pages win when present; the yaml only matters alone
    for text in ("", "entities: null", "just a string", "- a\n- b\n"):
        _write(lore / "entities.yaml", text)
        assert spine.load_entities("testvale") == []
    _write(lore / "entities.yaml", "entities:\n  - name: X\n    kind: k\n")
    assert [e["name"] for e in spine.load_entities("testvale")] == ["X"]


# ------------------------------------------------------------- migration


def test_open_migrates_legacy_yaml_once(client, tmp_spine_root):
    lore = tmp_spine_root / "testvale" / "lore"
    _write(lore / "entities.yaml",
           "entities:\n  - id: kestra\n    kind: person\n    name: Kestra\n"
           "    aliases: [Kes]\n    note: ferries cattle across the Vane\n")
    r = client.get("/api/campaign/testvale")  # opening the campaign migrates
    assert r.status_code == 200
    assert not (lore / "entities.yaml").is_file()  # migrate-AND-delete
    page = lore / "entities" / "Kestra.md"
    assert page.is_file()
    doc = yaml.safe_load(page.read_text(encoding="utf-8").split("---")[1])
    assert doc == {"id": "kestra", "kind": "person", "name": "Kestra",
                   "aliases": ["Kes"]}
    # second open: nothing to do, no error
    assert client.get("/api/campaign/testvale").status_code == 200


def test_migration_never_clobbers_existing_pages(tmp_spine_root):
    _write(tmp_spine_root / "testvale" / "lore" / "entities" / "Kestra.md",
           "---\nname: Kestra\n---\n\nHand-written lore.\n")
    _write(tmp_spine_root / "testvale" / "lore" / "entities.yaml",
           "entities:\n  - name: Kestra\n    note: old yaml note\n"
           "  - name: Other\n    note: second\n")
    assert spine.migrate_entities_yaml_to_pages("testvale") == 2
    assert "Hand-written lore." in (tmp_spine_root / "testvale" / "lore" /
                                    "entities" / "Kestra.md").read_text("utf-8")
    assert (tmp_spine_root / "testvale" / "lore" / "entities" /
            "Kestra (2).md").is_file()


def test_migration_case_variant_collision_gets_suffix(tmp_spine_root):
    """Windows filenames are case-insensitive: 'The Reliquary' vs
    'the Reliquary' must not fight over one file."""
    entities = tmp_spine_root / "testvale" / "lore" / "entities"
    for p in entities.glob("*.md"):
        p.unlink()
    _write(tmp_spine_root / "testvale" / "lore" / "entities.yaml",
           "entities:\n  - name: The Reliquary\n    note: a\n"
           "  - name: the Reliquary\n    note: b\n")
    spine.migrate_entities_yaml_to_pages("testvale")
    assert {p.name for p in entities.glob("*.md")} == \
        {"The Reliquary.md", "the Reliquary (2).md"}


# ------------------------------------------------------------- link index


def _linker_page(root, name, body):
    _write(root / "testvale" / "lore" / "entities" / f"{name}.md",
           f"---\nname: {name}\nkind: person\n---\n\n{body}\n")


def test_index_resolves_aliases_and_chronicles(tmp_spine_root):
    _linker_page(tmp_spine_root, "Kestra",
                 "Apprentice to [[Alweric Tealeaf]] at [[The Vane|the river]]; "
                 "saw the crossing ([[0003-the-ash-crossing]]). [[Nonesuch]] is nowhere.")
    idx = links.build_index("testvale")
    # alias + display name resolve to the same page
    assert idx["resolve"][links.normalize_key("Alweric Tealeaf")]["file"] == \
        "lore/entities/Alweric Tealeaf.md"
    # chronicles are resolvable targets — the Appearances pattern must not dangle
    assert idx["resolve"]["0003-the-ash-crossing"]["source"] == "chronicle"
    assert [d["target"] for d in idx["dangling"]] == ["Nonesuch"]
    # backlinks: Kestra's page links the warden and the chronicle
    assert idx["backlinks"]["lore/entities/Alweric Tealeaf.md"] == [{
        "source_file": "lore/entities/Kestra.md",
        "source_kind": "entity",
        "source_name": "Kestra",
        "line": 6,
        "target": "Alweric Tealeaf",
    }]
    chron = idx["backlinks"]["simulation/chronicle/0003-the-ash-crossing.md"]
    assert chron[0]["source_kind"] == "entity"


def test_index_collision_finding(tmp_spine_root):
    _linker_page(tmp_spine_root, "Kestra", "one")
    _write(tmp_spine_root / "testvale" / "lore" / "entities" / "Kes.md",
           "---\nname: Kes\naliases: [Kestra]\n---\n\nalso claims the alias\n")
    assert "links/collision" in {f["rule"] for f in links.findings("testvale")}


def test_page_route_backlinks_and_review_findings(client, tmp_spine_root):
    _linker_page(tmp_spine_root, "Kestra",
                 "[[Alweric Tealeaf]] taught me the ferry. "
                 "[[Ghost of the Vane]] haunts still.")
    r = client.get("/api/campaign/testvale/lore/pages/Alweric Tealeaf")
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["kind"] == "people"  # the page's own kind, not the index's
    assert [b["source_file"] for b in page["backlinks"]] == \
        ["lore/entities/Kestra.md"]

    # the dangling link lands in the review queue as an advisory finding
    r = client.get("/api/campaign/testvale/review")
    assert r.status_code == 200
    link_items = [i for i in r.json()["items"] if i.get("source") == "links"]
    assert any("Ghost of the Vane" in i["body"] for i in link_items)


def test_search_finds_entity_pages(client, spine_root):
    hits = client.get("/api/campaign/testvale/search",
                      params={"q": "toll-keeper"}).json()["results"]
    assert any(h["file"] == "lore/entities/Alweric Tealeaf.md"
               and h["kind"] == "entities" for h in hits)


# ------------------------------------------------------------ doctor check


def test_doctor_vault_check(tmp_spine_root, monkeypatch):
    monkeypatch.setattr(doctor, "PARENT", tmp_spine_root.parent)
    ok = doctor.check_vault()
    assert ok["ok"] and not ok["required"]  # fine, and advisory

    _write(tmp_spine_root / "testvale" / "lore" / "entities" / "Kestra.md",
           "---\nname: Kestra\n---\n\n[[Nowhere At All]]\n")
    bad = doctor.check_vault()
    assert not bad["ok"]
    assert "dangling" in bad["detail"]


# ----------------------------------------------- stage 3: maps join the graph


def test_ensure_site_slugs_renames_legacy_basenames(client, tmp_spine_root):
    site = tmp_spine_root / "testvale" / "maps" / "sites" / "old-camp"
    for ext in ("json", "svg", "png"):
        _write(site / f"scene.{ext}", "x")
    _write(site / "old-camp.uvtt", "x")
    _write(site / "params.yaml", "name: Old Camp\n")

    assert client.get("/api/campaign/testvale/maps").status_code == 200
    for ext in ("json", "svg", "png"):
        assert (site / f"old-camp.{ext}").is_file(), ext
        assert not (site / f"scene.{ext}").exists(), ext
    # idempotent: a second listing finds nothing to rename, no error
    assert client.get("/api/campaign/testvale/maps").status_code == 200


def test_city_job_companion_note_and_place_stub(client, tmp_spine_root,
                                                monkeypatch):
    _fake_gen_city(tmp_spine_root, monkeypatch)
    r = client.post("/api/campaign/testvale/maps/cities",
                    json={"burg": "Dunmore"})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]

    note = (tmp_spine_root / "testvale" / "maps" / "cities" / "dunmore" /
            "dunmore.md").read_text(encoding="utf-8")
    assert "![[dunmore.svg]]" in note          # embed, vault-unique basename
    assert "'[[Dunmore]]'" in note             # place defaults to the burg
    # the burg's page was stubbed so the link resolves
    stub = tmp_spine_root / "testvale" / "lore" / "entities" / "Dunmore.md"
    assert stub.is_file()
    assert "Stub page" in stub.read_text(encoding="utf-8")
    assert any(e["name"] == "Dunmore"
               for e in spine.load_entities("testvale"))


def test_city_job_place_override_attaches_without_stubbing(
        client, tmp_spine_root, monkeypatch):
    _fake_gen_city(tmp_spine_root, monkeypatch)
    r = client.post("/api/campaign/testvale/maps/cities",
                    json={"burg": "Dunmore", "place": "The Vane"})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    note = (tmp_spine_root / "testvale" / "maps" / "cities" / "dunmore" /
            "dunmore.md").read_text(encoding="utf-8")
    assert "'[[The Vane]]'" in note  # attached to the existing page...
    assert not (tmp_spine_root / "testvale" / "lore" / "entities" /
                "Dunmore.md").exists()  # ...so no Dunmore stub


def test_place_page_backlinks_include_its_maps(client, tmp_spine_root,
                                               monkeypatch):
    """The everything-about-this-place join: a page's backlinks list the
    city map attached to it (source_kind 'map')."""
    _fake_gen_city(tmp_spine_root, monkeypatch)
    r = client.post("/api/campaign/testvale/maps/cities",
                    json={"burg": "Dunmore", "place": "The Vane"})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]

    page = client.get(
        "/api/campaign/testvale/lore/pages/The Vane").json()
    map_links = [b for b in page["backlinks"] if b["source_kind"] == "map"]
    assert [b["source_file"] for b in map_links] == \
        ["maps/cities/dunmore/dunmore.md"]


def test_site_job_place_link_dangles_and_reports(client, tmp_spine_root,
                                                 monkeypatch):
    """A site attached to a place that has no page: the link is written as
    asked, and the review queue reports it — findings, never verdicts."""
    site_name = "Marsh Camp"
    kind = "camp"

    def fake_run_py(engine_root, script, *args, timeout=300, on_line=None):
        rel = None
        for flag, value in zip(args, args[1:]):
            if flag == "--dir":
                rel = value
        if rel:
            d = engine_root / rel
            d.mkdir(parents=True, exist_ok=True)
            for name in ("scene.json", "scene.svg", "scene.png"):
                _write(d / name, "x")
            if args and args[0] == "wrap":  # the subcommand is the first arg
                _write(d / "marsh-camp.uvtt", "x")
        return 0, []

    def fake_run_node(engine_root, *args, timeout=300, on_line=None):
        return 0, []

    monkeypatch.setattr(engines, "run_py", fake_run_py)
    monkeypatch.setattr(engines, "run_node", fake_run_node)
    monkeypatch.setattr(engines, "DND_MAPPING", tmp_spine_root / "dnd-mapping")

    r = client.post("/api/campaign/testvale/maps/sites",
                    json={"kind": kind, "name": site_name,
                          "place": "the Docks"})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]

    note = (tmp_spine_root / "testvale" / "maps" / "sites" / "marsh-camp" /
            "marsh-camp.md").read_text(encoding="utf-8")
    assert "'[[the Docks]]'" in note  # written as asked, page not stubbed
    r = client.get("/api/campaign/testvale/review")
    link_items = [i for i in r.json()["items"] if i.get("source") == "links"]
    assert any("the Docks" in i["body"] for i in link_items)


# ================================================= stage 4: generation on graph


def _dangling_page(tmp_spine_root, body=""):
    _linker_page(tmp_spine_root, "Kestra",
                 "Sworn to [[the Ash Wardens]] since the crossing. " + body)


def _canned_pages_llm(monkeypatch, pages_yaml="", canon_json=""):
    import llm_client
    calls = []

    def fake(prompt, **kwargs):
        calls.append(prompt)
        if prompt.endswith("Return the JSON ONLY."):
            return canon_json
        return pages_yaml

    monkeypatch.setattr(llm_client, "complete", fake)
    return calls


PAGES_YAML = """```yaml
- name: the Ash Wardens
  kind: faction
  aliases: [Ash Wardens]
  body: Soldiery left unpaid after the crossing; they hold the ash tolls now.
  new_names: []
```"""


def test_missing_pages_targets_grouped(tmp_spine_root):
    _dangling_page(tmp_spine_root)
    _write(tmp_spine_root / "testvale" / "lore" / "entities" / "Pilgrim.md",
           "---\nname: Pilgrim\n---\n\nPrays to [[The Ash Wardens|the wardens]] daily.\n")
    targets = links.missing_page_targets("testvale")
    assert len(targets) == 1
    assert targets[0]["target"] == "the Ash Wardens"
    assert len(targets[0]["sources"]) == 2  # two pages link it


def test_missing_pages_job_stages_drafts(client, tmp_spine_root, monkeypatch):
    _dangling_page(tmp_spine_root)
    _canned_pages_llm(monkeypatch, PAGES_YAML)
    r = client.post("/api/campaign/testvale/lore/pages/missing", json={})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["kind"] == "lore-missing-pages"

    pending = (tmp_spine_root / "testvale" / "lore" /
               "pending-pages-1.yaml")
    assert pending.is_file()
    doc = yaml.safe_load(pending.read_text(encoding="utf-8"))
    entry = doc["entries"][0]
    assert entry["title"] == "the Ash Wardens"
    assert entry["page"]["kind"] == "faction"
    assert entry["page"]["aliases"] == ["Ash Wardens"]
    assert entry["id"] == "page-1"
    # the review queue lists it as a page
    items = client.get("/api/campaign/testvale/review").json()["items"]
    page_items = [i for i in items if i["kind"] == "lore-page"]
    assert any(i["title"] == "the Ash Wardens" for i in page_items)


def test_missing_pages_job_refuses_with_no_dangling(client, tmp_spine_root,
                                                    monkeypatch):
    import llm_client
    calls = []
    monkeypatch.setattr(llm_client, "complete",
                        lambda p, **k: calls.append(p) or "")
    r = client.post("/api/campaign/testvale/lore/pages/missing", json={})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "no dangling" in job["error"]
    assert calls == []  # refused before any model call


def test_missing_pages_job_drops_off_target_drafts(tmp_spine_root,
                                                   monkeypatch):
    import loregen
    _dangling_page(tmp_spine_root)
    _canned_pages_llm(monkeypatch, """```yaml
- name: Something Else
  kind: thing
  body: Not what was asked for.
```""")
    with pytest.raises(RuntimeError, match="every returned page draft"):
        loregen.generate_missing_pages(tmp_spine_root / "testvale")


def test_approve_page_draft_writes_the_page(client, tmp_spine_root):
    _write(tmp_spine_root / "testvale" / "lore" / "pending-pages-1.yaml",
           "created: now\nmode: missing-pages\n"
           "entries:\n"
           "  - id: page-1\n    title: the Ash Wardens\n"
           "    text: Soldiery left unpaid after the crossing.\n"
           "    source: authored\n    page:\n      kind: faction\n"
           "      aliases: [Ash Wardens]\n")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-pages-1.yaml",
                          "ids": ["page-1"]})
    assert r.status_code == 200
    body = r.json()
    assert body["pages"] == 1 and body["approved"] == 1
    page = (tmp_spine_root / "testvale" / "lore" / "entities" /
            "the Ash Wardens.md")
    assert page.is_file()
    text = page.read_text(encoding="utf-8")
    assert "kind: faction" in text and "aliases:" in text
    assert "Soldiery left unpaid" in text
    assert not (tmp_spine_root / "testvale" / "lore" /
                "pending-pages-1.yaml").exists()  # empty pending is deleted
    # the new page resolves — the dangling link is gone
    assert links.missing_page_targets("testvale") == []


def test_approve_entry_stubs_its_new_names(client, tmp_spine_root):
    _write(tmp_spine_root / "testvale" / "lore" / "pending-entries-9.yaml",
           "created: now\nentries:\n"
           "  - id: t0099\n    when: before the toll\n    title: Kestra's Ferry\n"
           "    text: Kestra ferried cattle for the wardens.\n"
           "    source: authored\n    new_names: [Kestra]\n")
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-entries-9.yaml",
                          "ids": ["t0099"]})
    assert r.status_code == 200
    assert r.json()["stubs"] == 1
    stub = (tmp_spine_root / "testvale" / "lore" / "entities" / "Kestra.md")
    assert stub.is_file()
    text = stub.read_text(encoding="utf-8")
    assert "kind: unknown" in text
    assert "Kestra's Ferry" in text  # the stub points back at its entry


def test_linkify_markdown_basics(tmp_spine_root):
    text = ("# Week 4 — the crossing\n\n"
            "Alweric Tealeaf counted, and the wardens watched. "
            "Alweric Tealeaf counted every cart twice.\n\n"
            "| faction | action |\n|---|---|\n| wardens | salvage |\n")
    out = links.linkify_markdown(text, spine.load_entities("testvale"))
    # first occurrence linked (once per file), display preserved
    assert out.count("[[Alweric Tealeaf]]") == 1
    assert "and the [[wardens]] watched. " \
           "Alweric Tealeaf counted every cart twice." in out
    # alias resolves ("The Bridge Wardens" carries "wardens"), but the
    # TABLE row is skipped
    assert "[[wardens]] watched" in out
    assert out.count("[[wardens]]") == 1
    # idempotent: a second pass changes nothing
    assert links.linkify_markdown(out, spine.load_entities("testvale")) == out


def test_week_chronicle_gets_linkified(client, tmp_spine_root, monkeypatch):
    """The week job linkifies its new chronicles on copy-out (campaign copy
    only; the engine home is scratch)."""
    import engines as eng

    state = ("meta:\n  clock:\n    week: 4\n"
             "factions:\n  - id: wardens\n    name: The Bridge Wardens\n"
             "events: []\nrumors: []\n")
    engine_home = tmp_spine_root / "agentic-simulator"
    (engine_home / "world").mkdir(parents=True, exist_ok=True)
    (engine_home / "chronicle").mkdir(parents=True, exist_ok=True)
    (engine_home / "world" / "state.yaml").write_text(state, encoding="utf-8")

    def fake_run_py(engine_root, script, *args, timeout=None, on_line=None,
                    **kw):
        # tick writes the NEW chronicle; it must not exist before the run
        # or the week job's before/after diff will not see it
        (engine_root / "chronicle" / "0004-the-toll.md").write_text(
            "# Week 4 — The Toll\n\nThe wardens counted the carts again.\n",
            encoding="utf-8")
        return 0, []

    monkeypatch.setattr(eng, "run_py", fake_run_py)
    monkeypatch.setattr(eng, "AGENTIC_SIMULATOR", engine_home)
    # the week job needs a simulation to copy in
    src = tmp_spine_root / "testvale" / "simulation" / "state.yaml"
    assert src.is_file()  # the fixture ships one

    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]

    copied = (tmp_spine_root / "testvale" / "simulation" / "chronicle" /
              "0004-the-toll.md")
    text = copied.read_text(encoding="utf-8")
    assert "[[wardens]]" in text  # linkified on the campaign copy
    # the engine home keeps its raw copy (scratch is never rewritten)
    raw = (engine_home / "chronicle" / "0004-the-toll.md").read_text(
        encoding="utf-8")
    assert "[[wardens]]" not in raw
  
