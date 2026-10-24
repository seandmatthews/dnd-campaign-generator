"""Phase 6: DM briefing, player disclosure, and bounded review workload.

All deterministic — no LLM pass anywhere. The briefing uses the requested
range, caps its display with an honest count, and never presents pending
proposals as canon. The player handout contains ONLY deliberately revealed
facts: an empty disclosure set is an empty handout with an explanation, and
a secret that is DM-visible must be absent from the export entirely.
"""

import briefing


def _add_fact(client, **body):
    r = client.post("/api/campaign/testvale/knowledge", json=body)
    assert r.status_code == 200, r.text
    return r.json()["fact"]


def test_briefing_range_and_cap(client, tmp_spine_root):
    # seed events in weeks 1..9 by hand (deterministic, no engine)
    path = tmp_spine_root / "testvale" / "simulation" / "state.yaml"
    state = yaml_load(path)
    state["events"] = [{"week": w, "actor": "wardens",
                        "text": f"wardens week-{w} deed"}
                       for w in range(1, 10)]
    write_yaml(path, state)

    doc = briefing.build("testvale", since_week=3)
    assert doc["total"] == 6  # weeks 4..9 only — the range is honored
    assert doc["shown"] == 6 and doc["omitted"] == 0  # under the cap: all shown
    assert all(i["week"] > 3 for i in doc["items"] if i["kind"] == "event")
    assert "more not shown" not in doc["markdown"], "nothing omitted, no note"


def test_briefing_cap_truncates_honestly(client, tmp_spine_root):
    """The name of the game: over BRIEFING_CAP items -> exactly the first
    ten shown, an honest omitted count, and the markdown says so."""
    path = tmp_spine_root / "testvale" / "simulation" / "state.yaml"
    state = yaml_load(path)
    total = briefing.BRIEFING_CAP + 5
    state["events"] = [{"week": w, "actor": "wardens",
                        "text": f"wardens week-{w} deed"}
                       for w in range(1, total + 1)]
    write_yaml(path, state)

    doc = briefing.build("testvale", since_week=0)
    # the fixture may carry other item kinds (pending reviews) — judge the
    # seeded events by kind
    event_items = [i for i in doc["items"] if i["kind"] == "event"]
    assert doc["total"] >= total, "every seeded event counts"
    assert len(event_items) == briefing.BRIEFING_CAP
    assert doc["shown"] == briefing.BRIEFING_CAP
    assert doc["omitted"] == doc["total"] - briefing.BRIEFING_CAP
    # newest weeks win the slots (sorted week-desc)
    assert [i["week"] for i in event_items] == list(
        range(total, total - briefing.BRIEFING_CAP, -1))
    assert f"{doc['omitted']} more not shown" in doc["markdown"], \
        "never a silent truncation of history"


def test_briefing_includes_rumors_stale_facts_and_pending_reviews(
        client, tmp_spine_root):
    """Three of the five item kinds were previously unasserted — a refactor
    could silently drop them."""
    # a source file, approved as a fact's source, then changed -> stale
    style = tmp_spine_root / "testvale" / "lore" / "style" / "voice.md"
    style.parent.mkdir(parents=True, exist_ok=True)
    style.write_text("original voice", encoding="utf-8")
    _add_fact(client, text="The bell tolls at dusk.", visibility="public",
              source_file="lore/style/voice.md")
    style.write_text("the voice changed under us", encoding="utf-8")

    # a pending review file (any file in the pending globs counts)
    (tmp_spine_root / "testvale" / "lore" / "pending-batch.yaml").write_text(
        "entries: []\n", encoding="utf-8")

    path = tmp_spine_root / "testvale" / "simulation" / "state.yaml"
    state = yaml_load(path)
    state["events"] = [{"week": 2, "actor": "wardens", "text": "wardens deed"}]
    state["rumors"] = [{"claim": "the ferry is haunted", "fidelity": "high",
                        "born_week": 3}]
    write_yaml(path, state)

    doc = briefing.build("testvale", since_week=0)
    kinds = {i["kind"] for i in doc["items"]}
    assert {"event", "rumor", "stale_fact", "pending_reviews"} <= kinds, kinds
    rumor = next(i for i in doc["items"] if i["kind"] == "rumor")
    assert rumor["week"] == 3 and rumor["detail"] == "fidelity high"
    stale = next(i for i in doc["items"] if i["kind"] == "stale_fact")
    assert stale["status"] == "needs_attention"
    assert "reconcile" in stale["detail"]
    pending = next(i for i in doc["items"] if i["kind"] == "pending_reviews")
    assert "1 pending review file" in pending["title"]


def test_briefing_focus_narrows_and_refs_link_chronicles(
        client, tmp_spine_root):
    path = tmp_spine_root / "testvale" / "simulation" / "state.yaml"
    state = yaml_load(path)
    state["events"] = [
        {"week": 2, "actor": "wardens", "text": "wardens week-2 deed"},
        {"week": 2, "actor": "mint", "text": "mint week-2 move"},
    ]
    write_yaml(path, state)
    # a chronicle for week 2 -> the event item should link it as a ref
    chron_dir = tmp_spine_root / "testvale" / "simulation" / "chronicle"
    chron_dir.mkdir(parents=True, exist_ok=True)
    (chron_dir / "0002-the-deed.md").write_text(
        "# Week 2 — The Deed\n\nThey acted.\n", encoding="utf-8")

    unfocused = briefing.build("testvale", since_week=0)
    # the fixture may carry other item kinds — judge the seeded events
    assert {i["actor"] for i in unfocused["items"]
            if i["kind"] == "event"} == {"wardens", "mint"}

    focused = briefing.build("testvale", since_week=0, focus=["mint"])
    assert {i["actor"] for i in focused["items"]
            if i["kind"] == "event"} == {"mint"}, \
        "focus filters items to the named faction/place"
    assert focused["focus"] == ["mint"]
    event = next(i for i in focused["items"] if i["kind"] == "event")
    assert any(r["type"] == "chronicle" and r["file"].endswith("0002-the-deed.md")
               for r in event["refs"]), \
        "the supporting chronicle rides along as a ref"


def test_briefing_labels_pending_proposals_as_proposed(
        client, tmp_spine_root, monkeypatch):
    import engines
    from conftest import wait_for_job

    def fake_tick(engine_root, script, *args, timeout=600, on_line=None,
                  env=None):
        world = engines.ENGINE_HOME
        world.mkdir(parents=True, exist_ok=True)
        world.joinpath("state.yaml").write_text(
            "meta:\n  clock:\n    week: 4\n", encoding="utf-8")
        engines.engine_chronicle_dir().mkdir(exist_ok=True)
        (engines.engine_chronicle_dir() / "0004-proposed.md").write_text(
            "# Week 4 — Proposed\n\nNot canon.\n", encoding="utf-8")
        return 0, ["week 4 written"]

    monkeypatch.setattr(engines, "run_py", fake_tick)
    monkeypatch.setattr(engines, "ENGINE_HOME",
                        tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME",
                        tmp_spine_root / "chronicle")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]

    doc = briefing.build("testvale", since_week=0)
    prop = [i for i in doc["items"] if i["kind"] == "proposal"]
    assert prop, "the pending proposal is visible to the DM"
    assert prop[0]["status"] == "pending"
    assert "NOT CANON" in doc["markdown"].upper()
    # and it is NOT listed as an approved change: no event item claims
    # week 4 happened
    assert all(i["kind"] != "event" or i["week"] < 4
               for i in doc["items"] if i["status"] == "canon")


def test_disclosure_round_trip_and_empty_handout(client, tmp_spine_root):
    # empty disclosure -> empty handout WITH an explanation, never the
    # DM briefing
    r = client.get("/api/campaign/testvale/disclosure/handout")
    assert r.status_code == 200
    text = r.text
    assert "Nothing has been disclosed" in text
    assert "wardens" not in text.lower() or "deed" not in text

    fact = _add_fact(client,
                     text="The Vane ferry now runs only at dawn.",
                     visibility="public")
    secret = _add_fact(client,
                       text="SECRET: the ferry master is a wardens' agent.",
                       visibility="dm_only")

    # revealing a dm_only fact is refused — faction/DM knowledge has no
    # accidental path to the party
    r = client.post(f"/api/campaign/testvale/knowledge/{secret['id']}/reveal")
    assert r.status_code == 422 and "public" in r.json()["error"]

    r = client.post(
        f"/api/campaign/testvale/knowledge/{fact['id']}/reveal",
        json={"week": 4})
    assert r.status_code == 200, r.text
    # revealing twice is idempotent
    client.post(f"/api/campaign/testvale/knowledge/{fact['id']}/reveal",
                json={"week": 4})

    r = client.get("/api/campaign/testvale/disclosure/handout")
    text = r.text
    assert "The Vane ferry now runs only at dawn." in text
    assert "known since week 4" in text
    assert "SECRET" not in text            # the secret is absent entirely
    assert secret["id"] not in text        # including its id/metadata
    assert "simulation/knowledge.yaml" not in text  # no file titles
    assert "coin" not in text and "muscle" not in text  # no mechanics

    # revoke removes it again
    r = client.post(f"/api/campaign/testvale/knowledge/{fact['id']}/revoke")
    assert r.status_code == 200
    text = client.get("/api/campaign/testvale/disclosure/handout").text
    assert "dawn" not in text


def test_superseded_fact_cannot_be_revealed_after_the_fact(
        client, tmp_spine_root):
    fact = _add_fact(client, text="Old bridge toll is two coppers.",
                     visibility="public")
    client.post(f"/api/campaign/testvale/knowledge/{fact['id']}/supersede",
                json={"text": "The bridge toll is now four coppers.",
                      "visibility": "public"})
    # the OLD fact was superseded after the reveal? No reveal yet — but the
    # new fact is what a fresh reveal sees; the old one is retired
    r = client.post(f"/api/campaign/testvale/knowledge/{fact['id']}/reveal")
    assert r.status_code == 422


def _yaml_load(path):
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


yaml_load = _yaml_load


def write_yaml(path, doc):
    import yaml
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                                   width=100), encoding="utf-8")
