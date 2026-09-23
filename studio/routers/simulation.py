"""Simulation tab API: reads off the spine, the live week, views, why.

The live week (D4 copy-in/copy-out, D5 confirm):
  0. a `.week-run-flag` (campaign + timestamp) is written into the engine
     home BEFORE copy-in and removed after copy-out/copy-back; on boot the
     server restores world/state.yaml from state.yaml.bak when the flag is
     left behind (restore_interrupted_week, called from server.py's lifespan)
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
  5. a successful week commits a canon snapshot ("campaign/<name>: week N —
     title") and takes the per-campaign mutation lock for its whole run
     (submit_campaign_job: 409 while another mutating job is in flight)
"""

import contextlib
import datetime
import json
import logging
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool, field_validator

import canon
import engines
import spine
from routers.common import require_campaign, submit_campaign_job

router = APIRouter()

_log = logging.getLogger("studio.simulation")


@contextlib.contextmanager
def _engine_lock_bounded(timeout: float = 1.0):
    """views/why wait briefly for the shared engine home; a running week may
    hold it for many minutes, and a sync request thread parked that long
    (several open tabs' worth) drains the threadpool everything else needs.
    Refuse quickly instead — the UI renders the 409 in the error panel."""
    if not engines.ENGINE_LOCK.acquire(timeout=timeout):
        raise HTTPException(
            status_code=409,
            detail="the engine home is busy — a week run is in progress; "
                   "views and why reopen when it finishes")
    try:
        yield
    finally:
        engines.ENGINE_LOCK.release()

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


class CityBody(BaseModel):
    name: str
    population: int | None = None


class FactionBody(BaseModel):
    name: str
    kind: str = ""
    goal: str
    resources: dict[str, str] = {}


class CreateStateBody(BaseModel):
    premise: str
    season: str = "early spring"
    # optional in the model so the 422 can name the problem in studio words;
    # post_create_state refuses a sandbox without one — the engine's week
    # chronicle and faction views read city.name and crash after the spend
    city: CityBody | None = None
    factions: list[FactionBody]
    force: StrictBool = False


_RESOURCE_LEVELS = ("none", "low", "medium", "high")
_RESOURCE_KEYS = ("coin", "muscle", "legitimacy")
_PREMISE_MAX = 600


def _validated_faction_doc(factions: list[FactionBody]) -> list[dict]:
    """The state.yaml factions list from the create form: ids derived from
    names (underscore slugs — tick.py's HOLDS log lines parse [a-z_]+),
    resources filled to the canonical three with "low" defaults."""
    if not (2 <= len(factions) <= 5):
        raise HTTPException(
            status_code=422,
            detail=f"a sandbox needs 2-5 factions that want things from each "
                   f"other (got {len(factions)})")
    seen_ids: dict[str, str] = {}
    out = []
    for f in factions:
        name = (f.name or "").strip()
        goal = (f.goal or "").strip()
        if not name:
            raise HTTPException(status_code=422,
                                detail="every faction needs a name")
        if not goal:
            raise HTTPException(
                status_code=422,
                detail=f"faction '{name}' needs a goal — what it wants, in "
                       "one line (that want is what drives the week)")
        fid = spine.slugify(name).replace("-", "_")
        if not fid:
            raise HTTPException(
                status_code=422,
                detail=f"faction name '{name}' has no usable id characters")
        if fid == "world":
            raise HTTPException(
                status_code=422,
                detail="faction id 'world' is reserved (world events are "
                       "scenery, not a faction)")
        if fid in seen_ids:
            raise HTTPException(
                status_code=422,
                detail=f"two factions collapse to the same id '{fid}' "
                       f"('{name}' and '{seen_ids[fid]}') — give them "
                       "distinct names")
        seen_ids[fid] = name
        resources = {}
        for key, level in f.resources.items():
            if key not in _RESOURCE_KEYS:
                raise HTTPException(
                    status_code=422,
                    detail=f"faction '{name}': unknown resource '{key}' "
                           f"(use coin, muscle, legitimacy)")
            level = str(level).lower().strip()
            if level not in _RESOURCE_LEVELS:
                raise HTTPException(
                    status_code=422,
                    detail=f"faction '{name}': resource '{key}' must be one "
                           f"of {', '.join(_RESOURCE_LEVELS)}")
            resources[key] = level
        for key in _RESOURCE_KEYS:
            resources.setdefault(key, "low")
        out.append({
            "id": fid,
            "name": name,
            "goal": goal,
            "resources": resources,
        })
        if (f.kind or "").strip():
            out[-1]["kind"] = f.kind.strip()
    return out


@router.post("/api/campaign/{name}/simulation/state", status_code=201)
def post_create_state(name: str, body: CreateStateBody):
    """Author simulation/state.yaml from the UI — the fresh-campaign path
    that used to require hand-editing YAML. Nothing here calls the LLM: a
    sandbox is the DM's setup, not generated prose."""
    d = require_campaign(name)
    state_file = d / "simulation" / "state.yaml"
    if state_file.is_file() and not body.force:
        raise HTTPException(
            status_code=409,
            detail="simulation/state.yaml already exists — replacing it "
                   "would rewrite the sandbox's history, so this needs the "
                   "force flag (git keeps the old state either way)")

    premise = (body.premise or "").strip()
    if not premise:
        raise HTTPException(
            status_code=422,
            detail="the sandbox needs a premise — one line on what the "
                   "tension is about (e.g. 'the toll bridge burned the "
                   "night the wardens raised the toll')")
    if len(premise) > _PREMISE_MAX:
        raise HTTPException(status_code=422,
                            detail=f"premise is over {_PREMISE_MAX} chars — "
                                   "keep it to one tension, one line")
    season = (body.season or "early spring").strip() or "early spring"
    if len(season) > 40:
        raise HTTPException(status_code=422, detail="season: keep it short")

    if body.city is None or not (body.city.name or "").strip():
        raise HTTPException(
            status_code=422,
            detail="the sandbox needs a city or place — weeks are chronicled "
                   "there and faction views look at it (the engine crashes "
                   "on a city-less state after the week's spend)")
    city_name = body.city.name.strip()

    doc: dict = {
        "meta": {"premise": premise,
                 # week 0 = nothing has happened yet: tick.py increments
                 # BEFORE running, so the first live week is week 1
                 "clock": {"week": 0, "season": season}},
    }
    city = {"name": city_name}
    if body.city.population is not None:
        if body.city.population <= 0:
            raise HTTPException(status_code=422,
                                detail="city population must be positive")
        city["population"] = body.city.population
    doc["city"] = city
    doc["factions"] = _validated_faction_doc(body.factions)
    doc["events"] = []
    doc["rumors"] = []

    replaced = state_file.is_file()
    # Hold the campaign lock across write + snapshot: without it, a week job
    # finishing for this campaign would copy its (premise-derived) state back
    # over the user's brand-new sandbox and commit on top — the replace would
    # be silently lost to history. Blocking is right here: it's one file
    # write plus a git commit, same as review approve/delete.
    with spine.campaign_lock(name):
        spine.write_state(name, doc)
        canon.snapshot(name, f"campaign/{name}: simulation "
                             f"{'replaced' if replaced else 'created'}")
    return {"ok": True, "week": 0,
            "factions": [f["id"] for f in doc["factions"]]}


@router.get("/api/campaign/{name}/simulation")
def get_simulation(name: str):
    require_campaign(name)
    state = spine.load_state(name)
    if not state:
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign yet — "
                   "create one with the form on this tab")
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
    # the engine home is one shared scratch file: copy-in + run + read is a
    # critical section (engines.ENGINE_LOCK), or a views call for campaign B
    # clobbers campaign A's live week mid-run
    with _engine_lock_bounded():
        _copy_state_in(name)
        try:
            rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR, "src/views.py",
                                       faction_id, timeout=60)
        except RuntimeError as e:  # engine tree or python missing entirely
            raise HTTPException(status_code=503, detail=f"engine failed to start: {e}")
    out = "\n".join(lines).strip()
    if rc != 0:
        # the CLI exists (rc 0/1/2 are its contract); anything nonzero here
        # is an engine failure — say so, with the engine's own words
        raise HTTPException(
            status_code=502,
            detail=f"views engine failed (rc={rc})"
                   + (f": {out[-200:]}" if out else ""))
    if not out:
        raise HTTPException(status_code=502,
                            detail="views engine printed nothing (rc=0)")
    try:
        view = yaml.safe_load(out)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=502,
                            detail=f"views engine printed unparseable YAML: {e}")
    if not isinstance(view, dict):
        raise HTTPException(status_code=502,
                            detail="views engine output was not a YAML mapping")
    return view


@router.post("/api/campaign/{name}/simulation/week", status_code=202)
def post_week(name: str, body: WeekBody):
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign — create the "
                   "sandbox first ('Create a simulation' on this tab)")
    # fast-fail courtesy: the engine home is shared across campaigns, and the
    # job's blocking acquire would otherwise queue a second week behind a
    # half-hour run from a different campaign with no signal at all
    if not engines.ENGINE_LOCK.acquire(timeout=1.0):
        raise HTTPException(
            status_code=409,
            detail="the engine home is busy — a week run is already in "
                   "flight (one engine home, one week at a time)")
    engines.ENGINE_LOCK.release()

    def job(log):
        sim_dir = d / "simulation"
        # interrupted-run detection: flagged before copy-in, cleared only
        # when copy-out/copy-back has finished (or failed and rolled back)
        write_week_flag(name)
        log(f"week run flagged ({engines.WEEK_FLAG_NAME}) — a boot after a "
            "crash restores the engine home from its backup")
        # the engine home is one shared scratch file across campaigns: the
        # whole copy-in -> tick -> copy-back sequence is a critical section
        # (engines.ENGINE_LOCK). Lock order: campaign lock (held by this job)
        # outside, engine lock inside — views/why take only the engine lock.
        with engines.ENGINE_LOCK:
            return _run_week_locked(name, d, log)

    return submit_campaign_job("sim-week", name, job)


def _run_week_locked(name: str, d: Path, log) -> dict:
    """The week's copy-in -> tick -> copy-back, run under the engine lock
    (post_week's job holds the campaign lock outside). Flag clearing happens
    however the block exits."""
    sim_dir = d / "simulation"
    try:
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
        engines.copy_file_atomic(new_state_file, sim_dir / "state.yaml")
        after = {p.name for p in chronicle_dir.glob("*.md")}
        new_files = after - before
        for fn in new_files:
            engines.copy_file(chronicle_dir / fn, sim_dir / "chronicle" / fn)
            log(f"copied back chronicle/{fn}")

        new_state = spine.load_state(name)
        week = (new_state.get("meta") or {}).get("clock", {}).get("week")
        chronicle = None
        held = {m.group(1) for ln in lines
                if (m := _HOLDS_RE.match(ln))}
        if new_files:
            # the run's chronicle = the newest new file by mtime; sorted-by-
            # name is wrong for reruns (0005-a-2.md sorts before 0005-a.md)
            fn = max(new_files, key=lambda f: (chronicle_dir / f).stat().st_mtime)
            chronicle = _chronicle_result(name, fn)
            held |= _held_from_chronicle(sim_dir / "chronicle" / fn)

        result = {"week": week, "chronicle": chronicle, "held": sorted(held)}
        # M3 approval queue: stage the pending-week skeleton for the week
        # just run (mechanical extraction; no LLM — the chronicle is already
        # written, the timeline only needs its skeleton)
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

        # canon snapshot: the clock advanced, the chronicle exists
        canon.snapshot(name, _week_message(name, week, chronicle))
        return result
    finally:
        clear_week_flag()


def _week_message(name: str, week, chronicle) -> str:
    msg = f"campaign/{name}: week {week}" if week is not None \
        else f"campaign/{name}: simulation tick"
    title = str((chronicle or {}).get("title") or "").strip()
    if title:
        msg += f" — {title}"
    return msg


# ------------------------------------------------- interrupted-week flag


def write_week_flag(name: str) -> None:
    """Mark a week run in flight (content: campaign name + ISO timestamp)."""
    flag = engines.week_flag_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(json.dumps({
        "campaign": name,
        "started": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")


def clear_week_flag() -> None:
    try:
        engines.week_flag_path().unlink()
    except FileNotFoundError:
        pass


def restore_interrupted_week():
    """Boot-time repair (server.py lifespan): a left-behind week flag means
    the server died mid-week. Restore the engine home's state.yaml from its
    .bak (when one exists), delete the flag, and warn loudly. Returns
    {"campaign","restored"} or None when no flag was left."""
    flag = engines.week_flag_path()
    if not flag.is_file():
        return None
    doc = {}
    try:
        loaded = json.loads(flag.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            doc = loaded
    except (OSError, ValueError):
        pass  # unreadable flag: still restore, still clear
    name = str(doc.get("campaign") or "unknown")
    state = engines.AGENTIC_SIMULATOR / "world" / "state.yaml"
    bak = state.parent / _STATE_BACKUP
    if bak.is_file():
        engines.copy_file_atomic(bak, state)
        _log.warning("interrupted week run for campaign %s — engine home "
                     "restored", name)
    else:
        _log.warning("interrupted week run for campaign %s — no %s in the "
                     "engine home, leaving it as it stands", name, _STATE_BACKUP)
    clear_week_flag()
    return {"campaign": name, "restored": bak.is_file()}


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
    # atomic write: a review GET may read this file while the week job is
    # still finishing
    spine.write_text_atomic(
        path,
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))
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
    state = spine.load_state(name)
    ids = [f.get("id") for f in (state.get("factions") or [])]
    if body.faction not in ids:
        raise HTTPException(status_code=404,
                            detail=f"unknown faction: {body.faction}")
    with _engine_lock_bounded():  # copy-in + run is the shared-home critical section
        _copy_state_in(name)
        try:
            rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR, "src/why.py",
                                       str(body.week), body.faction, "--json",
                                       timeout=120)
        except RuntimeError as e:  # engine tree or python missing entirely
            raise HTTPException(status_code=503, detail=f"engine failed to start: {e}")
    out = "\n".join(lines).strip()
    if rc != 0:
        raise HTTPException(
            status_code=502,
            detail=f"why engine failed (rc={rc})"
                   + (f": {out[-200:]}" if out else ""))
    try:
        data = json.loads(out)
    except ValueError:
        # why.py has supported --json since M3 — non-JSON output means the
        # engine run itself went wrong, not that the flag is missing
        raise HTTPException(
            status_code=502,
            detail="why engine returned non-JSON output"
                   + (f": {out[-200:]}" if out else ""))
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
