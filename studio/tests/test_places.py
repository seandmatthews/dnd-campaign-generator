"""Place-centered campaign view tests (docs/plans/place-centered-campaign-
view.md acceptance list, the deterministic slice).

Covers: lore-only places read with no simulation; legacy-city campaigns
read without writing; suggested joins never merge on name alone; the Link
action writes one page and preserves body + unknown frontmatter; renames
keep ids; two same-named places stay distinct; duplicate place ids are a
conflict (422), never first-match-wins; events join ONLY on explicit
place_ids; private facts stay visibly private; pending proposals and
forecasts stay separate from canon counts; cyclic parents and unknown
references are findings; association mutations are revision-checked and
campaign-contained."""

import copy
import json

import forecasts as forecast_store
import places
import proposals as proposal_store
import pytest
import spine
import yaml

NL = chr(10)
NL2 = chr(10) + chr(10)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _page(root, name, front, body="A place of no consequence."):
    meta = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
    return _write(root / "lore" / "entities" / f"{name}.md",
                  f"---\n{meta}---\n\n{body}\n")


def _make_campaign(root, name, *, state=None):
    d = root / name
    for sub in ("lore/entities", "simulation/chronicle", "maps/cities",
                "maps/sites", "world"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    _write(d / "campaign.yaml",
           yaml.safe_dump({"name": name, "title": name}, sort_keys=False))
    if state is not None:
        _write(d / "simulation" / "state.yaml",
               yaml.safe_dump(state, sort_keys=False,
                              allow_unicode=True, width=100))
    return d


def _sim_state(clock=3, places=None, factions=None, events=None):
    return {
        "meta": {"clock": {"week": clock, "season": "spring"}},
        "places": places or [],
        "factions": factions or [],
        "events": events or [],
        "rumors": [],
    }


@pytest.fixture()
def camp(tmp_spine_root):
    """A writable campaign with a two-place sandbox: the city Ruravaqan and
    the settlement Ferry Crossing."""
    name = "placevale"
    state = _sim_state(
        clock=10,
        places=[
            {"id": "ruravaqan", "name": "Ruravaqan", "kind": "city",
             "population": 8000},
            {"id": "ferry-crossing", "name": "Ferry Crossing",
             "population": 300},
        ],
        factions=[
            {"id": "wardens", "name": "The Bridge Wardens",
             "place": "ruravaqan"},
            {"id": "ferrymen", "name": "The Ferrymen",
             "place": "ferry-crossing"},
        ],
        events=[
            {"week": 9, "actor": "ferrymen",
             "text": "The concession was struck at the crossing.",
             "place_ids": ["ferry-crossing"]},
            {"week": 9, "actor": "wardens",
             "text": "A kingdom-wide census was decreed."},  # unlocated
            {"week": 1, "actor": "wardens",
             "text": "The old crossing burned.",
             "place_ids": ["ferry-crossing"]},
        ],
    )
    _make_campaign(tmp_spine_root, name, state=state)
    return name


# --------------------------------------------------------------- lore-only


def test_lore_only_place_reads_without_simulation(tmp_spine_root):
    """A kind: place page with no simulation, geography, or maps still gets
    a useful page; mentions come from wiki-links, not the place registry."""
    _make_campaign(tmp_spine_root, "lore-only")
    _page(tmp_spine_root / "lore-only", "Ferry Crossing",
          {"kind": "place", "name": "Ferry Crossing", "aliases": ["Old Ferry"]})
    _page(tmp_spine_root / "lore-only", "The Ferrymaster",
          {"kind": "person", "name": "The Ferrymaster"},
          body="He keeps the crossing, and [[Ferry Crossing]] remembers him.")

    doc = places.list_places("lore-only")
    assert len(doc["places"]) == 1
    p = doc["places"][0]
    assert p["id"].startswith("page:") and p["provisional"]

    view = places.place_view("lore-only", p["id"])
    assert view["place"]["name"] == "Ferry Crossing"
    assert view["place"]["aliases"] == ["Old Ferry"]
    assert view["events"] == [] and view["factions"] == []
    assert len(view["mentions"]) == 1
    assert view["mentions"][0]["source"]["file"] == \
        "lore/entities/The Ferrymaster.md"
    assert "Ferry Crossing" in view["mentions"][0]["excerpt"]


def test_unknown_place_id_is_404_and_malformed_names_422(client, camp):
    r = client.get(f"/api/campaign/{camp}/places/no-such-place")
    assert r.status_code == 404
    assert "error" in r.json()
    # a hostile URL segment must be a bounded 422, never a 500
    r = client.post(f"/api/campaign/{camp}/places/ferry-crossing/link",
                    json={"page": ".."})
    assert r.status_code == 422, r.text


# ------------------------------------------------------- legacy read-only


def test_legacy_city_reads_without_writing_any_file(spine_root):
    """Opening a simulated place in a legacy campaign must not write a byte
    (the fixture campaign is read from the read-only root on purpose)."""
    name = "testvale"
    d = spine.campaign_dir(name)

    def tree_snapshot():
        return {str(p.relative_to(d)): p.read_bytes()
                for p in sorted(d.rglob("*")) if p.is_file()
                and ".git" not in p.parts}

    before = tree_snapshot()
    doc = places.list_places(name)
    assert any(g["sim_id"] == "provis" for g in doc["places"])
    view = places.place_view(name, "provis")
    assert view["place"]["name"] == "Provis"
    assert {f["id"]: f["relationship"] for f in view["factions"]} == \
        {"wardens": "home", "ferrymen": "home"}
    assert all(not f["explicit"] for f in view["factions"])
    assert tree_snapshot() == before


# ------------------------------------------------------------- link/rename


def test_suggested_join_then_link_adopts_the_sim_id(camp, tmp_spine_root):
    root = tmp_spine_root
    _page(root / camp, "Ferry Crossing",
          {"kind": "place", "name": "Ferry Crossing", "custom_note": "keep me"},
          body="The tollhouse stands beside the north landing.")

    doc = places.list_places(camp)
    suggested = [s for s in doc["suggested"] if s["sim_id"] == "ferry-crossing"]
    assert suggested and suggested[0]["lore_file"] == \
        "lore/entities/Ferry Crossing.md"

    r = client_post_link(camp, "ferry-crossing")
    assert r["linked"] == "ferry-crossing" and not r["minted"]

    text = (root / camp / "lore" / "entities" / "Ferry Crossing.md") \
        .read_text(encoding="utf-8")
    assert "place_id: ferry-crossing" in text
    assert "custom_note: keep me" in text          # unknown key preserved
    assert "tollhouse stands beside" in text       # body preserved

    doc = places.list_places(camp)
    group = next(g for g in doc["places"] if g["id"] == "ferry-crossing")
    assert group["explicit"] and group["association"] == "explicit"
    assert doc["suggested"] == []

    # the page write is the ONLY write: state.yaml untouched
    state_text = (root / camp / "simulation" / "state.yaml") \
        .read_text(encoding="utf-8")
    assert "place_id:" not in state_text


def client_post_link(camp, place_id):
    # direct service call (the HTTP route wraps this with the same lock and
    # publish path; route-level behavior is covered by the association test)
    return places.link(camp, place_id)


def test_rename_keeps_id_and_folds_old_name_into_aliases(camp, tmp_spine_root):
    root = tmp_spine_root
    _page(root / camp, "Ferry Crossing",
          {"kind": "place", "name": "Ferry Crossing"})
    places.link(camp, "ferry-crossing")
    result = places.patch(camp, "ferry-crossing", name_="The Ferry Crossing")
    assert "name" in result["patched"]

    view = places.place_view(camp, "ferry-crossing")
    assert view["place"]["name"] == "The Ferry Crossing"
    assert "Ferry Crossing" in view["place"]["aliases"]  # old name resolves
    assert view["place"]["lore"]["file"] == "lore/entities/Ferry Crossing.md"


def test_rename_of_an_unlinked_lore_only_record_mints_on_link(
        camp, tmp_spine_root):
    """A lore-only provisional record has no durable identity; the Link
    action MINTS one (place-<uuid>) rather than writing 'page:<stem>'."""
    root = tmp_spine_root
    _page(root / camp, "Old Quarry", {"kind": "place", "name": "Old Quarry"})
    doc = places.list_places(camp)
    group = next(g for g in doc["places"] if g["id"] == "page:Old Quarry")
    assert group["provisional"]
    out = places.link(camp, "page:Old Quarry")
    assert out["minted"] and out["linked"].startswith("place-")
    text = (root / camp / "lore" / "entities" / "Old Quarry.md") \
        .read_text(encoding="utf-8")
    assert f"place_id: {out['linked']}" in text


# ---------------------------------------------------------- name conflicts


def test_two_same_named_places_stay_distinct(tmp_spine_root):
    state = _sim_state(
        places=[{"id": "new-town", "name": "New Town"},
                {"id": "harbor-new-town", "name": "New Town"}],
        factions=[{"id": "a", "name": "A", "place": "new-town"},
                  {"id": "b", "name": "B", "place": "harbor-new-town"}])
    _make_campaign(tmp_spine_root, "twin-towns", state=state)
    doc = places.list_places("twin-towns")
    ids = {g["id"] for g in doc["places"]}
    assert {"new-town", "harbor-new-town"} <= ids
    assert any(f["rule"] == "places/duplicate-name" for f in doc["findings"])
    # no suggested join and no merge: each faction's home is its own group
    assert doc["suggested"] == []
    view = places.place_view("twin-towns", "new-town")
    assert [f["id"] for f in view["factions"]] == ["a"]


def test_duplicate_place_id_is_a_conflict_not_first_match(tmp_spine_root):
    _make_campaign(tmp_spine_root, "dup-id")
    _page(tmp_spine_root / "dup-id", "Ferry Crossing",
          {"kind": "place", "name": "Ferry Crossing", "place_id": "ferry"})
    _page(tmp_spine_root / "dup-id", "Ferry Crossing East",
          {"kind": "place", "name": "Ferry Crossing East", "place_id": "ferry"})
    doc = places.list_places("dup-id")
    assert doc["conflicts"] and doc["conflicts"][0]["id"] == "ferry"
    with pytest.raises(places.PlaceError) as e:
        places.place_view("dup-id", "ferry")
    assert "2 place pages" in str(e.value)


# ------------------------------------------------------------------ events


def test_events_join_on_explicit_ids_only_and_survive_faction_moves(
        camp, tmp_spine_root):
    """An event's location is stored with the event: moving the faction's
    home must not drag history along, and unlocated events stay unlocated."""
    view = places.place_view(camp, "ferry-crossing")
    assert [e["text"] for e in view["events"]] == \
        ["The concession was struck at the crossing."]

    state = spine.load_state(camp)
    state["factions"][1]["place"] = "ruravaqan"  # the Ferrymen move away
    spine.write_state(camp, state)
    view2 = places.place_view(camp, "ferry-crossing")
    # the rewrite bumps the state revision, so compare the located content,
    # not the whole locator: history did not move with the faction
    assert [e["text"] for e in view2["events"]] == \
        [e["text"] for e in view["events"]]
    assert [e["week"] for e in view2["events"]] == [9]


def test_recent_window_and_all_history(camp):
    recent = places.place_view(camp, "ferry-crossing")   # clock 10, window 8
    assert all(e["week"] > 2 for e in recent["events"])
    everything = places.place_view(camp, "ferry-crossing", window="all")
    assert len(everything["events"]) == 2
    assert everything["counts"]["events"]["total"] == 2
    # old events carry a read-only locator, not a fake stable id
    old = next(e for e in everything["events"] if e["week"] == 1)
    assert old["locator"]["index"] == 2
    assert old["locator"]["state_revision"]


def test_event_pagination_counts(camp):
    view = places.place_view(camp, "ferry-crossing", window="all",
                             event_limit=1)
    assert view["counts"]["events"] == {"total": 2, "returned": 1, "more": 1}
    page2 = places.place_view(camp, "ferry-crossing", window="all",
                              event_limit=1, event_offset=1)
    assert page2["counts"]["events"]["more"] == 0


# ------------------------------------------------------------------ facts


def test_private_facts_stay_visibly_private_and_disclosure_is_explicit(
        camp, tmp_spine_root):
    root = tmp_spine_root
    _write(root / camp / "simulation" / "knowledge.yaml", yaml.safe_dump({
        "schema_version": 1,
        "facts": [
            {"id": "fact-1", "text": "The ferrymaster skims the toll.",
             "visibility": "dm_only", "status": "current",
             "place_ids": ["ferry-crossing"]},
            {"id": "fact-2", "text": "The crossing is safe by day.",
             "visibility": "public", "status": "current",
             "place_ids": ["ferry-crossing"]},
            {"id": "fact-3", "text": "Unrelated.",
             "visibility": "dm_only", "status": "current"},
        ]}, sort_keys=False))
    # fact-2 is public in-world but NOT yet party-known
    _write(root / camp / "simulation" / "party-knowledge.yaml", yaml.safe_dump(
        {"schema_version": 1, "revealed": []}, sort_keys=False))

    view = places.place_view(camp, "ferry-crossing")
    by_id = {f["id"]: f for f in view["knowledge"]}
    assert set(by_id) == {"fact-1", "fact-2"}
    assert by_id["fact-1"]["visibility"] == "dm_only"
    assert by_id["fact-2"]["disclosed"] is False
    assert view["counts"]["events"]["total"] == 1  # facts never inflate counts


# ----------------------------------------------------- pending + forecasts


def test_pending_proposal_stays_out_of_canon_counts(camp, tmp_spine_root):
    base = spine.load_state(camp)
    after = copy.deepcopy(base)
    after["events"].append({
        "week": 10, "actor": "ferrymen", "text": "PROPOSED pier fire",
        "place_ids": ["ferry-crossing"]})
    with spine.campaign_lock(camp):
        proposal_store.create(
            camp, kind="session_effects", base_week=10, proposed_week=10,
            base_revisions={}, input_set_revisions={},
            artifacts={"state": yaml.safe_dump(after, sort_keys=False,
                                               allow_unicode=True, width=100),
                       "chronicles": {}, "entries": []},
            run_record={"kind": "session", "source": "session:test"},
            before_state=base)
    view = places.place_view(camp, "ferry-crossing")
    assert len(view["pending"]) == 1
    pend = view["pending"][0]
    assert pend["canonical"] is False and pend["hypothetical"] is False
    assert pend["proposal_status"] == "pending"
    assert all(e["text"] != "PROPOSED pier fire" for e in view["events"])
    assert view["counts"]["events"]["total"] == 1


def test_forecasts_are_hypothetical_never_canon(camp):
    with spine.campaign_lock(camp):
        forecast_store.create(
            camp, base_week=10,
            weeks=[{"week": 11, "title": " projection", "markdown": "md",
                    "diff": {"events_added": [
                        {"week": 11, "actor": "ferrymen",
                         "text": "HYPOTHETICAL storm damage",
                         "place_ids": ["ferry-crossing"]}]}}],
            run_record={"kind": "forecast"})
    view = places.place_view(camp, "ferry-crossing")
    assert len(view["forecasts"]) == 1
    assert view["forecasts"][0]["hypothetical"] is True
    assert view["forecasts"][0]["canonical"] is False
    assert all("HYPOTHETICAL" not in e["text"] for e in view["events"])


# ---------------------------------------------------------------- findings


def test_cyclic_parents_are_findings_not_crashes(tmp_spine_root):
    _make_campaign(tmp_spine_root, "cycle")
    _page(tmp_spine_root / "cycle", "A", {"kind": "place", "name": "A",
                                          "place_id": "a",
                                          "parent_place_id": "b"})
    _page(tmp_spine_root / "cycle", "B", {"kind": "place", "name": "B",
                                          "place_id": "b",
                                          "parent_place_id": "a"})
    doc = places.list_places("cycle")
    assert any(f["rule"] == "places/parent-cycle" for f in doc["findings"])
    view = places.place_view("cycle", "a")  # still renders
    assert view["place"]["id"] == "a"


def test_unknown_place_references_become_findings(camp, tmp_spine_root):
    root = tmp_spine_root
    _page(root / camp, "Ghost Docks",
          {"kind": "place", "name": "Ghost Docks", "place_id": "ghost-docks"})
    # a fact pointing at a made-up id is a finding, not a drop
    _write(root / camp / "simulation" / "knowledge.yaml", yaml.safe_dump({
        "schema_version": 1, "facts": [
            {"id": "fact-9", "text": "Smugglers land here.",
             "visibility": "dm_only", "place_ids": ["not-a-place"]}]},
        sort_keys=False))
    doc = places.list_places(camp)
    rules = {f["rule"] for f in doc["findings"]}
    assert "places/unknown-reference" in rules
    assert any("not-a-place" in f["message"] for f in doc["findings"])
    # and the page still renders with the fact attached to its real place
    view = places.place_view(camp, "ferry-crossing")
    assert view["knowledge"] == []


# ------------------------------------------------------------ associations


def test_association_routes_entity_event_revision_checks(client, camp):
    # residents: entity frontmatter
    _page(spine.campaign_dir(camp), "Ferrymaster",
          {"kind": "person", "name": "Ferrymaster"})
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "entity", "source_id": "Ferrymaster",
              "relationship": "resides_at"})
    assert r.status_code == 200, r.text
    page_text = (spine.campaign_dir(camp) / "lore" / "entities" /
                 "Ferrymaster.md").read_text(encoding="utf-8")
    assert "resides_at:" in page_text and "ferry-crossing" in page_text
    view = places.place_view(camp, "ferry-crossing")
    assert [(e["name"], e["relationship"]) for e in view["entities"]] == \
        [("Ferrymaster", "resides_at")]

    # events: located via the view's read-only locator, revision-checked
    view = places.place_view(camp, "ferry-crossing")
    ev = view["events"][0]
    stale_rev = "0" * 64
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "event",
              "source_id": str(ev["locator"]["index"]),
              "relationship": "occurred_at", "add": False,
              "expected_revision": stale_rev})
    assert r.status_code == 409  # stale revision refused, nothing written

    current_rev = ev["locator"]["state_revision"]
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "event",
              "source_id": str(ev["locator"]["index"]),
              "relationship": "occurred_at", "add": False,
              "expected_revision": current_rev})
    assert r.status_code == 200, r.text
    state = spine.load_state(camp)
    located = [e for e in state["events"] if e.get("place_ids")]
    assert not any(e["text"].startswith("The concession") for e in located)


def test_association_refuses_unknown_sources_and_bad_relationships(
        client, camp):
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": " faction", "source_id": "wardens",
              "relationship": "interest"})
    assert r.status_code == 422  # unknown source kind (leading space)
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "fact", "source_id": "fact-404",
              "relationship": "about"})
    assert r.status_code == 404
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "entity", "source_id": "Someone",
              "relationship": "occurred_at"})
    assert r.status_code == 422  # wrong relationship for the source kind


def test_patch_refuses_parent_cycles_and_non_map_preferred(
        client, camp, tmp_spine_root):
    root = tmp_spine_root
    _page(root / camp, "Ferry Crossing",
          {"kind": "place", "name": "Ferry Crossing", "place_id":
           "ferry-crossing"})
    _page(root / camp, "Ruravaqan",
          {"kind": "place", "name": "Ruravaqan", "place_id": "ruravaqan",
           "parent_place_id": "ferry-crossing"})

    # children show up as navigation first
    view = places.place_view(camp, "ferry-crossing")
    assert [c["id"] for c in view["children"]] == ["ruravaqan"]

    # ferry-crossing <- ruravaqan <- ferry-crossing would be a cycle
    r = client.patch(f"/api/campaign/{camp}/places/ferry-crossing",
                     json={"parent_place_id": "ruravaqan"})
    assert r.status_code == 422 and "cycle" in r.json()["error"]
    r = client.patch(f"/api/campaign/{camp}/places/ferry-crossing",
                     json={"preferred_map": "campaign.yaml"})
    assert r.status_code == 422  # must be an existing file under maps/

    # clearing the parent is legal; a real map artifact is accepted
    r = client.patch(f"/api/campaign/{camp}/places/ruravaqan",
                     json={"parent_place_id": ""})
    assert r.status_code == 200
    map_dir = root / camp / "maps" / "cities" / "ferry"
    map_dir.mkdir(parents=True)
    _write(map_dir / "ferry.md",
               yaml.safe_dump({"kind": "city map"}) + NL2 + "map" + NL)
    r = client.patch(f"/api/campaign/{camp}/places/ferry-crossing",
                     json={"preferred_map": "maps/cities/ferry/ferry.md"})
    assert r.status_code == 200, r.text
    view = places.place_view(camp, "ferry-crossing")
    assert view["children"] == []
    assert view["maps"] == []  # preference is not an attachment


# ---------------------------------------------------------------- maps


def test_map_attachments_join_by_note_and_by_id(camp, tmp_spine_root):
    root = tmp_spine_root
    _page(root / camp, "Ferry Crossing",
          {"kind": "place", "name": "Ferry Crossing"})
    slug_dir = root / camp / "maps" / "sites" / "ferry-inn"
    slug_dir.mkdir(parents=True)
    _write(slug_dir / "ferry-inn.md",
           "---\nkind: battlemap\nplace: \"[[Ferry Crossing]]\"\n---\n\n"
           "![[ferry-inn.png]]\n")
    view = places.place_view(camp, "ferry-crossing")
    assert [m["slug"] for m in view["maps"]] == ["ferry-inn"]
    assert view["maps"][0]["scale"] == "battlemap"


# ------------------------------------------------- map association


def test_map_association_writes_note_and_params_in_one_publish(
        client, camp, tmp_spine_root):
    """source_kind=map is the module's only multi-file publish: the
    companion note AND params.yaml carry place_id together; the generated
    artifacts are untouched."""
    root = tmp_spine_root
    slug_dir = root / camp / "maps" / "sites" / "old-mill"
    slug_dir.mkdir(parents=True)
    (slug_dir / "old-mill.png").write_bytes(b"png")
    note = _write(slug_dir / "old-mill.md",
                  yaml.safe_dump({"kind": "battlemap"}) + NL + NL +
                  "map" + NL)
    _write(slug_dir / "params.yaml", yaml.safe_dump(
        {"name": "The Old Mill", "kind": "mill", "seed": "1"}))

    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "map", "source_id": "old-mill",
              "relationship": "depicts"})
    assert r.status_code == 200, r.text
    note_meta = yaml.safe_load(
        note.read_text(encoding="utf-8").split("---")[1])
    assert note_meta["place_id"] == "ferry-crossing"
    params = yaml.safe_load(
        (slug_dir / "params.yaml").read_text(encoding="utf-8"))
    assert params["place_id"] == "ferry-crossing"
    assert (slug_dir / "old-mill.png").read_bytes() == b"png"

    # the explicit-id join: the registry groups the attachment by id
    view = places.place_view(camp, "ferry-crossing")
    assert [(m["slug"], m["place_id"]) for m in view["maps"]] ==         [("old-mill", "ferry-crossing")]

    # remove clears both files
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "map", "source_id": "old-mill",
              "relationship": "depicts", "add": False})
    assert r.status_code == 200, r.text
    params = yaml.safe_load(
        (slug_dir / "params.yaml").read_text(encoding="utf-8"))
    assert "place_id" not in params


def test_page_scoped_groups_refuse_associations_and_parenting(
        client, camp, tmp_spine_root):
    """A provisional page:<stem> id is not a durable reference: writes
    against it are refused with the 'link it first' action instead of
    minting references that break on rename."""
    root = tmp_spine_root
    _page(root / camp, "Old Quarry", {"kind": "place", "name": "Old Quarry"})
    _page(root / camp, "Ferrymaster", {"kind": "person", "name": "Ferrymaster"})
    r = client.post(
        f"/api/campaign/{camp}/places/page:Old Quarry/associations",
        json={"source_kind": "fact", "source_id": "fact-1",
              "relationship": "about"})
    assert r.status_code == 422 and "link it first" in r.json()["error"]
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "entity", "source_id": "Ferrymaster",
              "relationship": "resides_at", "add": True})
    assert r.status_code == 200  # explicit groups still associate


def test_event_association_requires_the_locator_revision(client, camp):
    r = client.post(
        f"/api/campaign/{camp}/places/ferry-crossing/associations",
        json={"source_kind": "event", "source_id": "0",
              "relationship": "occurred_at"})
    assert r.status_code == 422 and "expected_revision" in r.json()["error"]


def test_supersede_carries_forward_place_ids_and_subjects(camp,
                                                          tmp_spine_root):
    """A knowledge-tab supersede (which sends only text/visibility) must
    not strip the fact's place associations or subjects."""
    root = tmp_spine_root
    _write(root / camp / "simulation" / "knowledge.yaml", yaml.safe_dump({
        "schema_version": 1, "facts": [
            {"id": "fact-1", "text": "Old wording.",
             "visibility": "dm_only", "status": "current",
             "place_ids": ["ferry-crossing"],
             "subjects": ["The Ferrymaster"]}]}, sort_keys=False))
    import knowledge
    with spine.campaign_lock(camp):
        new, pub = knowledge.supersede_fact_tx(
            camp, "fact-1", text="Corrected wording.",
            visibility="dm_only")
    assert new["place_ids"] == ["ferry-crossing"]
    assert new["subjects"] == ["The Ferrymaster"]
    assert pub["state"] == "published"


# ------------------------------------------------------------------ CLI


def test_cli_show_json_and_list(camp, capsys):
    rc = places._cli(["show", camp, "ferry-crossing", "--json"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["place"]["id"] == "ferry-crossing"
    rc = places._cli(["list", camp])
    out = capsys.readouterr().out
    assert "ferry-crossing" in out and "ruravaqan" in out


def test_cli_markdown_labels_dm_material_and_hypothetical(camp):
    with spine.campaign_lock(camp):
        forecast_store.create(
            camp, base_week=10,
            weeks=[{"week": 11, "title": " p", "markdown": "md",
                    "diff": {"events_added": [
                        {"week": 11, "actor": "ferrymen",
                         "text": "HYPOTHETICAL",
                         "place_ids": ["ferry-crossing"]}]}}],
            run_record={"kind": "forecast"})
    view = places.place_view(camp, "ferry-crossing")
    md = places.render_markdown(camp, view)
    assert "DM material" in md and "not a player handout" in md
    assert "HYPOTHETICAL" in md


# ------------------------------------------------- route-level hardening


def test_place_routes_require_campaign(client):
    assert client.get("/api/campaign/nope/places").status_code == 404
