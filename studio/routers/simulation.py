"""Simulation tab API: reads off the spine, the live week, views, why.

The live week (D4 copy-in/copy-out, D5 confirm):
  1. campaign simulation/state.yaml -> agentic-simulator/world/state.yaml
     (the engine's previous state is kept as state.yaml.bak first; the
     engine home is scratch, the campaign is canon)
  2. run `python -X utf8 src/tick.py --live` (tick.py docstring mode 2),
     cwd=agentic-simulator, list-form args per D3
  3. copy back the new state.yaml + any NEW chronicle files (diff by
     filename); on engine failure nothing is copied back
  4. result {week, chronicle:{week,title,file}, held:[faction ids]} — held
     parsed from the tick log's `HOLDS (` lines and the chronicle's
     `Held (scheduler): a, b` line
"""

import datetime
import json
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool, field_validator

import engines
import jobs
import spine
from routers.common import require_campaign

router = APIRouter()

_STATE_BACKUP = "state.yaml.bak"
_HOLDS_RE = re.compile(r"^\s*([a-z_]+)\s+HOLDS\b")
_WEEK_TIMEOUT = 1800  # a live week is minutes of LLM calls
# clause/sentence punctuation that may end a proposed timeline title
# (apostrophes and quotes deliberately excluded — possessives are not cuts)
_CLAUSE_PUNCT_RE = re.compile(r"[—–,;:.!?()\[\]]")


class WeekBody(BaseModel):
    # StrictBool: the string "true" must not pass the expensive-button gate
    confirm: StrictBool

    @field_validator("confirm")
    @classmethod
    def _confirm_is_true(cls, v):
        if v is not True:
            raise ValueError("confirm must be exactly true — this runs a real "
                             "week: it spends API credits and advances the canon clock")
        return v


class WhyBody(BaseModel):
    week: int
    faction: str


@router.get("/api/campaign/{name}/simulation")
def get_simulation(name: str):
    require_campaign(name)
    state = spine.load_state(name)
    if not state:
        raise HTTPException(status_code=404,
                            detail="no simulation/state.yaml in this campaign")
    meta = state.get("meta") or {}
    clock = meta.get("clock") or {}
    factions = state.get("factions") or []
    return {
        "clock": {"week": clock.get("week"), "season": clock.get("season")},
        "premise": meta.get("premise") or "",
        "factions": factions,
        "events": state.get("events") or [],
        "rumors": state.get("rumors") or [],
        "chronicles": spine.chronicles(name),
        "views_available": [f.get("id") for f in factions],
    }


@router.get("/api/campaign/{name}/simulation/views/{faction_id}")
def get_view(name: str, faction_id: str):
    require_campaign(name)
    state = spine.load_state(name)
    ids = [f.get("id") for f in (state.get("factions") or [])]
    if faction_id not in ids:
        raise HTTPException(status_code=404,
                            detail=f"unknown faction: {faction_id}")
    _copy_state_in(name)
    try:
        rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR, "src/views.py",
                                   faction_id, timeout=60)
    except RuntimeError as e:  # engine tree or python missing entirely
        raise HTTPException(status_code=503, detail=f"engine failed to start: {e}")
    out = "\n".join(lines).strip()
    if rc != 0 or not out:
        raise HTTPException(
            status_code=501,
            detail="views CLI not landed yet (agentic-simulator src/views.py "
                   "has no command-line interface)" + (f": {out[-200:]}" if out else ""))
    view = yaml.safe_load(out)
    if not isinstance(view, dict):
        raise HTTPException(status_code=501,
                            detail="views CLI printed unparseable YAML")
    return view


@router.post("/api/campaign/{name}/simulation/week", status_code=202)
def post_week(name: str, body: WeekBody):
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(status_code=404,
                            detail="no simulation/state.yaml in this campaign")

    def job(log):
        sim_dir = d / "simulation"
        _copy_state_in(name)
        log("state copied into engine home (previous engine state kept as "
            "state.yaml.bak)")
        chronicle_dir = engines.AGENTIC_SIMULATOR / "chronicle"
        before = {p.name for p in chronicle_dir.glob("*.md")} \
            if chronicle_dir.is_dir() else set()

        log("running src/tick.py --live (scheduler, proposals, dice, chronicle)")
        rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR, "src/tick.py",
                                   "--live", timeout=_WEEK_TIMEOUT, on_line=log)
        if rc != 0:
            for ln in lines[-10:]:  # make sure the tail shows the failure
                log(ln)
            raise RuntimeError("tick --live failed rc=%s: %s"
                               % (rc, " | ".join(lines[-10:])))

        new_state_file = engines.AGENTIC_SIMULATOR / "world" / "state.yaml"
        engines.copy_file(new_state_file, sim_dir / "state.yaml")
        after = {p.name for p in chronicle_dir.glob("*.md")}
        new_files = sorted(after - before)
        for fn in new_files:
            engines.copy_file(chronicle_dir / fn, sim_dir / "chronicle" / fn)
            log(f"copied back chronicle/{fn}")

        new_state = spine.load_state(name)
        week = (new_state.get("meta") or {}).get("clock", {}).get("week")
        chronicle = None
        held = {m.group(1) for ln in lines
                if (m := _HOLDS_RE.match(ln))}
        if new_files:
            fn = new_files[-1]
            chronicle = _chronicle_result(name, fn)
            held |= _held_from_chronicle(sim_dir / "chronicle" / fn)

        result = {"week": week, "chronicle": chronicle, "held": sorted(held)}
        # M3 approval queue: stage the pending-week skeleton for the week
        # just run (mechanical extraction; no LLM — the chronicle is already
        # written, the timeline only needs its skeleton)
        staged = None
        if week is not None:
            staged = stage_pending_week(name, week,
                                        new_state.get("events") or [])
            result["pending"] = f"simulation/pending-week-{week}.yaml"
            result["pending_entries"] = len(staged["entries"])
            if staged["entries"]:
                log(f"staged {len(staged['entries'])} proposed timeline "
                    f"entries -> simulation/pending-week-{week}.yaml "
                    "(review them in the Review tab)")
            else:
                log(f"no non-world events in week {week} — staged an empty "
                    f"pending-week-{week}.yaml")
        return result

    return JSONResponse({"job_id": jobs.submit("sim-week", job)}, status_code=202)


def _chronicle_result(name: str, filename: str) -> dict:
    c = next((c for c in spine.chronicles(name)
              if c["file"] == f"simulation/chronicle/{filename}"), None)
    if c is None:
        return {"week": None, "title": filename, 
                "file": f"simulation/chronicle/{filename}"}
    return c


def _held_from_chronicle(path: Path) -> set:
    """The chronicle's `Held (scheduler): a, b` line (tick.py write_chronicle)."""
    held = set()
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("Held") and ":" in ln:
            for tok in re.split(r"[,\s]+", ln.split(":", 1)[1].strip()):
                if tok:
                    held.add(tok)
    return held


def propose_title(text: str, max_words: int = 7, min_words: int = 4) -> str:
    """One short title for a proposed timeline entry. Mechanical, per the
    design doc: the chronicle prose is already written and reviewable — the
    timeline only needs its skeleton. Take the first 4-7 words of the event
    text, cut at the first clause-punctuation mark once min_words are in
    (an em-dash or comma at word 1-3 does not cut; possessives never cut)."""
    head = (text or "").split()[:max_words]
    for i, word in enumerate(head):
        if i >= min_words and _CLAUSE_PUNCT_RE.search(word):
            cut = " ".join(head[:i]).strip(" \t—–-,;:.!?()[]\"'")
            if cut:
                return cut
    return " ".join(head).strip()


def stage_pending_week(name: str, week: int, events: list) -> dict:
    """M3: mechanically stage simulation/pending-week-N.yaml — one proposed
    timeline entry per NON-WORLD event of the week just run (world events
    are scenery, not faction history). The pending file IS the state of the
    review; the DM edits/approves it in the Review tab. No LLM call.
    Returns the staged doc."""
    entries = []
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("week") != week:
            continue
        if str(ev.get("actor") or "").lower() == "world":
            continue
        text = str(ev.get("text") or "").strip()
        if not text:
            continue
        entries.append({
            "id": f"pw{week}-{len(entries) + 1}",
            "when": f"week {week}",
            "title": propose_title(text),
            "text": text,
            "source": f"sim:week-{week}",
        })
    doc = {
        "created": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "week": week,
        "entries": entries,
    }
    path = spine.campaign_dir(name) / "simulation" / f"pending-week-{week}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8")
    return doc


@router.get("/api/campaign/{name}/simulation/chronicle/{week}")
def get_chronicle(name: str, week: int):
    require_campaign(name)
    c = spine.read_chronicle(name, week)
    if c is None:
        raise HTTPException(status_code=404,
                            detail=f"no chronicle for week {week}")
    return {"week": c["week"], "title": c["title"], "markdown": c["markdown"]}


@router.post("/api/campaign/{name}/simulation/why")
def post_why(name: str, body: WhyBody):
    require_campaign(name)
    if not (spine.campaign_dir(name) / "simulation" / "state.yaml").is_file():
        raise HTTPException(status_code=404,
                            detail="no simulation/state.yaml in this campaign")
    _copy_state_in(name)
    try:
        rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR, "src/why.py",
                                   str(body.week), body.faction, "--json",
                                   timeout=120)
    except RuntimeError as e:  # engine tree or python missing entirely
        raise HTTPException(status_code=503, detail=f"engine failed to start: {e}")
    out = "\n".join(lines).strip()
    try:
        data = json.loads(out)
    except ValueError:
        raise HTTPException(
            status_code=501,
            detail="why.py does not support --json yet (agentic-simulator "
                   "src/why.py returned non-JSON output; the flag lands with M3)")
    lines_out = data if isinstance(data, list) else (data.get("lines") or [])
    return {"week": body.week, "faction": body.faction, "lines": lines_out}


def _copy_state_in(name: str) -> None:
    """D4: campaign state.yaml -> engine home, backing up the engine's
    previous state first. The engine reads a fixed world/state.yaml."""
    src = spine.campaign_dir(name) / "simulation" / "state.yaml"
    engine_state = engines.AGENTIC_SIMULATOR / "world" / "state.yaml"
    engine_state.parent.mkdir(parents=True, exist_ok=True)
    if engine_state.is_file():
        engines.copy_file(engine_state,
                          engine_state.parent / _STATE_BACKUP)
    engines.copy_file(src, engine_state)
