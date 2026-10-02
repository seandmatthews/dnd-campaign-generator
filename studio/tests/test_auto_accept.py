"""Ticket 02: auto-accept — the opt-in that lets a staged bundle publish
itself through the SAME approval path a human would use.

- lore-entries / lore-missing-pages: the job approves its own pending file
  (same publication transactions, receipts, snapshots; recorded in the
  transaction as auto: true) — unless an error-level verification finding
  blocks it and the bundle stays pending, as today.
- goal-review: the staged goal_updates proposal is accepted by the job
  itself, same gate.
- sim-week: ONLY behind an explicit strict-bool checkbox, default off.
- session_effects / project_changes: no flag at all — DM rulings are
  never auto-accepted (they are synchronous routes; nothing to opt into).

The flows run with fake engines/LLMs (a real pass costs API credit and
belongs to the human user), exactly like the neighboring flow tests.
"""

import json

import yaml

import engines
import job_history
import loregen
import proposals
import publication
from conftest import wait_for_job


def _receipts(camp: str):
    """Every receipt in the campaign's publication record (the durable
    half — a completed transaction's payload is pruned once the git
    snapshot has it, but the receipt answers retries forever)."""
    root = (publication.spine.campaign_dir(camp)
            / publication.TX_DIR_NAME / "receipts")
    out = []
    if root.is_dir():
        for p in sorted(root.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return out

PENDING_DOC = {
    "entries": [{
        "id": "story-1",
        "when": "week 9",
        "title": "The Vane Rerouted",
        "text": "Engineers rerouted the Vane around the broken toll.",
        "source": "gen:test",
    }],
    "created": "2026-10-01T00:00:00+00:00",
}


def _fake_generate_entries(pending_doc, findings):
    """The loregen stand-in (both entry shapes): stage a REAL pending file
    so the auto-accept's approve path reads real bytes."""

    def fake(campaign_dir, focus="", log=None, limit=None):
        pending = campaign_dir / "lore" / "pending-2.yaml"
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(yaml.safe_dump(pending_doc, allow_unicode=True),
                           encoding="utf-8")
        return {"pending": "lore/pending-2.yaml", "entries": 1,
                "pages": 1, "findings": list(findings)}

    return fake


def _timeline_texts(tmp_spine_root):
    path = tmp_spine_root / "testvale" / "lore" / "timeline.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = doc if isinstance(doc, list) else doc["entries"]
    return [e["text"] for e in entries]


# ------------------------------------------------------------------- lore

def test_lore_entries_auto_accept_publishes_and_records_auto(
        client, tmp_spine_root, monkeypatch):
    (tmp_spine_root / "testvale" / "world" / "geography.yaml").write_text(
        "nations: []\n", encoding="utf-8")
    monkeypatch.setattr(loregen, "generate_entries",
                        _fake_generate_entries(PENDING_DOC, []))
    r = client.post("/api/campaign/testvale/lore/entries",
                    json={"auto_accept": True})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["auto_accept"] == "approved"
    assert result["approval"]["approved"] == 1
    assert result["approval"]["auto"] is True

    # the SAME approve path ran: the timeline gained the entry, the pending
    # file is gone (approved fully)
    assert any("rerouted the Vane" in t
               for t in _timeline_texts(tmp_spine_root))
    assert not (tmp_spine_root / "testvale" / "lore" / "pending-2.yaml"
                ).exists()

    # the publication record identifies the auto-approval, in the two
    # places that survive the post-snapshot prune: the receipt (the durable
    # answer) and the git snapshot message
    assert any(r.get("auto") is True for r in _receipts("testvale")), \
        _receipts("testvale")
    assert "(auto-accepted)" in result["approval"]["snapshot"]["detail"]
    txs = publication.transactions("testvale")
    assert all(t["auto"] is False for t in txs), \
        "manual transactions are not marked auto"


def test_lore_entries_error_finding_blocks_auto_accept(
        client, tmp_spine_root, monkeypatch):
    """Advisory findings ride along as always; ONE at error level leaves
    the bundle pending — auto-accept refuses to publish it."""
    (tmp_spine_root / "testvale" / "world" / "geography.yaml").write_text(
        "nations: []\n", encoding="utf-8")
    blocked = {"file": "lore/pending-2.yaml", "line": 3, "rule": "dup",
               "level": "error", "quote": "x", "note": "duplicate variant"}
    advisory = {"file": "lore/pending-2.yaml", "line": 3, "rule": "length",
                "level": "warning", "quote": "x", "note": "35+ words"}
    monkeypatch.setattr(
        loregen, "generate_entries",
        _fake_generate_entries(PENDING_DOC, [advisory, blocked]))
    r = client.post("/api/campaign/testvale/lore/entries",
                    json={"auto_accept": True})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["auto_accept"] == "blocked"
    assert result["blocking_findings"] == [blocked]

    # the bundle is pending, exactly as an un-auto-accepted run leaves it
    assert (tmp_spine_root / "testvale" / "lore" / "pending-2.yaml").exists()
    texts = _timeline_texts(tmp_spine_root)
    assert not any("rerouted the Vane" in t for t in texts)
    # no publication happened at all: nothing carries the auto marker
    assert not any(r.get("auto") for r in _receipts("testvale"))
    assert not any(t["auto"] for t in publication.transactions("testvale"))

    # a human can still approve the blocked bundle by hand (the normal path)
    r = client.post("/api/campaign/testvale/review/approve",
                    json={"file": "lore/pending-2.yaml", "all": True})
    assert r.status_code == 200, r.text
    assert r.json().get("auto") is None, "a manual approval is not auto"


def test_lore_missing_pages_auto_accept(tmp_spine_root, client, monkeypatch):
    page_doc = {"entries": [dict(PENDING_DOC["entries"][0], page={
        "kind": "place", "aliases": []}), ],
        "created": PENDING_DOC["created"], "mode": "missing-pages"}
    monkeypatch.setattr(loregen, "generate_missing_pages",
                        _fake_generate_entries(page_doc, []))
    r = client.post("/api/campaign/testvale/lore/pages/missing",
                    json={"auto_accept": True})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    assert job["result"]["auto_accept"] == "approved"
    # the page draft became a real entity page through the approve path
    # (display-name page stems, per the vault design)
    assert (tmp_spine_root / "testvale" / "lore" / "entities" /
            "The Vane Rerouted.md").is_file()
    assert any(r.get("auto") is True for r in _receipts("testvale"))
    assert "(auto-accepted)" in job["result"]["approval"]["snapshot"]["detail"]


def test_lore_auto_accept_defaults_off(client, tmp_spine_root, monkeypatch):
    """Without the flag the staging behaves exactly as before: pending,
    awaiting review."""
    (tmp_spine_root / "testvale" / "world" / "geography.yaml").write_text(
        "nations: []\n", encoding="utf-8")
    monkeypatch.setattr(loregen, "generate_entries",
                        _fake_generate_entries(PENDING_DOC, []))
    r = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done"
    assert "auto_accept" not in job["result"]
    assert (tmp_spine_root / "testvale" / "lore" / "pending-2.yaml").exists()


# ------------------------------------------------------------- goal review

def _fake_goal_engine(updates_doc):
    """Dispatch on script: goals.py writes its updates; effects.py applies
    (same shape as the real engine — see test_goal_review.py)."""
    import pathlib

    def run(engine_root_, script, *args, timeout=600, on_line=None):
        if script == "src/goals.py":
            out = pathlib.Path(args[args.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(yaml.safe_dump(updates_doc, allow_unicode=True),
                           encoding="utf-8")
            return 0, ["[goals] proposed"]
        if script == "src/effects.py":
            mode, record_path, state_path = \
                args[0], pathlib.Path(args[1]), pathlib.Path(args[3])
            if mode == "--check":
                return 0, ["effects valid"]
            rec = yaml.safe_load(record_path.read_text(encoding="utf-8"))
            st = yaml.safe_load(state_path.read_text(encoding="utf-8"))
            for eff in rec["effects"]:
                faction = next(f for f in st["factions"]
                               if f["id"] == eff["faction_id"])
                faction[eff["field"]] = eff["after"]
            state_path.write_text(
                yaml.safe_dump(st, allow_unicode=True), encoding="utf-8")
            return 0, ["applied"]
        raise AssertionError(f"unexpected engine script: {script}")

    return run


UPDATES = {"updates": [{"faction_id": "wardens", "name": "The Bridge Wardens",
                        "goal": "hold the bridge until the regency expires",
                        "reason": "week 3: the regency ends",
                        "current_goal": "rebuild the bridge"}],
           "findings": []}


def test_goal_review_auto_accept_publishes_the_updates(
        client, tmp_spine_root, monkeypatch):
    monkeypatch.setattr(engines, "run_py", _fake_goal_engine(UPDATES))
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/goal-review",
                    json={"auto_accept": True})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["auto_accept"] == "approved"
    assert result["accept_result"]["goals_changed"] == 1
    assert result["accept_result"]["auto"] is True

    # the goal landed; no proposal is left pending
    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    wardens = next(f for f in state["factions"] if f["id"] == "wardens")
    assert wardens["goal"] == "hold the bridge until the regency expires"
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] \
        is None
    assert any(r.get("auto") is True for r in _receipts("testvale"))
    assert "(auto-accepted)" in result["accept_result"]["snapshot"]["detail"]


def test_goal_review_auto_accept_blocked_by_error_finding(
        client, tmp_spine_root, monkeypatch):
    """A dict finding at error level in the staged bundle blocks the
    auto-accept: the proposal stays pending for a human decision."""
    blocked = dict(UPDATES, findings=[
        {"rule": "goal/duplicate", "level": "error", "line": 4,
         "note": "duplicate of an applied effect"}])
    monkeypatch.setattr(engines, "run_py", _fake_goal_engine(blocked))
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/goal-review",
                    json={"auto_accept": True})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["auto_accept"] == "blocked"
    assert result["blocking_findings"]
    # pending for review, live state untouched
    pending = proposals.find_pending("testvale")
    assert pending and pending["kind"] == "goal_updates"
    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    wardens = next(f for f in state["factions"] if f["id"] == "wardens")
    assert wardens["goal"] == "rebuild the bridge and the toll with it"
    # the DM's own accept still works on the blocked proposal
    r = client.post(f"/api/campaign/testvale/simulation/proposal/"
                    f"{pending['proposal_id']}/accept")
    assert r.status_code == 200 and r.json().get("auto") is None


# ---------------------------------------------------------------- the week

def _fake_tick(week=4):
    def tick(engine_root_, script, *args, timeout=600, on_line=None):
        assert (script,) + args == ("src/tick.py", "--live")
        world = engine_root_ / "world"
        world.mkdir(parents=True, exist_ok=True)
        world.joinpath("state.yaml").write_text(yaml.safe_dump({
            "meta": {"clock": {"week": week, "season": "late autumn"},
                     "rng_seeds": {str(week): 123456789}},
            "events": [],
        }), encoding="utf-8")
        chron = engine_root_ / "chronicle"
        chron.mkdir(exist_ok=True)
        (chron / f"{week:04d}-the-gate.md").write_text(
            f"# Week {week} — The Gate\n\nProse.\n", encoding="utf-8")
        return 0, [f"week {week} written"]
    return tick


def test_week_auto_accept_publishes_without_review(
        client, tmp_spine_root, monkeypatch):
    """The loud checkbox, ON: the staged week publishes itself through the
    normal accept path, and the publication says auto."""
    monkeypatch.setattr(engines, "run_py", _fake_tick())
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True, "auto_accept": True})
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"], timeout=30.0)
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["auto_accept"] == "approved"
    assert result["accept_result"]["week"] == 4
    assert result["accept_result"]["auto"] is True

    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert state["meta"]["clock"]["week"] == 4, "the week published"
    assert client.get(
        "/api/campaign/testvale/simulation/proposal").json()["proposal"] \
        is None
    week_txs = [t for t in publication.transactions("testvale")
                if "week" in t["operation"]]
    assert all(t["auto"] is False for t in week_txs), \
        "a manual week accept is not auto"
    assert any(r.get("auto") is True for r in _receipts("testvale"))
    assert "(auto-accepted)" in result["accept_result"]["snapshot"]["detail"]


def test_week_auto_accept_defaults_off(client, tmp_spine_root, monkeypatch):
    """Default OFF — reading the chronicle is the point of the feature."""
    monkeypatch.setattr(engines, "run_py", _fake_tick())
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"], timeout=30.0)
    assert job["status"] == "done", job["error"]
    assert "auto_accept" not in job["result"]
    pending = proposals.find_pending("testvale")
    assert pending, "the week stays pending for review"
    state = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" / "state.yaml")
        .read_text(encoding="utf-8"))
    assert state["meta"]["clock"]["week"] == 3, "nothing published"


def test_week_auto_accept_must_be_a_real_bool(client, tmp_spine_root):
    """StrictBool: a string "true" must not pass the loud-checkbox gate —
    only an explicit JSON true/false (default false) is accepted."""
    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True, "auto_accept": "true"})
    assert r.status_code == 422, r.text


# ------------------------------------------------------------- job history

def test_finished_jobs_feed_the_history(tmp_spine_root, client, monkeypatch):
    """The duration recording rides on submit_campaign_job: a finished
    lore job lands in the campaign's rolling history (the store the busy
    409s quote)."""
    (tmp_spine_root / "testvale" / "world" / "geography.yaml").write_text(
        "nations: []\n", encoding="utf-8")
    monkeypatch.setattr(loregen, "generate_entries",
                        _fake_generate_entries(PENDING_DOC, []))
    r = client.post("/api/campaign/testvale/lore/entries", json={})
    assert r.status_code == 202
    assert wait_for_job(client, r.json()["job_id"])["status"] == "done"
    est = job_history.estimate("testvale", "lore-entries")
    assert est is not None and est["samples"] == 1
