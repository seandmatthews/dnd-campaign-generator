"""Ticket 17: the engine-copy seam must be byte-exact (no CRLF doubling).

state.yaml is CRLF on disk on Windows (every studio text writer used text
mode; a Notepad hand edit does the same). The staging flows hand the
engine a TEMP COPY of that state: any re-translation turns \\r\\n into
\\r\\r\\n, and YAML folds ONE break inside a plain scalar to a space but TWO
to a literal newline — so every dumper-wrapped value (width 100: premises,
long goals, event texts) silently mutates in what the engine parses, and
accept would publish the corruption into canon.

Each affected flow runs end to end against a faked engine apply; the fake
reads the copy exactly the way the real engine does (store.load_state:
text-mode read + yaml.safe_load, universal newlines included) and records
the parse BEFORE mutating — the test asserts that parse EQUALS the canon
parse. A doubled \\r\\r\\n reads back as \\n\\n and folds the wrapped scalars
to different values, which is precisely the bug.
"""

import copy
from pathlib import Path

import yaml

import engines
import spine
import sessions as session_store
from conftest import wait_for_job

C = "/api/campaign/testvale"

# long enough to wrap as a plain scalar at the dumper's width=100 — the
# shape whose folding a \r\r\n corrupts
PREMISE = ("The toll bridge burned the night the wardens raised the toll, "
           "and the ferrymen watched the smoke drift over Provis while the "
           "regency council argued about the ferry concession.")
LONG_GOAL = ("rebuild the bridge and the toll with it before the regency "
             "expires at week 8, whatever the ferrymen try")
LONG_EVENT = ("raised the toll on the Vane crossing and doubled the night "
              "rate — succeeded, margin +3, and the ferrymen swore to "
              "answer the increase in kind")

FERRY = {
    "faction_id": "wardens",
    "title": "Buy the ferry concession",
    "objective": "Secure the ferry concession before the spring auctions.",
    "target": 4,
    "requirements": {"coin": "low"},
    "completion": {
        "text": "The Wardens acquired the ferry concession.",
        "effects": [
            {"type": "set_resource", "faction_id": "wardens",
             "resource": "legitimacy", "expected_before": "high",
             "after": "medium", "reason": "Recognized standing."}],
    },
}


def _state_path(tmp_spine_root):
    return tmp_spine_root / "testvale" / "simulation" / "state.yaml"


def _crlf_state(tmp_spine_root):
    """Rewrite testvale's state as the studio dumper shapes it (width 100,
    wrapped plain scalars) but with CRLF on disk — the Windows shape. All
    the long values must actually wrap, or the test guards nothing."""
    path = _state_path(tmp_spine_root)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["meta"]["premise"] = PREMISE
    doc["factions"][0]["goal"] = LONG_GOAL
    doc["events"][0]["text"] = LONG_EVENT
    text = spine.dump_yaml(doc)
    for value in (PREMISE, LONG_GOAL, LONG_EVENT):
        # the value spans a line break in the dump (wrapped plain scalar)
        assert value not in text, \
            "the long values must wrap at width=100, not sit on one line"
    # one break folds back to a space — the dump round-trips
    assert yaml.safe_load(text)["meta"]["premise"] == PREMISE
    path.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n"))
    raw = path.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), \
        "the state on disk must be CRLF throughout"
    canon = yaml.safe_load(raw)
    assert canon["meta"]["premise"] == PREMISE  # CRLF alone folds cleanly
    return canon


def _recording_run_py(parsed):
    """A run_py stand-in that READS the state copy exactly the way the real
    engine does (store.load_state: text-mode open + yaml.safe_load) and
    records a snapshot of the parse BEFORE any mutation — the values the
    engine would validate against and re-dump."""

    def run(engine_root, script, *args, timeout=600, on_line=None):
        if script == "src/effects.py":
            mode, record_path, state_path = \
                args[0], Path(args[1]), Path(args[3])
            st = yaml.safe_load(state_path.read_text(encoding="utf-8"))
            parsed.append(copy.deepcopy(st))
            if mode == "--check":
                return 0, ["[effects] check ok"]
            rec = yaml.safe_load(record_path.read_text(encoding="utf-8"))
            week = rec.get("week")
            for ev in rec.get("events") or []:
                st.setdefault("events", []).append({
                    "week": ev.get("week") if ev.get("week") is not None
                    else week,
                    "actor": ev.get("actor") or "world",
                    "text": ev.get("text"), "source": rec.get("source")})
            for eff in rec.get("effects") or []:
                faction = next(f for f in st["factions"]
                               if f["id"] == eff["faction_id"])
                if eff["type"] == "set_resource":
                    faction["resources"][eff["resource"]] = eff["after"]
                else:
                    faction[eff["field"]] = eff["after"]
                st.setdefault("events", []).append({
                    "week": eff.get("week") if eff.get("week") is not None
                    else week,
                    "actor": eff["faction_id"], "text": eff["reason"],
                    "effect": eff["id"], "source": eff["source"]})
                st.setdefault("meta", {}).setdefault(
                    "applied_effects", []).append(eff["id"])
            # the real save_state: text-mode write, ONE translation — valid
            # YAML (engine-side temp-name hygiene is ticket 26, not this)
            state_path.write_text(
                yaml.safe_dump(st, sort_keys=False, allow_unicode=True),
                encoding="utf-8")
            return 0, ["[effects] applied %d"
                       % len(rec.get("effects") or [])]
        if script == "src/goals.py":
            state_path = Path(args[args.index("--state") + 1])
            out = Path(args[args.index("--out") + 1])
            parsed.append(copy.deepcopy(
                yaml.safe_load(state_path.read_text(encoding="utf-8"))))
            out.write_text(yaml.safe_dump(
                {"updates": [
                    {"faction_id": "wardens",
                     "name": "The Bridge Wardens",
                     "goal": "hold the crossing open and collect both tolls",
                     "reason": "week 3: the treasury fire changed the "
                               "arithmetic",
                     "current_goal": LONG_GOAL}],
                 "findings": []},
                sort_keys=False, allow_unicode=True), encoding="utf-8")
            return 0, ["[goals] proposed 1 goal update(s)"]
        if script == "src/projects.py":
            state_path = Path(args[args.index("--state") + 1])
            out = Path(args[args.index("--out") + 1])
            st = yaml.safe_load(state_path.read_text(encoding="utf-8"))
            parsed.append(copy.deepcopy(st))
            changes = yaml.safe_load(Path(args[1]).read_text(
                encoding="utf-8"))
            ops = []
            for op in changes.get("operations") or []:
                if op["op"] == "create":
                    project = dict(op["project"])
                    project.setdefault("id", "project-0001")
                    project.setdefault("progress", 0)
                    project.setdefault("status", "active")
                    st.setdefault("projects", []).append(project)
                    ops.append({**op, "project_id": project["id"]})
            out.write_text(yaml.safe_dump(
                {"operations": ops, "findings": [], "state": st},
                sort_keys=False, allow_unicode=True), encoding="utf-8")
            return 0, ["[projects] preview ok"]
        raise AssertionError(f"unexpected engine script: {script}")

    return run


def _assert_engine_sees_canon(parsed, canon, count):
    assert len(parsed) == count, \
        f"expected {count} engine reads of the copy, got {len(parsed)}"
    for st in parsed:
        assert st == canon, \
            "the engine's parse of the copy differs from canon — a " \
            "wrapped scalar was folded differently (CRLF doubling)"


# ------------------------------------------------- the four staging flows


def test_session_effects_flow_copy_is_byte_exact(
        client, tmp_spine_root, monkeypatch):
    canon = _crlf_state(tmp_spine_root)
    parsed = []
    monkeypatch.setattr(engines, "run_py", _recording_run_py(parsed))
    r = client.post(f"{C}/simulation/session", json={
        "source": "session:session-08", "week": 7,
        "events": [{"actor": "wardens",
                    "text": "The party destroyed the treasury."}],
        "effects": [{"type": "set_resource", "faction_id": "wardens",
                     "resource": "coin", "expected_before": "medium",
                     "after": "none",
                     "reason": "The party destroyed the treasury."}]})
    assert r.status_code == 200, r.text
    _assert_engine_sees_canon(parsed, canon, count=2)  # --check + --apply

    # accept publishes the staged after-image — canon must show ONLY the
    # intended diff, never a re-folded scalar
    pid = r.json()["proposal_id"]
    r = client.post(f"{C}/simulation/proposal/{pid}/accept")
    assert r.status_code == 200, r.text
    after = yaml.safe_load(_state_path(tmp_spine_root).read_bytes())
    assert after["meta"]["premise"] == PREMISE  # no \n where a space was
    assert after["factions"][0]["goal"] == LONG_GOAL
    assert after["events"][0]["text"] == LONG_EVENT
    assert next(f for f in after["factions"]
                if f["id"] == "wardens")["resources"]["coin"] == "none"
    # the untouched faction is untouched
    assert next(f for f in after["factions"]
                if f["id"] == "ferrymen") == \
        next(f for f in canon["factions"] if f["id"] == "ferrymen")
    new = [e for e in after["events"] if e.get("week") == 7]
    assert len(new) == 2 and all(e.get("source") ==
                                 "session:session-08" for e in new)


def test_projects_prepare_flow_copy_is_byte_exact(
        client, tmp_spine_root, monkeypatch):
    canon = _crlf_state(tmp_spine_root)
    parsed = []
    monkeypatch.setattr(engines, "run_py", _recording_run_py(parsed))
    r = client.post(f"{C}/simulation/projects/prepare",
                    json={"operations": [{"op": "create", "reason": "setup",
                                          "project": FERRY}]})
    assert r.status_code == 200, r.text
    _assert_engine_sees_canon(parsed, canon, count=1)  # --preview


def test_goal_review_flow_copy_is_byte_exact(
        client, tmp_spine_root, monkeypatch):
    canon = _crlf_state(tmp_spine_root)
    parsed = []
    monkeypatch.setattr(engines, "run_py", _recording_run_py(parsed))
    monkeypatch.setattr(engines, "ENGINE_HOME",
                        tmp_spine_root / "engine-home")
    monkeypatch.setattr(engines, "CHRONICLE_HOME",
                        tmp_spine_root / "chronicle")
    r = client.post(f"{C}/simulation/goal-review")
    assert r.status_code == 202, r.text
    job = wait_for_job(client, r.json()["job_id"])
    assert job["status"] == "done", job["error"]
    # goals.py + effects --check + --apply each read the copy
    _assert_engine_sees_canon(parsed, canon, count=3)


def test_session_changes_flow_copy_is_byte_exact(
        client, tmp_spine_root, monkeypatch):
    canon = _crlf_state(tmp_spine_root)
    parsed = []
    monkeypatch.setattr(engines, "run_py", _recording_run_py(parsed))
    r = client.post(f"{C}/sessions", json={
        "title": "The treasury fire",
        "notes": "The party burned the toll treasury at the Vane crossing.",
        "event_week": 3})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    draft = session_store.save_draft(
        "testvale", sid, origin="manual", suggestions=[
            {"kind": "resource_change", "faction_id": "wardens",
             "resource": "coin", "expected_before": "medium",
             "after": "none",
             "reason": "The toll treasury burned at the Vane crossing.",
             "evidence": [{"notes_path": None, "line_start": 1,
                           "line_end": 1,
                           "quote": "burned the toll treasury"}],
             "confirmed": {"resource_grade": True}}])
    r = client.post(f"{C}/sessions/{sid}/prepare", json={
        "selected": [s["id"] for s in draft["suggestions"]],
        "draft_id": draft["draft_id"]})
    assert r.status_code == 200, r.text
    _assert_engine_sees_canon(parsed, canon, count=2)  # --check + --apply


# --------------------------------------- write_text_atomic (root cause 2)


def test_write_text_atomic_writes_crlf_exactly_once(tmp_path):
    text = "kind: style\r\nnote: raised the toll\r\npremise: and held it\r\n"
    p = spine.write_text_atomic(tmp_path / "style.yaml", text)
    raw = p.read_bytes()
    assert raw == text.encode("utf-8")
    assert b"\r\r\n" not in raw


def test_write_text_atomic_keeps_lf_text_single(tmp_path):
    text = "kind: style\nnote: raised the toll\n"
    p = spine.write_text_atomic(tmp_path / "style.yaml", text)
    assert p.read_bytes() == text.encode("utf-8")
