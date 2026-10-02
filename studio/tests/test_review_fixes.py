"""Regression tests for the 2026-09-30 consolidated review
(docs/reviews/2026-09-30-current-iteration.md), studio side.

DND-01  prepare conflicts with a mid-flight draft edit (edit survives)
DND-05  manual drafting through the exposed API (create draft, add cards)
DND-07  a superseding knowledge fact inherits subjects/place associations
DND-06  clearing the preferred map actually clears it
DND-08  ambiguous map slugs are refused; detach only from the owner
DND-09  page mutations hash the same bytes they parsed
DND-10  event locators carry the revision of the exact bytes on screen
DND-12  the campaign lock excludes a second PROCESS (server vs CLI)
DND-13  an orphan notes revision is recovered, never blocks appends
DND-14  accept finalizes even when session bookkeeping is damaged
DND-15  duplicate selected ids cannot mint duplicate facts
DND-16  malformed confirmation values are blockers, not 500s
DND-17  renaming a place with a scalar alias works
DND-18  unsafe preferred-map paths are a 422
DND-19  linking a non-place page is refused
DND-20  an explicit suggestion week 0 stays week 0
OBS-03  cycle findings name only the cycle members
"""

import hashlib
import subprocess
import sys

import knowledge
import places as place_store
import proposals
import sessions as session_store
import spine
import yaml
from conftest import client  # noqa: F401

C = "/api/campaign/testvale"


def _state(tmp_spine_root):
    return yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))


def _page(tmp_spine_root, name, front, body="A place of no consequence."):
    meta = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
    p = (tmp_spine_root / "testvale" / "lore" / "entities" / f"{name}.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{meta}---\n\n{body}\n", encoding="utf-8")
    return p


NOTES = "The party burned the toll treasury at the Vane crossing.\n"


def _mk_session(client, **kw):
    r = client.post(f"{C}/sessions", json={
        "title": "The treasury fire", "notes": NOTES, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _resource_sug(confirm=True, **over):
    s = {"kind": "resource_change", "faction_id": "wardens",
         "resource": "coin", "expected_before": "medium", "after": "none",
         "reason": "The toll treasury burned at the Vane crossing.",
         "evidence": [{"notes_path": None, "line_start": 1, "line_end": 1,
                       "quote": "burned the toll treasury"}],
         "confirmed": {"resource_grade": confirm} if confirm else {}}
    s.update(over)
    return s


def _draft_via_api(client, sid, suggestions):
    """DND-05: the no-LLM path through the EXPOSED route, never the
    service layer directly."""
    r = client.post(f"{C}/sessions/{sid}/drafts",
                    json={"suggestions": suggestions})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------- DND-01


def test_prepare_conflicts_when_draft_edited_mid_flight(
        client, tmp_spine_root, monkeypatch):
    s = _mk_session(client)
    sid = s["session_id"]
    draft = _draft_via_api(client, sid, [_resource_sug()])
    sug_id = draft["suggestions"][0]["id"]

    import engines
    real_run_py = engines.run_py
    raced = {"done": False}

    def racing_run_py(*a, **kw):
        # a concurrent editor saves while the engine subprocess runs
        # (once — the engine is called for --check and --apply)
        if not raced["done"]:
            raced["done"] = True
            with spine.campaign_lock("testvale"):
                session_store.update_draft(
                    "testvale", sid, draft["draft_id"],
                    suggestions=[{**draft["suggestions"][0],
                                  "reason": "Corrected consequence text."}],
                    expected_revision=draft["revision"])
        return real_run_py(*a, **kw)

    monkeypatch.setattr(engines, "run_py", racing_run_py)
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [sug_id], "draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"]})
    assert r.status_code == 409, r.text
    assert "nothing was staged" in r.json()["error"]
    assert proposals.find_pending("testvale") is None
    # the concurrent edit survived — both its text and its revision
    live = session_store.load_draft("testvale", sid, draft["draft_id"])
    assert live["revision"] == draft["revision"] + 1
    assert live["suggestions"][0]["reason"] == "Corrected consequence text."


# ------------------------------------------------------------- DND-05


def test_manual_drafting_through_the_api(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    history = {"kind": "history_event", "actor": "wardens",
               "text": "The party burned the toll treasury.",
               "evidence": [{"notes_path": None, "line_start": 1,
                             "line_end": 1,
                             "quote": "burned the toll treasury"}]}
    draft = _draft_via_api(client, sid, [_resource_sug(), history])
    assert draft["origin"] == "manual"
    ids = [x["id"] for x in draft["suggestions"]]
    assert len(ids) == 2 and all(ids)

    # add a card to the existing draft (the editor's "add suggestion")
    r = client.post(
        f"{C}/sessions/{sid}/drafts/{draft['draft_id']}/suggestions",
        json={"suggestions": [{"kind": "knowledge_fact",
                               "text": "The toll treasury was burned."},
                              # an EXPLICIT id that already exists is refused
                              {"id": ids[0], "kind": "history_event",
                               "actor": "world", "text": "dupe"}]})
    assert r.status_code == 422 and "already exists" in r.json()["error"]
    r = client.post(
        f"{C}/sessions/{sid}/drafts/{draft['draft_id']}/suggestions",
        json={"suggestions": [{"kind": "knowledge_fact",
                               "text": "The toll treasury was burned."}]})
    assert r.status_code == 201, r.text
    grown = r.json()
    assert len(grown["suggestions"]) == 3
    assert grown["suggestions"][2]["id"] not in ids

    # an unknown id in a PUT is still refused (a stale card never
    # silently re-enters), while an id-less PUT entry is minted
    r = client.put(f"{C}/sessions/{sid}/drafts/{grown['draft_id']}", json={
        "suggestions": grown["suggestions"] +
        [{"id": "suggestion-deadbeef", "kind": "history_event",
          "actor": "world", "text": "stale"}],
        "expected_revision": grown["revision"]})
    assert r.status_code == 422
    fresh = dict(grown["suggestions"][0])
    fresh.pop("id")
    r = client.put(f"{C}/sessions/{sid}/drafts/{grown['draft_id']}", json={
        "suggestions": grown["suggestions"] + [fresh],
        "expected_revision": grown["revision"]})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert len(saved["suggestions"]) == 4
    assert saved["suggestions"][3]["id"]

    # the whole no-LLM workflow completes: prepare + accept
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [ids[0]],
        "draft_id": grown["draft_id"],
        "expected_draft_revision": saved["revision"]})
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]
    r = client.post(f"{C}/simulation/proposal/{pid}/accept")
    assert r.status_code == 200, r.text
    state = _state(tmp_spine_root)
    wardens = next(f for f in state["factions"] if f["id"] == "wardens")
    assert wardens["resources"]["coin"] == "none"


# ------------------------------------------------------------- DND-07


def test_superseding_fact_inherits_associations(client, tmp_spine_root):
    fact = knowledge.upsert("testvale",
                            text="The bridge was insured twice over.",
                            visibility="dm_only", subjects=["Vane"],
                            place_ids=["vane-crossing"])
    s = _mk_session(client)
    sid = s["session_id"]
    card = {"kind": "knowledge_fact",
            "text": "The bridge was insured three times over.",
            "supersedes_fact_id": fact["id"],
            "evidence": [{"notes_path": None, "line_start": 1,
                          "line_end": 1,
                          "quote": "burned the toll treasury"}]}
    draft = _draft_via_api(client, sid, [card])
    sug = draft["suggestions"][0]
    assert sug["validation"] == [], sug["validation"]
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [sug["id"]], "draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"]})
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]
    r = client.post(f"{C}/simulation/proposal/{pid}/accept")
    assert r.status_code == 200, r.text
    doc = knowledge.load("testvale")
    new = next(f for f in doc["facts"] if f["id"] != fact["id"]
               and f.get("status") == "current")
    assert new["text"].startswith("The bridge was insured three")
    assert new.get("subjects") == ["Vane"], \
        "the replacement lost the subject join"
    assert new.get("place_ids") == ["vane-crossing"], \
        "the replacement lost its place page"
    old = next(f for f in doc["facts"] if f["id"] == fact["id"])
    assert old["status"] == "superseded"


def test_superseding_fact_with_explicit_empty_list_clears_associations(
        client, tmp_spine_root):
    fact = knowledge.upsert("testvale", text="A fact about a place.",
                            visibility="dm_only", place_ids=["somewhere"])
    s = _mk_session(client)
    sid = s["session_id"]
    card = {"kind": "knowledge_fact", "text": "A corrected fact.",
            "supersedes_fact_id": fact["id"], "place_ids": [],
            "evidence": [{"notes_path": None, "line_start": 1,
                          "line_end": 1,
                          "quote": "burned the toll treasury"}]}
    draft = _draft_via_api(client, sid, [card])
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [draft["suggestions"][0]["id"]],
        "draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"]})
    assert r.status_code == 200, r.text
    r = client.post(f"{C}/simulation/proposal/"
                    f"{r.json()['proposal_id']}/accept")
    assert r.status_code == 200, r.text
    doc = knowledge.load("testvale")
    new = next(f for f in doc["facts"] if f["id"] != fact["id"]
               and f.get("status") == "current")
    assert new.get("place_ids") == [], "an explicit [] must clear"


# ------------------------------------------------------- DND-06/17/18/19


def test_clearing_preferred_map_actually_clears(client, tmp_spine_root):
    page = _page(tmp_spine_root, "Ferry Crossing",
                 {"kind": "place", "name": "Ferry Crossing",
                  "place_id": "ferry-crossing",
                  "preferred_map": "maps/cities/provis/provis.svg"})
    r = client.patch(f"{C}/places/ferry-crossing",
                     json={"preferred_map": None})
    view = place_store.place_view("testvale", "ferry-crossing")
    assert view["place"]["lore"]["preferred_map"] == \
        "maps/cities/provis/provis.svg", \
        "a null patch is an omitted field, not a clear"
    r = client.patch(f"{C}/places/ferry-crossing",
                     json={"preferred_map": ""})
    assert r.status_code == 200, r.text
    text = page.read_text(encoding="utf-8")
    assert "preferred_map" not in text
    view = place_store.place_view("testvale", "ferry-crossing")
    assert view["place"]["lore"]["preferred_map"] == ""
    # setting it again works
    r = client.patch(f"{C}/places/ferry-crossing", json={
        "preferred_map": "maps/cities/provis/provis.svg"})
    assert r.status_code == 200
    assert "preferred_map" in page.read_text(encoding="utf-8")


def test_rename_with_scalar_alias(client, tmp_spine_root):
    # the loader tolerates a scalar alias; rename folding used to
    # concatenate the list with the raw scalar (TypeError -> 500)
    page = _page(tmp_spine_root, "The Old Mill",
                 {"kind": "place", "name": "The Old Mill",
                  "place_id": "the-old-mill",
                  "aliases": "The Old Mill"})
    r = client.patch(f"{C}/places/the-old-mill",
                     json={"name": "Miller's Rest"})
    assert r.status_code == 200, r.text
    doc = yaml.safe_load(
        page.read_text(encoding="utf-8").split("---")[1])
    assert doc["name"] == "Miller's Rest"
    assert "The Old Mill" in doc["aliases"], \
        "the old display name must fold into aliases"
    assert set(doc["aliases"]) == {"The Old Mill"}


def test_unsafe_preferred_map_is_422_not_500(client, tmp_spine_root):
    _page(tmp_spine_root, "Ferry Crossing",
          {"kind": "place", "name": "Ferry Crossing",
           "place_id": "ferry-crossing"})
    for bad in ("../x", "C:/tmp/x", "/etc/passwd"):
        r = client.patch(f"{C}/places/ferry-crossing",
                         json={"preferred_map": bad})
        assert r.status_code == 422, (bad, r.status_code, r.text)


def test_link_refuses_non_place_page(client, tmp_spine_root):
    # the state's sim-only place has NO lore page: link would claim the
    # named page for it — here, a kind: people page
    npc = _page(tmp_spine_root, "Alweric Tealeaf copy",
                {"kind": "people", "name": "Alweric the Younger"})
    before = npc.read_text(encoding="utf-8")
    r = client.post(f"{C}/places/provis/link",
                    json={"page": "Alweric Tealeaf copy"})
    assert r.status_code == 422 and "kind: people" in r.json()["error"]
    assert npc.read_text(encoding="utf-8") == before, \
        "the NPC page must be untouched"


# ------------------------------------------------------- DND-08


def _map_note(tmp_spine_root, kind_dir, slug, front=None):
    d = tmp_spine_root / "testvale" / "maps" / kind_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    note = d / f"{slug}.md"
    note.write_text("---\n" + yaml.safe_dump(front or {}, sort_keys=False) +
                    "---\n\nmap\n", encoding="utf-8")
    return d


def test_map_association_ambiguity_and_detach_guard(client, tmp_spine_root):
    _page(tmp_spine_root, "Ruins", {"kind": "place", "name": "Ruins",
                                    "place_id": "ruins"})
    _map_note(tmp_spine_root, "cities", "ruins")
    _map_note(tmp_spine_root, "sites", "ruins")
    # a bare ambiguous slug is refused, never first-match-wins
    r = client.post(f"{C}/places/ruins/associations", json={
        "source_kind": "map", "source_id": "ruins",
        "relationship": "depicts"})
    assert r.status_code == 422 and "ambiguous" in r.json()["error"]
    assert "cities/ruins" in r.json()["error"]
    # the qualified form selects exactly one
    r = client.post(f"{C}/places/ruins/associations", json={
        "source_kind": "map", "source_id": "sites/ruins",
        "relationship": "depicts"})
    assert r.status_code == 200, r.text
    note = (tmp_spine_root / "testvale" / "maps" / "sites" / "ruins" /
            "ruins.md").read_text(encoding="utf-8")
    assert "place_id" in note and "ruins" in note
    other = (tmp_spine_root / "testvale" / "maps" / "cities" / "ruins" /
             "ruins.md").read_text(encoding="utf-8")
    assert "place_id" not in other, "the wrong map was modified"
    # a second place attaching overwrites deliberately...
    _page(tmp_spine_root, "Other Ruins",
          {"kind": "place", "name": "Other Ruins", "place_id": "other-ruins"})
    r = client.post(f"{C}/places/other-ruins/associations", json={
        "source_kind": "map", "source_id": "sites/ruins",
        "relationship": "depicts", "add": True})
    assert r.status_code == 200  # attaching overwrites deliberately
    # ...but detaching from a place that does not own it is refused
    r = client.post(f"{C}/places/ruins/associations", json={
        "source_kind": "map", "source_id": "sites/ruins",
        "relationship": "depicts", "add": False})
    assert r.status_code == 422 and "other-ruins" in r.json()["error"]
    # the owner detaches cleanly
    r = client.post(f"{C}/places/other-ruins/associations", json={
        "source_kind": "map", "source_id": "sites/ruins",
        "relationship": "depicts", "add": False})
    assert r.status_code == 200, r.text
    note = (tmp_spine_root / "testvale" / "maps" / "sites" / "ruins" /
            "ruins.md").read_text(encoding="utf-8")
    assert "place_id" not in note


# ------------------------------------------------------- DND-09/10


def test_page_snapshot_hashes_the_bytes_it_parses(tmp_spine_root):
    p = tmp_spine_root / "testvale" / "lore" / "entities" / "snap.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"---\r\nkind: place\r\nname: Snap\r\n---\r\n\r\nbody\r\n")
    raw, rev = place_store._page_snapshot(p, None)
    # the revision is the hash of the EXACT bytes on disk…
    assert rev == hashlib.sha256(p.read_bytes()).hexdigest()
    # …and CRLF hand-edits still parse their frontmatter (the universal-
    # newline contract read_text used to provide)
    meta, body = place_store._frontmatter_of(raw)
    assert meta == {"kind": "place", "name": "Snap"}
    assert body.strip() == "body"


def test_event_locator_revision_matches_the_viewed_bytes(
        client, tmp_spine_root):
    _page(tmp_spine_root, "Ferry Crossing",
          {"kind": "place", "name": "Ferry Crossing",
           "place_id": "ferry-crossing"})
    # an id-less event explicitly located at the place (locators exist
    # only for events without a stable id)
    state_path = tmp_spine_root / "testvale" / "simulation" / "state.yaml"
    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    doc["events"].append({"week": 2, "actor": "wardens",
                          "text": "Something happened at the crossing.",
                          "place_ids": ["ferry-crossing"]})
    state_path.write_text(yaml.safe_dump(doc, sort_keys=False),
                          encoding="utf-8")
    view = place_store.place_view("testvale", "ferry-crossing")
    located = [e for e in view["events"] if "locator" in e]
    assert located, "id-less events must carry locators"
    assert located[0]["locator"]["state_revision"] == \
        hashlib.sha256(state_path.read_bytes()).hexdigest()
    # a state REWRITE after the view (reordering events) stales every
    # locator: the association must refuse, never re-target
    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    doc["events"].reverse()
    state_path.write_text(yaml.safe_dump(doc, sort_keys=False),
                          encoding="utf-8")
    loc = located[0]["locator"]
    r = client.post(f"{C}/places/ferry-crossing/associations", json={
        "source_kind": "event", "source_id": str(loc["index"]),
        "relationship": "occurred_at",
        "expected_revision": loc["state_revision"]})
    assert r.status_code == 409, r.text


# ------------------------------------------------------- DND-12


def test_campaign_lock_excludes_a_second_process(tmp_spine_root=None):
    """The server and a separately launched CLI are different processes:
    the lock file, not the threading registry, must serialize them."""
    import pathlib
    import tempfile
    root = pathlib.Path(tempfile.mkdtemp(prefix="lock-proc-"))
    holder = root / "holder.py"
    holder.write_text(
        "import sys, time, pathlib\n"
        "sys.path.insert(0, r'%s')\n"
        "import spine\n"
        "spine.CAMPAIGNS_ROOT = pathlib.Path(r'%s')\n"
        "lk = spine.campaign_lock('lockproc')\n"
        "assert lk.acquire(timeout=5)\n"
        "print('held', flush=True)\n"
        "time.sleep(4)\n" % (spine.STUDIO_DIR, root))
    p = subprocess.Popen([sys.executable, str(holder)],
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        saved = spine.CAMPAIGNS_ROOT
        spine.CAMPAIGNS_ROOT = root
        try:
            lock = spine.campaign_lock("lockproc")
            assert lock.acquire(timeout=0.5) is False, \
                "a second PROCESS took the 'exclusive' campaign lock"
        finally:
            spine.CAMPAIGNS_ROOT = saved
    finally:
        p.wait()
    saved = spine.CAMPAIGNS_ROOT
    spine.CAMPAIGNS_ROOT = root
    try:
        lock = spine.campaign_lock("lockproc")
        assert lock.acquire(timeout=5) is True, \
            "the dead process did not release the lock"
        lock.release()
    finally:
        spine.CAMPAIGNS_ROOT = saved


# ------------------------------------------------------- DND-13


def test_orphan_notes_revision_is_recovered(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    sdir = tmp_spine_root / "testvale" / "sessions" / sid
    # the crash: notes file written, session.yaml not updated
    orphan = "A revision written just before the crash.\n"
    (sdir / "notes" / "0002.md").write_text(orphan, encoding="utf-8")
    doc = session_store.load("testvale", sid)
    r = client.post(f"{C}/sessions/{sid}/notes", json={
        "notes": "A later sitting happened.\n",
        "expected_revision": doc["revision"]})
    assert r.status_code == 201, r.text
    detail = client.get(f"{C}/sessions/{sid}").json()
    notes = detail["notes"]
    assert len(notes) == 3
    assert notes[1]["recovered"] is True
    assert notes[1]["text"] == orphan, "the DM must see the recovered text"
    assert notes[2]["text"] == "A later sitting happened.\n"
    # an identical retry lands ON the orphan (idempotent), no duplicate
    retry = "Retried text.\n"
    (sdir / "notes" / "0004.md").write_text(retry, encoding="utf-8")
    r = client.post(f"{C}/sessions/{sid}/notes", json={
        "notes": retry, "expected_revision": 2})  # 1 -> 2 by the append
    assert r.status_code == 201, r.text
    detail = client.get(f"{C}/sessions/{sid}").json()
    assert len(detail["notes"]) == 4
    assert detail["notes"][3]["text"] == retry
    assert detail["notes"][3]["recovered"] is True
    assert not (sdir / "notes" / "0005.md").exists()


# ------------------------------------------------------- DND-14


def test_accept_finalizes_when_session_bookkeeping_is_damaged(
        client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    draft = _draft_via_api(client, sid, [_resource_sug()])
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [draft["suggestions"][0]["id"]],
        "draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"]})
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]
    # corrupt the session metadata AFTER preparation
    (tmp_spine_root / "testvale" / "sessions" / sid / "session.yaml") \
        .write_text("{not: [valid", encoding="utf-8")
    r = client.post(f"{C}/simulation/proposal/{pid}/accept")
    assert r.status_code == 200, \
        "advisory bookkeeping must not fail the accept"
    assert _state(tmp_spine_root)["factions"][0]["resources"]["coin"] \
        == "none"
    # the idempotent replay answers too (it used to hit the same failure)
    r = client.post(f"{C}/simulation/proposal/{pid}/accept")
    assert r.status_code == 200 and r.json().get("idempotent")


# ------------------------------------------------------- DND-15


def test_duplicate_selected_ids_cannot_mint_duplicate_facts(
        client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    card = {"kind": "knowledge_fact",
            "text": "The toll treasury was burned by adventurers.",
            "evidence": [{"notes_path": None, "line_start": 1,
                          "line_end": 1,
                          "quote": "burned the toll treasury"}]}
    draft = _draft_via_api(client, sid, [card])
    sug_id = draft["suggestions"][0]["id"]
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [sug_id, sug_id], "draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"]})
    assert r.status_code == 200, r.text
    assert r.json()["selected"] == [sug_id], "one selection, not two"
    r = client.post(f"{C}/simulation/proposal/"
                    f"{r.json()['proposal_id']}/accept")
    assert r.status_code == 200 and r.json()["facts_added"] == 1
    doc = knowledge.load("testvale")
    hits = [f for f in doc["facts"]
            if f["text"] == "The toll treasury was burned by adventurers."]
    assert len(hits) == 1


# ------------------------------------------------------- DND-16


def test_malformed_confirmation_is_a_blocker_not_500(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    bad = _resource_sug()
    bad["confirmed"] = "yes"  # a string, not a mapping
    draft = _draft_via_api(client, sid, [bad])
    saved = draft["suggestions"][0]
    assert any("CONFIRM" in v for v in saved["validation"]), \
        "malformed input must never count as approval"
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [saved["id"]], "draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"]})
    assert r.status_code == 422, r.text
    # a truthy NON-boolean flag is not approval either
    bad2 = _resource_sug()
    bad2["confirmed"] = {"resource_grade": "yes"}
    draft2 = _draft_via_api(client, sid, [bad2])
    saved2 = draft2["suggestions"][0]
    assert any("CONFIRM" in v for v in saved2["validation"])
    # and the visibility confirmation has the same strictness
    bad3 = {"kind": "knowledge_fact", "text": "A public fact.",
            "visibility": "public", "confirmed": "yes"}
    draft3 = _draft_via_api(client, sid, [bad3])
    saved3 = draft3["suggestions"][0]
    assert any("CONFIRM" in v for v in saved3["validation"])


# ------------------------------------------------------- DND-20


def test_suggestion_week_zero_is_preserved(client, tmp_spine_root):
    s = _mk_session(client, event_week=1)
    sid = s["session_id"]
    hist = {"kind": "history_event", "actor": "wardens",
            "text": "A prologue raid, long before.",
            "week": 0,
            "evidence": [{"notes_path": None, "line_start": 1,
                          "line_end": 1,
                          "quote": "burned the toll treasury"}]}
    res = _resource_sug(week=0)
    draft = _draft_via_api(client, sid, [hist, res])
    assert all(x["validation"] == [] for x in draft["suggestions"])
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [x["id"] for x in draft["suggestions"]],
        "draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"]})
    assert r.status_code == 200, r.text
    r = client.post(f"{C}/simulation/proposal/"
                    f"{r.json()['proposal_id']}/accept")
    assert r.status_code == 200, r.text
    state = _state(tmp_spine_root)
    weeks = {e["text"]: e["week"] for e in state["events"]}
    assert weeks["A prologue raid, long before."] == 0, \
        "the explicit week 0 fell through to the event week"
    assert any(e["week"] == 0 and e.get("effect")
               for e in state["events"]), \
        "the effect's audit event lost its week 0"


# ------------------------------------------------------- OBS-03


def test_cycle_finding_names_only_the_cycle(tmp_spine_root):
    _page(tmp_spine_root, "A", {"kind": "place", "name": "A",
                                "place_id": "a", "parent_place_id": "b"})
    _page(tmp_spine_root, "B", {"kind": "place", "name": "B",
                                "place_id": "b", "parent_place_id": "a"})
    _page(tmp_spine_root, "C", {"kind": "place", "name": "C",
                                "place_id": "c", "parent_place_id": "a"})
    reg = place_store.registry("testvale")
    cycle_findings = [f for f in reg["findings"]
                      if f["rule"] == "places/parent-cycle"]
    assert cycle_findings, "the A<->B cycle must be reported"
    members = cycle_findings[0]["message"].split("through: ")[1].split(", ")
    assert set(members) == {"a", "b"}, cycle_findings[0]["message"]
