"""M3 staging: a successful week mechanically stages
simulation/pending-week-N.yaml — one proposed timeline entry per
NON-WORLD event, source sim:week-N, next-week numbering, no LLM.

Unit tests run the functions directly; the integration test runs the week
job with a fake tick (fixture events only — a real week costs API credit
and belongs to the human user)."""

import yaml

import engines
import spine
from conftest import wait_for_job
from routers.simulation import propose_title, stage_pending_week

WEEK4_EVENTS = [
    {"week": 4, "actor": "bastard",
     "text": "Petition the Reliquary to convene a formal examination — "
             "succeeded (contested vs mint), margin +12"},
    {"week": 4, "actor": "world",
     "text": "The season turned; the harbor ices closed two days"},
    {"week": 4, "actor": "mint",
     "text": "Open the Mint's coffers to the Longshoremen — failed "
             "(contested vs longshoremen), margin +0"},
    {"week": 3, "actor": "longshoremen",
     "text": "A week-3 event must not leak into week 4's pending file"},
]


# ------------------------------------------------------------- title rule

def test_propose_title_cuts_at_punctuation_after_four_words():
    # the parenthetical starts word 5: cut before it
    assert propose_title(
        "burned the toll bridge (contested vs wardens) — succeeded, margin +6"
    ) == "burned the toll bridge"


def test_propose_title_keeps_seven_words_without_clause_punctuation():
    # the em-dash is word 8, beyond the 7-word window: no cut
    assert propose_title(
        "raised the toll on the Vane crossing — succeeded, margin +3"
    ) == "raised the toll on the Vane crossing"


def test_propose_title_floor_and_possessives():
    # punctuation before the 4-word floor does not cut; the first clause
    # mark at word 4 (0-based) or later does
    assert propose_title("A, b, c, d — something more") == "A, b, c, d"
    assert propose_title("Run, hide, survive the winter and beyond") == \
        "Run, hide, survive the winter and beyond"
    # possessive apostrophes are not clause punctuation
    assert propose_title("The Mint's coffers stayed shut") == \
        "The Mint's coffers stayed shut"


# ------------------------------------------------------------ staging unit

def test_stage_pending_week_one_entry_per_non_world_event(tmp_spine_root):
    doc = stage_pending_week("testvale", 4, WEEK4_EVENTS)
    assert doc["week"] == 4
    assert doc["created"]
    assert len(doc["entries"]) == 2  # the world event and week-3 are out

    first, second = doc["entries"]
    assert first["id"] == "pw4-1" and second["id"] == "pw4-2"
    for e in (first, second):
        assert e["when"] == "week 4"
        assert e["source"] == "sim:week-4"
        assert e["text"].strip() == e["text"] and e["text"]

    assert first["title"] == "Petition the Reliquary to convene a formal"
    assert second["title"] == "Open the Mint's coffers to the Longshoremen"

    # the pending file is on disk, parseable, and counted as review pending
    path = tmp_spine_root / "testvale" / "simulation" / "pending-week-4.yaml"
    assert path.is_file()
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk == doc
    assert spine.count_pending("testvale") >= 1


def test_stage_pending_week_empty_week_writes_empty_entries(tmp_spine_root):
    doc = stage_pending_week("testvale", 9, [])
    assert doc == {"created": doc["created"], "week": 9, "entries": []}
    path = tmp_spine_root / "testvale" / "simulation" / "pending-week-9.yaml"
    assert path.is_file()


# ------------------------------------------------------------- integration

def test_week_job_stages_pending_week_after_success(client, tmp_spine_root,
                                                    monkeypatch):
    def fake_tick(engine_root, script, *args, timeout=600, on_line=None):
        assert (script,) + args == ("src/tick.py", "--live")
        world = engine_root / "world"
        world.mkdir(parents=True, exist_ok=True)
        # the engine advanced the clock to week 4 and recorded its events
        world.joinpath("state.yaml").write_text(yaml.safe_dump({
            "meta": {"clock": {"week": 4, "season": "late autumn"}},
            "events": WEEK4_EVENTS,
        }), encoding="utf-8")
        chron = engine_root / "chronicle"
        chron.mkdir(exist_ok=True)
        (chron / "0004-the-gate.md").write_text(
            "# Week 4 — The Gate\n\nProse.\n\nHeld (scheduler): reliquary\n",
            encoding="utf-8")
        return 0, ["week 4 written"]

    monkeypatch.setattr(engines, "run_py", fake_tick)
    monkeypatch.setattr(engines, "AGENTIC_SIMULATOR",
                        tmp_spine_root / "engine-home")

    r = client.post("/api/campaign/testvale/simulation/week",
                    json={"confirm": True})
    assert r.status_code == 202
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]

    # held is still in the result (now alongside the staging fields)
    assert job["result"]["held"] == ["reliquary"]
    assert job["result"]["week"] == 4
    assert job["result"]["pending"] == "simulation/pending-week-4.yaml"
    assert job["result"]["pending_entries"] == 2

    pending = yaml.safe_load(
        (tmp_spine_root / "testvale" / "simulation" /
         "pending-week-4.yaml").read_text(encoding="utf-8"))
    assert pending["week"] == 4
    assert [e["id"] for e in pending["entries"]] == ["pw4-1", "pw4-2"]
    assert all(e["source"] == "sim:week-4" for e in pending["entries"])
