"""The integrated completion scenario (the handoff's completion gate).

A temporary campaign — the testvale fixture: two factions (the Bridge
Wardens, the Ferrymen), a bridge, a treasury — plus one public fact and
one secret known only to the wardens. Every engine/model response is fake;
no campaign data outside the temp tree is touched.

1.  record a session event with a reviewed treasury resource effect; accept once
2.  live state, history, and faction views agree
3.  generate a week on the updated resources and correctly filtered facts
4.  live state is byte-identical while the week is pending
5.  an external edit makes approval conflict without overwriting
6.  discard the stale proposal; generate a fresh one from the new base
7.  inject a publication failure, "restart" (recover), no duplicates,
    no mixed canonical state
8.  a repeated approval returns the original result
9.  build the DM briefing; reveal one fact to the party
10. the player handout excludes the secret and private metadata
11. export + restore the campaign into a fresh directory and verify it
"""

import json
import shutil
import unittest.mock as mock
import zipfile

import engines
import publication
import proposals
import spine
import yaml
from conftest import wait_for_job
from export_campaign import main as export_main

TREASURY_TEXT = "The party smashed open the wardens' treasury at midnight."


def _facts(client, tmp_spine_root):
    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml").read_text(
            encoding="utf-8"))
    wardens = next(f for f in state["factions"] if f["id"] == "wardens")
    coin = wardens["resources"]["coin"]
    events = [e for e in state["events"] if e.get("text") == TREASURY_TEXT]
    return coin, events


def _campaign_bytes(tmp_spine_root):
    """Canon bytes only: the campaign's live files — not the scratch engine
    home, recovery payloads, proposal staging, or git internals."""
    skip = {"engine-home", ".studio-transactions", ".git", "proposals"}
    return {p.relative_to(tmp_spine_root).as_posix(): p.read_bytes()
            for p in sorted(tmp_spine_root.rglob("*")) if p.is_file()
            and not (skip & set(p.parts))}


def _fake_tick(state_text, chronicle_name, log_lines):
    def tick(engine_root, script, *args, timeout=600, on_line=None, **kw):
        world = engine_root / "world"
        world.mkdir(parents=True, exist_ok=True)
        # capture what the engine actually received (copy-in)
        state_text["received"] = (world / "state.yaml").read_text(
            encoding="utf-8")
        state_text["counter"] = state_text.get("counter", 0) + 1
        n = state_text["counter"]
        # a real tick loads the copy-in state and ADVANCES it — events,
        # factions, and the knowledge section all carry through
        state = yaml.safe_load(state_text["received"])
        state["meta"]["clock"]["week"] = 4
        state["meta"].setdefault("rng_seeds", {})["4"] = 424242
        state.setdefault("events", []).append(
            {"week": 4, "actor": "ferrymen",
             "text": "ran the blockade at dawn — failed (contested vs "
                     "wardens), margin -3"})
        world.joinpath("state.yaml").write_text(
            yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
        chron = engine_root / "chronicle"
        chron.mkdir(exist_ok=True)
        name = chronicle_name if n == 1 else \
            chronicle_name.replace(".md", "-2.md")
        (chron / name).write_text(
            "# Week 4 — The Broken Toll\n\nHeld (scheduler): ferrymen\n",
            encoding="utf-8")
        if log_lines is not None:
            log_lines.append(f"week 4 written ({name})")
        return 0, ["wardens       HOLDS (quiet week)", "week 4 written"]
    return tick


def test_integrated_completion_scenario(client, tmp_spine_root, monkeypatch,
                                        tmp_path):
    # ---- setup: the public fact and the faction A secret -----------------
    r = client.post("/api/campaign/testvale/knowledge", json={
        "text": "The Vane crossing tolls doubled this season.",
        "visibility": "public"})
    assert r.status_code == 200, r.text
    public_fact = r.json()["fact"]
    r = client.post("/api/campaign/testvale/knowledge", json={
        "text": "SECRET: the wardens hoard the doubled tolls in a vault.",
        "visibility": ["wardens"]})
    assert r.status_code == 200, r.text
    secret = r.json()["fact"]

    # ---- 1. session event + reviewed treasury effect, accepted once ------
    r = client.post("/api/campaign/testvale/simulation/session", json={
        "source": "session:session-01", "week": 3,
        "events": [{"actor": "wardens", "text": TREASURY_TEXT}],
        "effects": [{"type": "set_resource", "faction_id": "wardens",
                     "resource": "coin", "expected_before": "medium",
                     "after": "none",
                     "reason": TREASURY_TEXT}]})
    assert r.status_code == 202, r.text
    pid_session = r.json()["proposal_id"]
    r = client.post(f"/api/campaign/testvale/simulation/proposal/"
                    f"{pid_session}/accept")
    assert r.status_code == 200, r.text

    # ---- 2. live state, history, and faction views agree -----------------
    coin, treasury_events = _facts(client, tmp_spine_root)
    assert coin == "none"                      # the treasury is gone
    assert len(treasury_events) == 1           # the sourced event, once
    assert treasury_events[0].get("source") == "session:session-01"
    home = tmp_spine_root / "engine-home"
    (home / "world").mkdir(parents=True, exist_ok=True)
    shutil.copytree(engines.AGENTIC_SIMULATOR / "src", home / "src")
    orig_home = engines.AGENTIC_SIMULATOR
    engines.AGENTIC_SIMULATOR = home
    try:
        r = client.get("/api/campaign/testvale/simulation/views/wardens")
        assert r.status_code == 200, r.text
        wardens_view = r.json()
        r = client.get("/api/campaign/testvale/simulation/views/ferrymen")
        assert r.status_code == 200, r.text
        ferrymen_view = r.json()
    finally:
        engines.AGENTIC_SIMULATOR = orig_home
    assert wardens_view["you"]["resources"]["coin"] == "none"  # new world
    wardens_text = str(wardens_view)
    ferrymen_text = str(ferrymen_view)
    assert "tolls doubled this season" in wardens_text      # public fact
    assert "tolls doubled this season" in ferrymen_text     # ...for everyone
    assert "hoard the doubled tolls" not in ferrymen_text   # the secret holds
    assert "hoard the doubled tolls" in wardens_text        # ...held by A

    # ---- 3. generate a week on the updated state + filtered facts --------
    captured = {}
    monkeypatch.setattr(engines, "run_py",
                        _fake_tick(captured, "0004-the-broken-toll.md", None))
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR", home)
    received = yaml.safe_load(captured.get("received") or "{}") \
        if captured.get("received") else None
    before = _campaign_bytes(tmp_spine_root)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    received = yaml.safe_load(captured["received"])
    # the engine ran on the UPDATED resources and the COMPILED knowledge
    wardens_in = next(f for f in received["factions"]
                      if f["id"] == "wardens")
    assert wardens_in["resources"]["coin"] == "none"
    knowledge_in = received["knowledge"]
    assert any(f["text"].startswith("The Vane crossing") for f in knowledge_in)
    secret_in = next(f for f in knowledge_in
                     if f["text"].startswith("SECRET"))
    assert secret_in["known_to"] == ["wardens"]
    assert all(f.get("visibility") != "dm_only" for f in knowledge_in)

    # ---- 4. live state is byte-identical while the week is pending -------
    assert _campaign_bytes(tmp_spine_root) == before
    pid_week1 = job["result"]["proposal_id"]
    assert proposals.load("testvale", pid_week1)["manifest"]["status"] == \
        "pending"

    # ---- 5. an external edit conflicts, nothing overwritten --------------
    timeline = tmp_spine_root / "testvale" / "lore" / "timeline.yaml"
    timeline.write_bytes(timeline.read_bytes() + b"# Obsidian edit\n")
    r = client.post(f"/api/campaign/testvale/simulation/proposal/"
                    f"{pid_week1}/accept")
    assert r.status_code == 409 and "stale proposal" in r.json()["error"]
    # the external edit stands untouched and no week landed
    assert b"# Obsidian edit" in timeline.read_bytes()
    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert state["meta"]["clock"]["week"] == 3

    # ---- 6. discard the stale proposal; regenerate from the new base -----
    r = client.post(f"/api/campaign/testvale/simulation/proposal/"
                    f"{pid_week1}/discard")
    assert r.status_code == 200
    # the fake engine keeps writing the next free chronicle (counter runs on)
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    pid_week2 = job["result"]["proposal_id"]
    assert pid_week2 != pid_week1

    # ---- 7. publication failure injected; restart recovers cleanly -------
    real_apply = publication._apply_text
    calls = {"n": 0}

    def crash_second(target, text):
        calls["n"] += 1
        if calls["n"] == 2:  # state.yaml landed, timeline write crashes
            raise RuntimeError("injected crash")
        return real_apply(target, text)

    with mock.patch.object(publication, "_apply_text", crash_second):
        r = client.post(f"/api/campaign/testvale/simulation/proposal/"
                        f"{pid_week2}/accept")
    assert r.status_code == 500, r.text  # surfaced, not swallowed
    # "restart": boot recovery rolls the open transaction forward
    out = publication.recover_campaign("testvale")
    assert out["recovered"] and not out["conflicts"]
    # no duplicate effects, no mixed canonical state
    coin, treasury_events = _facts(client, tmp_spine_root)
    assert coin == "none"
    assert len(treasury_events) == 1
    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml").read_text(
            encoding="utf-8"))
    assert state["meta"]["clock"]["week"] == 4  # exactly one week advanced
    timeline_now = yaml.safe_load(timeline.read_text(encoding="utf-8"))
    entries = timeline_now if isinstance(timeline_now, list) \
        else timeline_now["entries"]
    assert any(e.get("source") == "sim:week-4" for e in entries)
    assert any(p.name.startswith("0004-the-broken-toll")
               for p in (tmp_spine_root / "testvale" / "simulation" /
                         "chronicle").glob("*.md"))

    # ---- 8. the repeated approval returns the original result ------------
    r = client.post(f"/api/campaign/testvale/simulation/proposal/"
                    f"{pid_week2}/accept")
    assert r.status_code == 200 and r.json().get("idempotent") is True
    assert yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))["meta"]["clock"]["week"] == 4

    # ---- 9. DM briefing + explicit reveal to the party -------------------
    r = client.get("/api/campaign/testvale/briefing?since_week=3")
    assert r.status_code == 200
    doc = r.json()
    assert any(i["kind"] == "event" and i["week"] == 4 for i in doc["items"])
    assert "NOT CANON" not in doc["markdown"].upper() or doc["items"]
    r = client.post(f"/api/campaign/testvale/knowledge/"
                    f"{public_fact['id']}/reveal", json={"week": 4})
    assert r.status_code == 200, r.text

    # ---- 10. the handout excludes the secret and private metadata --------
    r = client.get("/api/campaign/testvale/disclosure/handout")
    handout = r.text
    assert "The Vane crossing tolls doubled this season." in handout
    assert "SECRET" not in handout
    assert secret["id"] not in handout
    assert "hoard" not in handout
    assert "knowledge.yaml" not in handout

    # ---- 11. export + restore into a fresh directory, verify -------------
    archive = tmp_path / "testvale-export.zip"
    assert export_main(["testvale", "--out", str(archive)]) == 0
    restored_parent = tmp_path / "restored"
    assert export_main(["--restore", str(archive), "--into",
                        str(restored_parent)]) == 0
    restored = restored_parent / "testvale"
    rs = yaml.safe_load(
        (restored / "simulation" / "state.yaml").read_text(encoding="utf-8"))
    assert rs["meta"]["clock"]["week"] == 4
    rward = next(f for f in rs["factions"] if f["id"] == "wardens")
    assert rward["resources"]["coin"] == "none"      # the treasury stays gone
    assert any(f["id"] == public_fact["id"] for f in rs.get("knowledge", []))
    assert any(e.get("text") == TREASURY_TEXT for e in rs["events"])
    tl = yaml.safe_load(
        (restored / "lore" / "timeline.yaml").read_text(encoding="utf-8"))
    tl_entries = tl if isinstance(tl, list) else tl["entries"]
    assert any(e.get("source") == "sim:week-4" for e in tl_entries)
    assert (restored / "simulation" / "party-knowledge.yaml").is_file()
