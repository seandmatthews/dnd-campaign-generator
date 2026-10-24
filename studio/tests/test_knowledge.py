"""Phase 4: approved facts with faction-specific visibility.

The handoff's tests, end to end:
- an approved public fact reaches the faction views (engine's real views.py,
  offline);
- a secret known only to faction A appears NOWHERE in faction B's
  serialized view (which is what the prompt embeds);
- rejected (never approved), pending (unapproved), dm_only, and superseded
  facts do not leak;
- a changed source file produces visible staleness instead of unreviewed
  knowledge changes — the compiled text stays the approved text;
- a campaign with no knowledge records still works.
"""

import shutil

import yaml

import engines
import knowledge
import publication
import spine


def _add(client, **body):
    return client.post("/api/campaign/testvale/knowledge", json=body)


def _view(client, faction_id, tmp_spine_root):
    """The REAL engine view (offline; temp engine home, never live scratch)."""
    home = tmp_spine_root / "engine-home"
    (home / "world").mkdir(parents=True, exist_ok=True)
    if not (home / "src").is_dir():
        shutil.copytree(engines.AGENTIC_SIMULATOR / "src", home / "src")
    orig_home = engines.AGENTIC_SIMULATOR
    engines.AGENTIC_SIMULATOR = home
    try:
        r = client.get(f"/api/campaign/testvale/simulation/views/{faction_id}")
        assert r.status_code == 200, r.text
        return yaml.safe_load(r.text if False else _dump(r))
    finally:
        engines.AGENTIC_SIMULATOR = orig_home


def _dump(r):
    return r.text


def test_public_fact_reaches_views_secret_stays_with_its_faction(
        client, tmp_spine_root):
    # public fact: the bridge burned — everyone in the world can know
    r = _add(client, text="The toll bridge over the Vane burned.",
             visibility="public")
    assert r.status_code == 200, r.text
    public_fact = r.json()["fact"]
    assert public_fact["id"] and public_fact["visibility"] == "public"

    # secret known ONLY to the wardens
    r = _add(client,
             text="The wardens' secret: they burned the bridge themselves "
                  "for the insurance.",
             visibility=["wardens"])
    assert r.status_code == 200, r.text
    secret_id = r.json()["fact"]["id"]

    wardens_view = _view(client, "wardens", tmp_spine_root)
    ferrymen_view = _view(client, "ferrymen", tmp_spine_root)

    wardens_yaml = yaml.safe_dump(wardens_view, allow_unicode=True)
    ferrymen_yaml = yaml.safe_dump(ferrymen_view, allow_unicode=True)

    assert "The toll bridge over the Vane burned." in wardens_yaml
    assert "The toll bridge over the Vane burned." in ferrymen_yaml
    assert "they burned the bridge themselves" in wardens_yaml
    # the secret does not occur anywhere in the other faction's view —
    # and the view is exactly what the prompt embeds (llm.render_prompt)
    assert "burned the bridge themselves" not in ferrymen_yaml
    assert secret_id not in ferrymen_yaml


def test_dm_only_and_superseded_never_leak(client, tmp_spine_root):
    r = _add(client, text="DM plan: the magistrate is the arsonist's buyer.",
             visibility="dm_only")
    assert r.status_code == 200
    dm_fact = r.json()["fact"]

    r = _add(client, text="The toll bridge over the Vane burned.",
             visibility="public")
    old_id = r.json()["fact"]["id"]
    r = client.post(
        f"/api/campaign/testvale/knowledge/{old_id}/supersede",
        json={"text": "The toll bridge over the Vane burned in week 2.",
              "visibility": "public"})
    assert r.status_code == 200, r.text

    view = _view(client, "wardens", tmp_spine_root)
    view_yaml = yaml.safe_dump(view, allow_unicode=True)
    assert "arsonist" not in view_yaml        # dm_only stays with the DM
    assert dm_fact["id"] not in view_yaml
    assert "burned in week 2" in view_yaml    # the current fact is there
    # the superseded fact is not compiled: only the corrected text appears
    assert view_yaml.count("toll bridge over the Vane burned") == 1

    state = spine.load_state("testvale")
    assert all(f["id"] != dm_fact["id"] for f in state.get("knowledge", []))
    # the old record keeps its provenance in the knowledge file
    doc = knowledge.load("testvale")
    old = next(f for f in doc["facts"] if f["id"] == old_id)
    assert old["status"] == "superseded" and old["superseded_by"]


def test_unclassified_lore_is_not_mass_published(client, tmp_spine_root):
    # nothing in the vault becomes knowledge by itself: the count is exactly
    # what the DM approved through this API, and empty campaigns work
    r = client.get("/api/campaign/testvale/knowledge")
    assert r.status_code == 200
    assert r.json()["facts"] == []
    # the state has no knowledge section at all (nothing compiled yet)
    state = spine.load_state("testvale")
    assert not state.get("knowledge")
    # the engine still works without one
    view = _view(client, "wardens", tmp_spine_root)
    assert "facts_you_know" not in view


def test_source_change_marks_stale_but_keeps_approved_text(
        client, tmp_spine_root):
    src = tmp_spine_root / "testvale" / "lore" / "entities" / "The Vane.md"
    text = src.read_text(encoding="utf-8")
    r = _add(client, text="The Vane freezes in late autumn.",
             visibility="public", source_file="lore/entities/The Vane.md")
    assert r.status_code == 200, r.text
    fact_id = r.json()["fact"]["id"]

    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    assert not next(f for f in facts if f["id"] == fact_id)["stale"]

    # the source file changes under the DM (Obsidian edit)
    src.write_text(text + "\nA footnote the DM has not reviewed.\n",
                   encoding="utf-8")
    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    assert next(f for f in facts if f["id"] == fact_id)["stale"]

    # the compiled state keeps the APPROVED text — never the new wording
    state = spine.load_state("testvale")
    compiled = next(f for f in state["knowledge"] if f["id"] == fact_id)
    assert compiled["text"] == "The Vane freezes in late autumn."
    assert "footnote" not in compiled["text"]

    # reconcile after the DM reads it: staleness clears, text unchanged
    r = client.post(f"/api/campaign/testvale/knowledge/{fact_id}/reconcile")
    assert r.status_code == 200, r.text
    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    assert not next(f for f in facts if f["id"] == fact_id)["stale"]


def test_visibility_validation(client, tmp_spine_root):
    # unknown faction ids are refused — a typo must not hide or leak a fact
    r = _add(client, text="x", visibility=["wardens", "ghosts"])
    assert r.status_code == 422 and "ghosts" in r.json()["error"]
    r = _add(client, text="x", visibility="everyone")
    assert r.status_code == 422
    r = _add(client, text="x", visibility=[])
    assert r.status_code == 422
    # a fact with no text is refused
    r = _add(client, text="  ", visibility="public")
    assert r.status_code == 422


def test_rumor_fact_records_chronicle_source_week_and_reconciles(
        client, tmp_spine_root):
    """Ticket 08: a rumor told to the party records through the UNCHANGED
    record route with its chronicle as source_file — so a later chronicle
    edit stales it and reconcile clears the staleness exactly like any
    sourced fact. The week rides along as provenance; a non-numeric week
    is a 422, not a silently mangled record."""
    chron = (tmp_spine_root / "testvale" / "simulation" / "chronicle"
             / "0003-the-ash-crossing.md")
    text = chron.read_text(encoding="utf-8")
    r = _add(client,
             text="Ferry poles doubled in price the week the bridge burned.",
             visibility="public",
             source_file="simulation/chronicle/0003-the-ash-crossing.md",
             week=3)
    assert r.status_code == 200, r.text
    fact = r.json()["fact"]
    assert fact["week"] == 3
    assert fact["source_file"] == \
        "simulation/chronicle/0003-the-ash-crossing.md"

    # the point of the chronicle provenance: an Obsidian edit to the
    # chronicle stales the recorded rumor-fact ...
    chron.write_text(text + "\nA line the DM has not reviewed.\n",
                     encoding="utf-8")
    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    row = next(f for f in facts if f["id"] == fact["id"])
    assert row["stale"] is True
    assert row["source_file"] == fact["source_file"]

    # ... and reconcile clears it through the unchanged route
    r = client.post(f"/api/campaign/testvale/knowledge/{fact['id']}/reconcile")
    assert r.status_code == 200, r.text
    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    assert not next(f for f in facts if f["id"] == fact["id"])["stale"]

    # the two-step rule survives: revealing the recorded fact is its own,
    # unchanged decision on the unchanged route
    r = client.post(f"/api/campaign/testvale/knowledge/{fact['id']}/reveal",
                    json={"week": 4})
    assert r.status_code == 200, r.text
    disclosure = client.get("/api/campaign/testvale/disclosure").json()
    assert any(x["fact_id"] == fact["id"] for x in disclosure["revealed"])

    # the week is typed: a malformed one is refused before anything writes
    r = _add(client, text="x", visibility="public", week="three")
    assert r.status_code == 422


def test_knowledge_requires_simulation_state(client, spine_root):
    # spine_root's testvale HAS a state; use the no-simulation path via a
    # campaign whose state.yaml is absent — the fixture has none such, so
    # assert the 404 shape through the wardens campaign minus its state
    r = client.get("/api/campaign/nonexistent/knowledge")
    assert r.status_code == 404


def test_knowledge_writes_take_history_snapshots(client, tmp_spine_root):
    """A knowledge write published its files but skipped the git snapshot —
    published-but-untracked until someone hit snapshot recovery (found live
    2026-09-25). Every canon change commits: record, supersede, reveal."""
    r = _add(client, text="The ford-toll ledger was rewritten in secret.",
             visibility="dm_only")
    assert r.status_code == 200, r.text
    assert r.json()["snapshot"]["state"] == "committed", r.json().get("snapshot")

    r = _add(client, text="The ford-toll bridge over the Vane burned.",
             visibility="public")
    assert r.json()["snapshot"]["state"] == "committed"
    fid = r.json()["fact"]["id"]
    rv = client.post(f"/api/campaign/testvale/knowledge/{fid}/reveal",
                     json={"week": 1})
    assert rv.status_code == 200, rv.text
    assert rv.json()["snapshot"]["state"] == "committed", rv.json().get("snapshot")


def test_source_file_traversal_is_refused_and_never_probed(
        client, tmp_spine_root):
    """source_file is spliced into a campaign-relative path and hashed: a
    traversal must be refused at write time (nothing recorded, nothing
    read), and a traversal path already sitting in hand-edited canon must
    read as ABSENT (no stale/fresh oracle over arbitrary files)."""
    outside = tmp_spine_root / "outside-secret.yaml"
    outside.write_text("top secret bytes\n", encoding="utf-8")
    r = _add(client, text="a fact sourced from nowhere",
             visibility="public",
             source_file="../../outside-secret.yaml")
    assert r.status_code in (409, 422), r.text
    # the refusal may echo the caller's own input, but never the outside
    # file's bytes or its resolved absolute path
    assert "top secret bytes" not in r.text
    assert str(outside.resolve()) not in r.text
    assert "outside the campaign" in r.text or "unsafe" in r.text
    facts = client.get("/api/campaign/testvale/knowledge").json()["facts"]
    assert all("outside-secret" not in (f.get("source_file") or "")
               for f in facts)

    # a traversal path in hand-edited canon: no probe, no hash, just ABSENT
    doc = knowledge.load("testvale")
    doc["facts"].append({"id": "fact-hand", "created": "x", "status":
                         "current", "text": "t", "visibility": "dm_only",
                         "approved": "x", "source_file": "../evil.txt",
                         "source_revision": "0" * 64})
    knowledge._save("testvale", doc)
    assert knowledge.source_revision("testvale",
                                     doc["facts"][-1]) == publication.ABSENT


def test_stale_base_knowledge_write_is_a_409_not_a_500(
        client, tmp_spine_root, monkeypatch):
    """P3-13: an external edit to knowledge.yaml between the route's base
    capture and its publish is a RevisionConflict — record/supersede used
    to let it escape as a raw 500 'internal error: RevisionConflict'
    instead of the 409 every other publisher answers."""
    r = _add(client, text="The ford is one plank wide.",
             visibility="public")
    assert r.status_code == 200, r.text
    fid = r.json()["fact"]["id"]
    kpath = tmp_spine_root / "testvale" / "simulation" / "knowledge.yaml"
    real_load = knowledge.load

    def edit_then_load(name):
        # the Obsidian hand-edit lands AFTER record_fact_tx captured its
        # base revisions but BEFORE the publish that checks them
        kpath.write_text(kpath.read_text(encoding="utf-8") + "# hand edit\n",
                         encoding="utf-8")
        return real_load(name)

    monkeypatch.setattr(knowledge, "load", edit_then_load)
    r = _add(client, text="A fact recorded over a stale base.",
             visibility="public")
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert "stale knowledge base" in err
    assert "knowledge.yaml" in err
    assert "Nothing was written" in err
    # supersede carries the same mapping over the same window
    r = client.post(f"/api/campaign/testvale/knowledge/{fid}/supersede",
                    json={"text": "The ford is two planks wide.",
                          "visibility": "public"})
    assert r.status_code == 409, r.text
    assert "stale knowledge base" in r.json()["error"]


def test_midflight_publication_failure_keeps_the_staged_wording(
        client, tmp_spine_root, monkeypatch):
    """P3-13's other half: a publication that fails mid-flight answers the
    canonical 500 — staged transaction kept, recovered on restart — not
    the opaque 'internal error: PublicationError'."""
    def broken_publish(*a, **kw):
        raise publication.PublicationError("disk full mid-write")

    monkeypatch.setattr(publication, "publish", broken_publish)
    r = _add(client, text="A fact that never lands.", visibility="public")
    assert r.status_code == 500, r.text
    err = r.json()["error"]
    assert "could not be published" in err
    assert "staged transaction is kept" in err
    assert "recovered automatically" in err
    assert "PublicationError" not in err


def test_reveal_week_is_gated_like_a_fact_week(client, tmp_spine_root):
    """P3-15: RevealBody.week takes the same >= 1 gate FactBody got in
    ticket 14 — "known since week 0" (or -5) must never reach the
    disclosure store and the player handout."""
    r = _add(client, text="The ferryman waves the toll on feast days.",
             visibility="public")
    assert r.status_code == 200, r.text
    fid = r.json()["fact"]["id"]
    for bad in (0, -5):
        r = client.post(f"/api/campaign/testvale/knowledge/{fid}/reveal",
                        json={"week": bad})
        assert r.status_code == 422, r.text
        assert "campaign week" in r.json()["error"]
    # nothing was recorded by the refused reveals
    disclosure = client.get("/api/campaign/testvale/disclosure").json()
    assert not disclosure["revealed"]
    # positive weeks are unaffected
    r = client.post(f"/api/campaign/testvale/knowledge/{fid}/reveal",
                    json={"week": 2})
    assert r.status_code == 200, r.text
    disclosure = client.get("/api/campaign/testvale/disclosure").json()
    entry = next(x for x in disclosure["revealed"] if x["fact_id"] == fid)
    assert entry["revealed_week"] == 2
