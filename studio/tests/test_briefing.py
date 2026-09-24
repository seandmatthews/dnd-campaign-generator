"""Phase 6: DM briefing, player disclosure, and bounded review workload.

All deterministic — no LLM pass anywhere. The briefing uses the requested
range, caps its display with an honest count, and never presents pending
proposals as canon. The player handout contains ONLY deliberately revealed
facts: an empty disclosure set is an empty handout with an explanation, and
a secret that is DM-visible must be absent from the export entirely.
"""

import briefing
import knowledge
import spine
from conftest import client  # noqa: F401


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
    assert doc["shown"] <= briefing.BRIEFING_CAP
    assert doc["omitted"] == doc["total"] - doc["shown"]
    assert all(i["week"] > 3 for i in doc["items"] if i["kind"] == "event")
    # the markdown renderer says so too — never a silent truncation
    assert "more not shown" in doc["markdown"] if doc["omitted"] else True


def test_briefing_labels_pending_proposals_as_proposed(
        client, tmp_spine_root, monkeypatch):
    import engines
    from conftest import wait_for_job

    def fake_tick(engine_root, script, *args, timeout=600, on_line=None):
        world = engine_root / "world"
        world.mkdir(parents=True, exist_ok=True)
        world.joinpath("state.yaml").write_text(
            "meta:\n  clock:\n    week: 4\n", encoding="utf-8")
        (engine_root / "chronicle").mkdir(exist_ok=True)
        (engine_root / "chronicle" / "0004-proposed.md").write_text(
            "# Week 4 — Proposed\n\nNot canon.\n", encoding="utf-8")
        return 0, ["week 4 written"]

    monkeypatch.setattr(engines, "run_py", fake_tick)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
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
