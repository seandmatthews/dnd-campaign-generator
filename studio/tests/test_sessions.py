"""Session notes -> reviewed campaign changes (docs/plans/
session-notes-to-changes.md) acceptance tests.

Covers: session/notes lifecycle, manual no-LLM drafting, deterministic
preparation (canon untouched until accept), the combined transactional
acceptance (state + knowledge + disclosure + decision record), conflicts,
disclosure dependencies, evidence validation, stale-base 409s, the
single-pending-proposal rule, mocked-provider extraction (including a
malformed response), and the CLI against the same service layer.
Preparation runs the REAL engine effects subprocess; the only mock in the
suite is llm_client.complete — no live API credits anywhere.
"""


import knowledge
import proposals
import sessions as session_store
import yaml
from conftest import client, wait_for_job  # noqa: F401

C = "/api/campaign/testvale"


def _state(tmp_spine_root):
    return yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))


def _state_bytes(tmp_spine_root):
    return (tmp_spine_root / "testvale" / "simulation" / "state.yaml") \
        .read_bytes()


def _kb_bytes(tmp_spine_root):
    p = (tmp_spine_root / "testvale" / "simulation" / "knowledge.yaml")
    return p.read_bytes() if p.exists() else None


# The session fixture the plan asks for: ambiguous names ("the Mint" does
# not exist here; "the Ferrymen" matches), a resource claim needing DM
# confirmation, a private secret, and an unsupported claimed consequence
# (the sky opening) the DM must either ground or drop.
NOTES = """The party burned the toll treasury at the Vane crossing.
The Bridge Wardens' coin reserve is gone; the ferrymen saw the smoke.
Captain Vane of the toll house takes bribes, but the party promised to keep it quiet.
That night the sky itself opened above Provis.
"""


def _mk_session(client, notes=NOTES, **kw):
    body = {"title": "The treasury fire", "notes": notes, **kw}
    r = client.post(f"{C}/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _manual_draft(client, tmp_spine_root, session_id, suggestions):
    """The no-LLM editor path: build a draft straight through the service
    (the UI's PUT route validates against an existing draft — the draft
    itself is born in the service layer for both origins)."""
    return session_store.save_draft(
        "testvale", session_id, origin="manual", suggestions=suggestions)


def _wardens_resource_sug(confirm=True, **over):
    s = {"kind": "resource_change", "faction_id": "wardens",
         "resource": "coin", "expected_before": "medium", "after": "none",
         "reason": "The toll treasury burned at the Vane crossing.",
         "evidence": [{"notes_path": None, "line_start": 1, "line_end": 1,
                       "quote": "burned the toll treasury"}],
         "confirmed": {"resource_grade": confirm} if confirm else {}}
    s.update(over)
    return s


def _history_sug(**over):
    s = {"kind": "history_event", "actor": "wardens",
         "text": "The party burned the toll treasury at the Vane crossing.",
         "evidence": [{"notes_path": None, "line_start": 1, "line_end": 1,
                       "quote": "burned the toll treasury"}]}
    s.update(over)
    return s


def _public_fact(tmp_spine_root):
    """An approved PUBLIC fact to disclose (approval != disclosure)."""
    fact = knowledge.upsert("testvale",
                            text="The bridge was insured twice over.",
                            visibility="public")
    return fact


# ---------------------------------------------------------------- lifecycle


def test_session_notes_lifecycle(client, tmp_spine_root):
    # default event week = the current clock week; records never advance it
    s = _mk_session(client)
    assert s["event_week"] == 3
    # future-dated needs an explicit explanation
    r = client.post(f"{C}/sessions", json={
        "title": "Too early", "notes": "x", "event_week": 9})
    assert r.status_code == 422 and "future" in r.json()["error"].lower()
    r = client.post(f"{C}/sessions", json={
        "title": "Flashback", "notes": "a prologue", "event_week": 1})
    assert r.status_code == 201

    detail = client.get(f"{C}/sessions/{s['session_id']}").json()
    assert detail["notes"][0]["text"].startswith("The party burned")
    rev = detail["notes"][0]["revision"]

    # immutable revisions: append is fine, rewrite is impossible
    r = client.post(f"{C}/sessions/{s['session_id']}/notes", json={
        "notes": "A second sitting happened.", "expected_revision": 99})
    assert r.status_code == 409
    r = client.post(f"{C}/sessions/{s['session_id']}/notes", json={
        "notes": "A second sitting happened.", "expected_revision": 1})
    assert r.status_code == 201 and len(r.json()["notes"]) == 2
    after = client.get(f"{C}/sessions/{s['session_id']}").json()
    assert after["notes"][0]["text"] == detail["notes"][0]["text"]  # immutable
    assert after["notes"][0]["revision"] == rev


def test_unknown_place_and_empty_title_refused(client, tmp_spine_root):
    r = client.post(f"{C}/sessions", json={
        "title": "", "notes": "x"})
    assert r.status_code == 422
    r = client.post(f"{C}/sessions", json={
        "title": "P", "notes": "x", "place_ids": ["not-a-place"]})
    assert r.status_code == 422 and "place" in r.json()["error"]


# ------------------------------------------------------- prepare and accept


def _prepare(client, session_id, draft_id, selected):
    return client.post(f"{C}/sessions/{session_id}/prepare", json={
        "selected": selected, "draft_id": draft_id})


def test_manual_prepare_accept_end_to_end(client, tmp_spine_root):
    fact = _public_fact(tmp_spine_root)
    s = _mk_session(client)
    sid = s["session_id"]
    draft = _manual_draft(client, tmp_spine_root, sid, [
        _wardens_resource_sug(),
        _history_sug(),
        {"kind": "knowledge_fact",
         "text": "The toll treasury was burned by adventurers.",
         "visibility": "public",
         "confirmed": {"knowledge_visibility": True},
         "evidence": [{"notes_path": "notes/0001.md", "line_start": 1,
                       "line_end": 1,
                       "quote": "burned the toll treasury"}]},
        {"kind": "party_disclosure", "fact_id": fact["id"]},
        # EXCLUDED: an unsupported claimed consequence stays in the draft
        {"kind": "history_event", "actor": "world",
         "text": "The sky opened above Provis.", "selected": False},
    ])
    # the excluded suggestion got an id when the draft was saved, never
    # re-minted on edit
    ids = [x["id"] for x in draft["suggestions"]]
    assert len(set(ids)) == len(ids) and all(ids)
    selected = ids[:4]
    for x in draft["suggestions"][:4]:
        assert x["validation"] == [], x["validation"]

    state_before = _state_bytes(tmp_spine_root)
    kb_before = _kb_bytes(tmp_spine_root)
    pk_before = (tmp_spine_root / "testvale" / "simulation" /
                 "party-knowledge.yaml")
    r = _prepare(client, sid, draft["draft_id"], selected)
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]

    # staging changed NOTHING in canon (state, knowledge, disclosure)
    assert _state_bytes(tmp_spine_root) == state_before
    assert _kb_bytes(tmp_spine_root) == kb_before

    assert not pk_before.exists()

    pending = proposals.find_pending("testvale")
    assert pending and pending["kind"] == "session_changes"
    assert pending["session_id"] == sid
    bundle = proposals.load("testvale", pid)
    assert bundle["extra"]["decision"]["selected_suggestions"]
    assert set(bundle["manifest"]["selected_suggestion_ids"]) == set(selected)

    r = client.post(f"{C}/simulation/proposal/{pid}/accept")
    assert r.status_code == 200, r.text
    res = r.json()
    # two events: the effect's sourced audit event + the history bullet
    assert res["events_added"] == 2 and res["resources_changed"] == 1
    assert res["facts_added"] == 1 and res["disclosed"] == 1

    state = _state(tmp_spine_root)
    wardens = next(f for f in state["factions"] if f["id"] == "wardens")
    assert wardens["resources"]["coin"] == "none"
    assert any("treasury" in e["text"] and e.get("effect")
               for e in state["events"])
    assert any(e["actor"] == "wardens" and "treasury" in e["text"]
               and not e.get("effect") for e in state["events"])
    # the public fact compiled into the state's knowledge section
    assert any(k["text"].startswith("The toll treasury")
               for k in state.get("knowledge") or [])
    # disclosure record got the EXISTING fact, not the new one
    pk = yaml.safe_load((tmp_spine_root / "testvale" / "simulation" /
                         "party-knowledge.yaml").read_text("utf-8"))
    revealed = {x["fact_id"] for x in pk["revealed"]}
    assert revealed == {fact["id"]}
    # decision record + bookkeeping
    dec = session_store.load_decision("testvale", sid, pid)
    assert dec and dec["decided"] and len(dec["selected_suggestions"]) == 4
    assert dec["excluded_suggestions"] == [ids[4]]
    detail = client.get(f"{C}/sessions/{sid}").json()
    statuses = {x["id"]: x["status"] for x in detail["drafts"][0]["suggestions"]}
    assert all(statuses[i] == "accepted" for i in selected)
    assert statuses[ids[4]] == "open"  # excluded: still preparable

    # idempotent: accepting the same proposal twice changes nothing twice
    ev_before = len(state["events"])
    r = client.post(f"{C}/simulation/proposal/{pid}/accept")
    assert r.status_code == 200 and r.json().get("idempotent")
    assert len(_state(tmp_spine_root)["events"]) == ev_before

    # the next proposal preparation still works (own prior publication
    # does not permanently stale the session). The sky line was
    # MANUALLY authored, so the DM may prepare it uncited; extraction
    # results, by contrast, can never prepare without support (tested
    # in test_extraction_without_evidence_cannot_prepare below).
    r = _prepare(client, sid, draft["draft_id"], [ids[4]])
    assert r.status_code == 200, r.text
    pid2 = r.json()["proposal_id"]
    r = client.post(f"{C}/simulation/proposal/{pid2}/accept")
    assert r.status_code == 200


def test_same_resource_conflict_refused(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    a = _wardens_resource_sug()
    b = _wardens_resource_sug(after="low", expected_before=None)
    draft = _manual_draft(client, tmp_spine_root, sid, [a, b])
    r = _prepare(client, sid, draft["draft_id"],
                 [x["id"] for x in draft["suggestions"]])
    assert r.status_code == 422 and "consolidate" in r.json()["error"]


def test_disclosure_dependency_on_excluded_fact_refused(client,
                                                        tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    fact = {"kind": "knowledge_fact", "text": "A public accident report.",
            "visibility": "public",
            "confirmed": {"knowledge_visibility": True}}
    disc = {"kind": "party_disclosure"}
    draft = _manual_draft(client, tmp_spine_root, sid, [fact, disc])
    disc_saved = draft["suggestions"][1]
    fact_saved = draft["suggestions"][0]
    assert disc_saved["validation"], "unresolved disclosure must carry a " \
                                     "finding before it is pointed at a fact"
    # point the disclosure at the fact suggestion, then select ONLY it
    draft["suggestions"][1]["fact_suggestion"] = fact_saved["id"]
    draft["suggestions"][1]["validation"] = []
    draft = session_store.update_draft(
        "testvale", sid, draft["draft_id"],
        suggestions=draft["suggestions"],
        expected_revision=draft["revision"])
    disc_saved = draft["suggestions"][1]
    r = _prepare(client, sid, draft["draft_id"], [disc_saved["id"]])
    assert r.status_code == 422
    assert fact_saved["id"] in r.json()["error"]  # the dependency is named


def test_invented_quote_cannot_prepare(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    sug = _wardens_resource_sug(
        evidence=[{"notes_path": "notes/0001.md", "line_start": 2,
                   "line_end": 2,
                   "quote": "the wardens quietly paid everyone off"}])
    draft = _manual_draft(client, tmp_spine_root, sid, [sug])
    saved = draft["suggestions"][0]
    assert any("does not occur" in v for v in saved["validation"])
    r = _prepare(client, sid, draft["draft_id"], [saved["id"]])
    assert r.status_code == 422 and "cannot be prepared" in r.json()["error"]


def test_unconfirmed_grade_cannot_prepare(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    sug = _wardens_resource_sug(confirm=False)
    draft = _manual_draft(client, tmp_spine_root, sid, [sug])
    saved = draft["suggestions"][0]
    assert any("CONFIRM" in v for v in saved["validation"])
    r = _prepare(client, sid, draft["draft_id"], [saved["id"]])
    assert r.status_code == 422


def test_stale_base_accept_is_409_with_no_writes(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    draft = _manual_draft(client, tmp_spine_root, sid, [_wardens_resource_sug()])
    r = _prepare(client, sid, draft["draft_id"],
                 [draft["suggestions"][0]["id"]])
    assert r.status_code == 200
    pid = r.json()["proposal_id"]
    kb_before = _kb_bytes(tmp_spine_root)
    # external edit AFTER the preview: state gained a rumor behind our back
    p = tmp_spine_root / "testvale" / "simulation" / "state.yaml"
    doc = yaml.safe_load(p.read_text("utf-8"))
    doc["rumors"].append({"id": "r_x", "claim": "edited behind the preview",
                          "born_week": 3, "fidelity": "hearsay"})
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    r = client.post(f"{C}/simulation/proposal/{pid}/accept")
    assert r.status_code == 409
    assert "changed" in r.json()["error"]
    # nothing was written — not even the state edit being reverted
    assert yaml.safe_load(p.read_text("utf-8"))["rumors"][-1]["claim"] == \
        "edited behind the preview"
    assert _kb_bytes(tmp_spine_root) == kb_before

    # discard frees the slot; the suggestions reopen
    r = client.post(f"{C}/simulation/proposal/{pid}/discard")
    assert r.status_code == 200
    assert proposals.find_pending("testvale") is None
    detail = client.get(f"{C}/sessions/{sid}").json()
    statuses = {x["id"]: x["status"] for x in detail["drafts"][0]["suggestions"]}
    assert statuses[draft["suggestions"][0]["id"]] == "open"


def test_prepare_respects_single_pending_proposal(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    draft = _manual_draft(client, tmp_spine_root, sid, [_wardens_resource_sug()])
    r = _prepare(client, sid, draft["draft_id"],
                 [draft["suggestions"][0]["id"]])
    assert r.status_code == 200
    first = r.json()["proposal_id"]
    # a second session, prepared while the first proposal is pending
    s2 = _mk_session(client, title="second sitting",
                     notes="The wardens lost more than coin that night.\n")
    draft2 = _manual_draft(client, tmp_spine_root, s2["session_id"], [_wardens_resource_sug(reason="The wardens lost more than coin.", evidence=[{"notes_path": None, "line_start": 1, "line_end": 1, "quote": "lost more than coin"}])])
    r = _prepare(client, s2["session_id"], draft2["draft_id"],
                 [draft2["suggestions"][0]["id"]])
    assert r.status_code == 409 and first in r.json()["error"]
    # the existing proposal is still exactly what it was
    assert proposals.find_pending("testvale")["proposal_id"] == first


def test_already_accepted_suggestion_cannot_reapply(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    draft = _manual_draft(client, tmp_spine_root, sid, [_wardens_resource_sug()])
    sug_id = draft["suggestions"][0]["id"]
    r = _prepare(client, sid, draft["draft_id"], [sug_id])
    pid = r.json()["proposal_id"]
    assert client.post(f"{C}/simulation/proposal/{pid}/accept").status_code \
        == 200
    r = _prepare(client, sid, draft["draft_id"], [sug_id])
    assert r.status_code == 422 and "already accepted" in r.json()["error"]


def test_stale_draft_editor_409(client, tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    draft = _manual_draft(client, tmp_spine_root, sid, [_history_sug()])
    r = client.put(f"{C}/sessions/{sid}/drafts/{draft['draft_id']}", json={
        "suggestions": draft["suggestions"], "expected_revision": 42})
    assert r.status_code == 409


def test_extraction_without_evidence_cannot_prepare(client, tmp_spine_root):
    """An extraction result with no citation at all is unresolved — it can
    never become a mechanical target on the extractor's word alone."""
    s = _mk_session(client)
    sid = s["session_id"]
    draft = session_store.save_draft(
        "testvale", sid, origin="extraction",
        suggestions=[{"kind": "resource_change", "faction_id": "wardens",
                      "resource": "coin", "after": "none",
                      "reason": "The treasury burned.",
                      "evidence": [],
                      "confirmed": {"resource_grade": True}}])
    saved = draft["suggestions"][0]
    assert any("no evidence citation" in v for v in saved["validation"])
    r = _prepare(client, sid, draft["draft_id"], [saved["id"]])
    assert r.status_code == 422


# ---------------------------------------------------------------- extraction


def _mock_llm(monkeypatch, responses):
    """Replace llm_client.complete with a scripted fake (metered shape)."""
    calls = {"n": 0}

    def fake_complete(prompt, **kw):
        calls["n"] += 1
        resp = responses[min(calls["n"], len(responses)) - 1]
        if isinstance(resp, Exception):
            raise resp
        return resp

    import llm_client
    monkeypatch.setattr(llm_client, "complete", fake_complete)
    monkeypatch.setattr(llm_client, "begin_run", lambda *a, **k: None)
    return calls


GOOD_RESPONSE = """Here is what I found in the notes:

```yaml
suggestions:
  - kind: resource_change
    faction: The Bridge Wardens
    resource: coin
    after: none
    reason: The toll treasury burned at the Vane crossing.
    evidence:
      - lines: 1-1
        quote: burned the toll treasury
  - kind: field_change
    faction: The Ferrymen
    field: goal
    after: Celebrate the crossing staying broken
    reason: their rivals' treasury is gone
    evidence:
      - lines: 2-2
        quote: the ferrymen saw the smoke
  - kind: knowledge_fact
    text: Captain Vane of the toll house takes bribes.
    subjects: [Captain Vane]
    evidence:
      - lines: 3-3
        quote: takes bribes
  - kind: party_disclosure
    fact: No such approved fact exists with this text.
    evidence:
      - lines: 3-3
        quote: takes bribes
  - kind: history_event
    actor: The Bridge Wardens
    text: The toll treasury burned at the Vane crossing.
    evidence:
      - lines: 1-1
        quote: burned the toll treasury
  - kind: history_event
    actor: The Mint
    text: An ambiguous faction claimed credit.
    evidence:
      - lines: 1-1
        quote: burned the toll treasury
```
"""


def test_extraction_mocked_provider_end_to_end(client, tmp_spine_root,
                                               monkeypatch):
    _mock_llm(monkeypatch, [GOOD_RESPONSE])
    s = _mk_session(client)
    sid = s["session_id"]
    r = client.post(f"{C}/sessions/{sid}/extract", json={"confirm": "true"})
    assert r.status_code == 422  # StrictBool: the string is not a confirm
    r = client.post(f"{C}/sessions/{sid}/extract", json={"confirm": True})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    out = job["result"]
    assert out["suggestions"] == 6
    draft = session_store.load_draft("testvale", sid, out["draft_id"])
    by_kind = {}
    for x in draft["suggestions"]:
        by_kind.setdefault(x["kind"], []).append(x)
    # identity resolution: exact-name faction match resolved; the ambiguous
    # "Mint" stayed unresolved and CANNOT become a mechanical target
    rc = by_kind["resource_change"][0]
    assert rc["faction_id"] == "wardens"
    assert rc["after"] == "none"
    assert "resource_grade" in rc["requires_confirmation"]
    fc = by_kind["field_change"][0]
    assert fc["faction_id"] == "ferrymen"
    he = by_kind["history_event"]
    assert he[0]["actor"] == "wardens"
    assert he[1]["validation"], "ambiguous actor must carry a finding"
    assert he[1]["actor"] is None  # unresolved -> never silently "world"
    kf = by_kind["knowledge_fact"][0]
    assert kf["visibility"] == "dm_only"  # secrets default to DM-only
    disc = by_kind["party_disclosure"][0]
    assert disc["validation"], "disclosure of nothing must stay unresolved"
    # evidence quotes validated against the recorded notes revision; the
    # grade still awaits the DM's explicit confirmation (extraction may
    # never wave a mechanical grade through on its own)
    assert rc["evidence"][0]["notes_path"] == "notes/0001.md"
    assert rc["validation"] == ["CONFIRM: the exact resource grade needs "
                                "your explicit confirmation"]
    # a retry mints a NEW draft, never a new session
    r = client.post(f"{C}/sessions/{sid}/extract", json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done"
    detail = client.get(f"{C}/sessions/{sid}").json()
    assert len(detail["drafts"]) == 2
    assert detail["drafts"][0]["draft_id"] != detail["drafts"][1]["draft_id"]
    # the extraction spend is recorded
    assert detail["drafts"][-1]["run_record"]["attempts"] == 1


def test_raw_to_suggestions_single_number_evidence_line():
    """A model may cite one line ("lines: 7") instead of a range ("7-9").
    The single-number form matched a regex with no group(2), and the
    group(2) access itself raised IndexError — crashing the whole
    extraction job (found live 2026-09-29)."""
    state = {"factions": [{"id": "wardens", "name": "The Bridge Wardens"}],
             "places": []}
    raw = {"suggestions": [
        {"kind": "resource_change", "faction": "The Bridge Wardens",
         "resource": "coin", "after": "none", "reason": "treasury gone",
         "evidence": [{"lines": "7", "quote": "burned the toll treasury"}]},
        {"kind": "resource_change", "faction": "The Bridge Wardens",
         "resource": "coin", "after": "low", "reason": "partial loss",
         "evidence": [{"lines": "7-9", "quote": "saw the smoke"}]},
    ]}
    sugs, findings = session_store.raw_to_suggestions(
        "s", raw, state, "burned the toll treasury\nsaw the smoke\n")
    assert findings == []
    assert len(sugs) == 2
    assert sugs[0]["evidence"][0]["line_start"] == 7
    assert sugs[0]["evidence"][0]["line_end"] == 7
    assert sugs[1]["evidence"][0]["line_start"] == 7
    assert sugs[1]["evidence"][0]["line_end"] == 9


def test_extraction_malformed_response_is_an_error(client, tmp_spine_root,
                                                   monkeypatch):
    _mock_llm(monkeypatch, ["I could not find anything, sorry! The notes "
                            "mention a treasury, a bridge, and a sky. " * 3])
    s = _mk_session(client)
    sid = s["session_id"]
    r = client.post(f"{C}/sessions/{sid}/extract", json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "no `suggestions:`" in job["error"]
    # notes intact, NO draft (never a fake 'no changes'), session reusable
    detail = client.get(f"{C}/sessions/{sid}").json()
    assert detail["drafts"] == []
    assert detail["notes"][0]["text"].startswith("The party burned")
    r = _prepare(client, sid, "draft-00000000", ["suggestion-00000000"])
    assert r.status_code == 404 or r.status_code == 422


def test_cli_import_prepare_accept(client, tmp_spine_root, tmp_path):
    import session_notes
    rc = session_notes.main([
        "import", "testvale", "--title", "CLI session",
        "--notes", "The party burned the toll treasury at the Vane crossing.\n",
        "--week", "3"])
    assert rc == 0
    rows = session_store.list_sessions("testvale")
    sid = rows[0]["session_id"]
    # manual drafting through the CLI itself (the documented no-LLM path):
    # add reads a YAML file and creates the session's first draft
    sug_file = tmp_path / "suggestions.yaml"
    sug_file.write_text(yaml.safe_dump(
        {"suggestions": [_wardens_resource_sug()]}), encoding="utf-8")
    rc = session_notes.main(
        ["add", "testvale", sid, "--from", str(sug_file)])
    assert rc == 0
    detail = session_store.session_detail("testvale", sid)
    assert detail["drafts"] and detail["drafts"][0]["origin"] == "manual"
    draft = detail["drafts"][0]
    rc = session_notes.main([
        "prepare", "testvale", sid, "--select", draft["suggestions"][0]["id"],
        "--draft", draft["draft_id"]])
    assert rc == 0
    pending = proposals.find_pending("testvale")
    assert pending and pending["kind"] == "session_changes"
    # the confirm-spend gate: extract refuses without it
    rc = session_notes.main(["extract", "testvale", sid])
    assert rc == 2
    # CLI accept = the HTTP accept service path
    rc = session_notes.main(["accept", "testvale", pending["proposal_id"]])
    assert rc == 0
    state = _state(tmp_spine_root)
    wardens = next(f for f in state["factions"] if f["id"] == "wardens")
    assert wardens["resources"]["coin"] == "none"
    rc = session_notes.main(["discard", "testvale", pending["proposal_id"]])
    assert rc == 1  # already accepted — discard refuses


def test_decision_record_and_receipts_survive_inspection(client,
                                                         tmp_spine_root):
    s = _mk_session(client)
    sid = s["session_id"]
    draft = _manual_draft(client, tmp_spine_root, sid, [_wardens_resource_sug()])
    r = _prepare(client, sid, draft["draft_id"],
                 [draft["suggestions"][0]["id"]])
    pid = r.json()["proposal_id"]
    assert client.post(f"{C}/simulation/proposal/{pid}/accept").status_code \
        == 200
    # the decision record is canon-adjacent DM material in the campaign
    # export tree (sessions/ is exported with the private campaign)
    dec_path = (tmp_spine_root / "testvale" / "sessions" / sid / "decisions" /
                f"{pid}.yaml")
    assert dec_path.is_file()
    dec = yaml.safe_load(dec_path.read_text("utf-8"))
    assert dec["session_id"] == sid and dec["decided"]
