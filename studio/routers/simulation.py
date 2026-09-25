"""Simulation tab API: reads off the spine, week PROPOSALS, views, why.

The live week became a proposal flow (Phase 2):
  0. a `.week-run-flag` guards the engine home as before; on boot a
     left-behind flag restores world/state.yaml from state.yaml.bak
  1. POST .../week captures the base revisions (state.yaml bytes, timeline
     bytes, chronicle + entity-pages membership fingerprints), copies the
     state into the engine home (scratch), runs `tick.py --live`
  2. the engine's OUTPUT IS NOT COPYED BACK: it is staged as a proposal
     bundle under simulation/proposals/<id>/ (proposed state, chronicles,
     timeline entries, structured diff, run record). Live clock, resources,
     rumors, events, timeline, chronicles remain byte-identical.
  3. GET .../proposal shows the pending bundle (survives restarts);
     POST .../proposal/{id}/accept publishes the WHOLE bundle through
     publication.py (revision-validated, transactional, idempotent);
     POST .../proposal/{id}/discard flips bundle status only.
  4. exactly one pending proposal per campaign; a new week is refused
     while one is unresolved.
  5. Standalone `python src/tick.py --live` keeps its direct-advance
     behavior — Studio's copy-in/copy-out is the only thing that changed.

Invalid candidates never become approvable bundles: an engine failure, a
clock that did not advance exactly one week, a missing chronicle, or a run
where every faction call failed are job errors, not proposals. A genuinely
quiet scheduler-held week is valid.
"""

import contextlib
import datetime
import hashlib
import json
import logging
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool, field_validator

import canon
import engines
import forecasts
import links
import llm_client
import proposals
import publication
import spine
import timeline
from routers.common import (campaign_lock_nowait, require_campaign,
                            submit_campaign_job)
from routers.common import ensure_publishable_409

router = APIRouter()

# proposal kinds that change ONLY simulation/state.yaml — no chronicles, no
# timeline entries. The accept path treats everything else as a full week.
STATE_ONLY_KINDS = frozenset({"session_effects", "goal_updates"})

_log = logging.getLogger("studio.simulation")


# views/why read the CAMPAIGN's own state via the engine's --state flag —
# no copy-in, so no shared-engine-home critical section and no lock: a view
# of campaign A works while campaign B's week runs. The engine lock now
# guards only what actually mutates the shared home (week copy-in/copy-out).
_HOLDS_RE = re.compile(r"^\s*([a-z_]+)\s+HOLDS\b")
_WEEK_TIMEOUT = 1800  # a live week is minutes of LLM calls


@contextlib.contextmanager
def _engine_lock_logged(log, why: str):
    """The engine home is one shared scratch across campaigns: jobs that
    passed the submit-time courtesy check can still queue here behind
    another campaign's run. Log the wait so a slow run is never silent,
    and bound it (a run aborted by its own watchdog eventually releases)."""
    deadline = 3 * (_WEEK_TIMEOUT + 60)
    if not engines.ENGINE_LOCK.acquire(timeout=5.0):
        log(f"waiting for the engine home — {why} is in flight; this run "
            "starts when it finishes (the job log goes quiet meanwhile)")
        if not engines.ENGINE_LOCK.acquire(timeout=deadline):
            raise RuntimeError(
                f"the engine home stayed busy for over {deadline // 60} "
                f"minutes — giving up without running anything")
    try:
        yield
    finally:
        engines.ENGINE_LOCK.release()
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


class ForecastBody(BaseModel):
    """A forecast runs the real engine forward N weeks on scratch data and
    stages a projection the DM reads and discards — never canon, never
    occupying the pending-proposal slot. N× a week's API spend."""
    confirm: StrictBool
    weeks: int = 2

    @field_validator("confirm")
    @classmethod
    def _confirm_is_true(cls, v):
        if v is not True:
            raise ValueError("confirm must be exactly true — this runs a "
                             "real forecast: it spends N weeks of API credits")
        return v

    @field_validator("weeks")
    @classmethod
    def _weeks_range(cls, v):
        if v not in (2, 3):
            raise ValueError("weeks must be 2 or 3 — the forecast runs the "
                             "real engine forward one week at a time")
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


class SessionEffectBody(BaseModel):
    """One structured consequence of play (Phase 3): typed, reviewed,
    deduplicated by stable id. `resource`/`field` choose the set_field vs
    set_resource shape; expected_before is the precondition the DM saw."""
    type: str
    faction_id: str
    after: str
    reason: str
    id: str | None = None
    resource: str | None = None
    field: str | None = None
    expected_before: str | None = None
    week: int | None = None


class SessionEventBody(BaseModel):
    actor: str = "world"
    text: str


class SessionBody(BaseModel):
    """Manual session ingestion — works without any API key (LLM extraction
    stays optional and outside this path). Events are HISTORY ONLY (no
    mechanical effect); effects are the reviewed state changes."""
    source: str
    week: int | None = None
    events: list[SessionEventBody] = []
    effects: list[SessionEffectBody] = []


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
    # be silently lost to history. Fast-fail, not blocking: it's one file
    # write plus a git commit, but a week run holds this lock for its whole
    # 30-minute ceiling and a blocked create reads as a hung page.
    with campaign_lock_nowait(name, "creating the simulation sandbox"):
        spine.write_state(name, doc)
        snap = canon.snapshot_result(
            name, f"campaign/{name}: simulation "
                  f"{'replaced' if replaced else 'created'}")
    return {"ok": True, "week": 0,
            "factions": [f["id"] for f in doc["factions"]],
            "snapshot": snap}


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
        "pending_proposal": (proposals.find_pending(name) or {}).get(
            "proposal_id"),
        "active_forecast": (forecasts.find_active(name) or {}).get(
            "forecast_id"),
    }


@router.get("/api/campaign/{name}/simulation/views/{faction_id}")
def get_view(name: str, faction_id: str):
    require_campaign(name)
    state = spine.load_state(name)
    ids = [f.get("id") for f in (state.get("factions") or [])]
    if faction_id not in ids:
        raise HTTPException(status_code=404,
                            detail=f"unknown faction: {faction_id}")
    # views.py reads ONE file, and its --state flag points it anywhere:
    # hand it the campaign's own canon state directly. No copy-in means no
    # shared-engine-home critical section — a view works for any campaign
    # while any week runs (canon is untouched until a proposal is accepted).
    try:
        rc, lines = engines.run_py(
            engines.AGENTIC_SIMULATOR, "src/views.py", faction_id,
            "--state", str(spine.campaign_dir(name)
                           / "simulation" / "state.yaml"), timeout=60)
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
    pending = proposals.find_pending(name)
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"a proposed week {pending.get('proposed_week')} is "
                   f"awaiting review ({pending.get('proposal_id')}) — accept "
                   f"or discard it before generating another")
    ensure_publishable_409(name)  # a parked conflict blocks campaign writes
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
        # the pre-route check raced: re-check under the campaign lock this
        # job runs in — two quick requests must never stage two pendings
        racing = proposals.find_pending(name)
        if racing:
            raise RuntimeError(
                f"a proposal appeared while this run was starting "
                f"({racing.get('proposal_id')}) — nothing was run or spent; "
                "accept or discard it first")
        # interrupted-run detection: flagged before copy-in, cleared only
        # when the proposal is staged (or failed and rolled back)
        write_week_flag(name)
        log(f"week run flagged ({engines.WEEK_FLAG_NAME}) — a boot after a "
            "crash restores the engine home from its backup")
        # the engine home is one shared scratch file across campaigns: the
        # whole copy-in -> tick sequence is a critical section
        # (engines.ENGINE_LOCK). Lock order: campaign lock (held by this job)
        # outside, engine lock inside — views/why take only the engine lock.
        with _engine_lock_logged(log, "a week run or forecast"):
            return _run_week_proposal(name, d, log)

    return submit_campaign_job("sim-week", name, job)


def _clock_week(doc: dict):
    return ((doc.get("meta") or {}).get("clock") or {}).get("week")


def _state_snapshot(state_path: Path) -> tuple[str, str]:
    """The state text and its revision from ONE read. Two separate reads
    (text, then revision) let an external hand edit land in between and pin
    an after-image to revisions it was never derived from — the staleness
    check would then pass at accept time and publish over the edit."""
    data = state_path.read_bytes()
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()


def _run_week_proposal(name: str, d: Path, log) -> dict:
    """Copy-in -> tick -> STAGE A PROPOSAL, run under the engine lock
    (post_week's job holds the campaign lock outside). Nothing is copied
    into the campaign here: the engine's output becomes a pending proposal
    bundle the DM reviews. Flag clearing happens however the block exits."""
    try:
        base_doc = spine.load_state(name)
        base_week = _clock_week(base_doc)
        # fail BEFORE any spend: a hand-edited float week (week: 4.0) would
        # otherwise crash tick.py mid-run after the faction calls, and re-
        # crash on every retry — each attempt a full week's API cost
        if not isinstance(base_week, int) or isinstance(base_week, bool):
            raise RuntimeError(
                f"meta.clock.week is {base_week!r}, not an integer — fix "
                "simulation/state.yaml before running a week")
        base_revisions = {
            "simulation/state.yaml": publication.file_revision(
                d / "simulation" / "state.yaml"),
            "lore/timeline.yaml": publication.file_revision(
                d / "lore" / "timeline.yaml"),
        }
        input_set_revisions = {
            "simulation/chronicle": publication.fingerprint_dir(
                d / "simulation" / "chronicle"),
            "lore/entities": publication.fingerprint_dir(
                d / "lore" / "entities"),
        }
        chronicle_dir = engines.engine_chronicle_dir()

        _copy_state_in(name)
        # capture AFTER copy-in: the copy clears the home's run outputs, so
        # this is the clean baseline every new chronicle is diffed against
        before = {p.name for p in chronicle_dir.glob("*.md")} \
            if chronicle_dir.is_dir() else set()
        log("state copied into engine home (previous engine state kept as "
            "state.yaml.bak)")
        log("running src/tick.py --live (scheduler, proposals, dice, chronicle)")
        rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR, "src/tick.py",
                                   "--live", timeout=_WEEK_TIMEOUT, on_line=log)
        if rc != 0:
            for ln in lines[-10:]:  # make sure the tail shows the failure
                log(ln)
            raise RuntimeError("tick --live failed rc=%s: %s"
                               % (rc, " | ".join(lines[-10:])))

        engine_state = engines.engine_state_path()
        try:
            new_state_text = engine_state.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"the engine wrote no state: {e}")
        after_doc = yaml.safe_load(new_state_text) or {}
        if not isinstance(after_doc, dict):
            raise RuntimeError("the engine's state does not parse as a "
                               "mapping — no proposal staged")
        proposed_week = _clock_week(after_doc)

        # invalid candidates never become approvable bundles (a float clock
        # would slip the != check — 5.0 == 5 — and poison the manifest)
        if not isinstance(proposed_week, int) or isinstance(proposed_week, bool) \
                or (base_week is not None and proposed_week != base_week + 1):
            raise RuntimeError(
                f"the engine did not advance exactly one week (base week "
                f"{base_week} -> {proposed_week}) — no proposal staged")
        new_files = sorted(
            (chronicle_dir.glob("*.md") and
             {p.name for p in chronicle_dir.glob("*.md")} - before)
            if chronicle_dir.is_dir() else set())
        if not new_files:
            raise RuntimeError("the engine wrote no chronicle — no proposal "
                               "staged (a run with no chronicle cannot be "
                               "reviewed)")
        n_factions = len(base_doc.get("factions") or [])
        proposal_failed = [ln for ln in lines
                           if "HOLDS (proposal failed" in ln]
        scheduler_held = {m.group(1) for ln in lines
                          if (m := _HOLDS_RE.match(ln))
                          and "proposal failed" not in ln}
        if n_factions and proposal_failed \
                and (len(proposal_failed) + len(scheduler_held)) >= n_factions:
            tail = " | ".join(proposal_failed[-3:])
            raise RuntimeError(
                "every faction call failed or held — reporting generation "
                f"failure rather than staging an empty week ({tail})")

        # the resolver's replay trail (normalized proposals + seed + rule
        # version) rides with the proposal when the engine wrote one
        replay_path = engines.engine_replay_path(proposed_week)
        replay_text = replay_path.read_text(encoding="utf-8") \
            if replay_path.is_file() else None
        try:
            replay_doc = yaml.safe_load(replay_text) \
                if replay_text else None
        except yaml.YAMLError:
            replay_doc = None

        # the engine's RNG seed for the proposed week (replay identity; the
        # LLM responses themselves are nondeterministic and are NOT replayed)
        run_record = {
            "engine": "agentic-simulator/src/tick.py --live",
            "seed": ((after_doc.get("meta") or {}).get("rng_seeds") or {})
            .get(str(proposed_week)),
            "rule_version": replay_doc.get("rule_version")
            if isinstance(replay_doc, dict) else None,
            "model": None, "base_url": None,
        }
        try:
            cfg = llm_client.resolve_config()
            run_record["model"] = cfg.get("model")
            run_record["base_url"] = cfg.get("url")
        except Exception:  # noqa: BLE001 — identity is best-effort, no secrets
            pass

        chronicles = {fn: (chronicle_dir / fn).read_text(encoding="utf-8")
                      for fn in new_files}
        entries = build_week_entries(proposed_week,
                                     after_doc.get("events") or [])
        pid = proposals.create(
            name, base_week=base_week, proposed_week=proposed_week,
            base_revisions=base_revisions,
            input_set_revisions=input_set_revisions,
            artifacts={"state": new_state_text, "chronicles": chronicles,
                       "entries": entries, "replay": replay_text},
            run_record=run_record, before_state=base_doc)
        log(f"proposed week {proposed_week} staged ({pid}) — nothing in the "
            "campaign has changed; review it, then accept or discard")

        held = {m.group(1) for ln in lines if (m := _HOLDS_RE.match(ln))}
        for fn in new_files:
            held |= _held_from_chronicle_path(chronicle_dir / fn)
        diff = proposals.diff_states(base_doc, after_doc, entries)
        return {
            "proposal_id": pid, "status": "pending",
            "week": proposed_week, "base_week": base_week,
            "chronicle": _chronicle_meta(new_files),
            "held": sorted(held), "timeline_entries": len(entries),
            "diff": diff,
        }
    finally:
        clear_week_flag(name)


def _run_forecast(name: str, d: Path, n_weeks: int, log) -> dict:
    """Copy-in -> N x tick --live on scratch -> record the projection. Runs
    under the engine lock (the job holds the campaign lock outside). The
    campaign is NEVER written except the forecast bundle; nothing here is
    canon-able, so there is no accept — the DM reads and discards."""
    try:
        base_doc = spine.load_state(name)
        base_week = _clock_week(base_doc)
        # fail BEFORE any spend (same rule as the week flow: a hand-edited
        # float week would otherwise burn the first tick then re-crash)
        if not isinstance(base_week, int) or isinstance(base_week, bool):
            raise RuntimeError(
                f"meta.clock.week is {base_week!r}, not an integer — fix "
                "simulation/state.yaml before running a forecast")
        _copy_state_in(name)
        log("state copied into engine home (previous engine state kept as "
            "state.yaml.bak)")
        chronicle_dir = engines.engine_chronicle_dir()
        prev_doc = base_doc
        weeks_out = []
        for i in range(1, n_weeks + 1):
            before = {p.name for p in chronicle_dir.glob("*.md")} \
                if chronicle_dir.is_dir() else set()
            log(f"projecting week {base_week + i} "
                f"(tick {i}/{n_weeks} on scratch data)")
            rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR,
                                       "src/tick.py", "--live",
                                       timeout=_WEEK_TIMEOUT, on_line=log)
            if rc != 0:
                for ln in lines[-10:]:
                    log(ln)
                raise RuntimeError(
                    "forecast week %d failed rc=%s: %s"
                    % (base_week + i, rc, " | ".join(lines[-10:])))
            try:
                after_text = engines.engine_state_path().read_text(
                    encoding="utf-8")
            except OSError as e:
                raise RuntimeError(f"the engine wrote no state: {e}")
            after_doc = yaml.safe_load(after_text) or {}
            if not isinstance(after_doc, dict):
                raise RuntimeError(
                    "the engine's state does not parse as a mapping — "
                    "forecast aborted")
            week = _clock_week(after_doc)
            if not isinstance(week, int) or isinstance(week, bool) \
                    or week != _clock_week(prev_doc) + 1:
                raise RuntimeError(
                    "the engine did not advance exactly one week "
                    f"({_clock_week(prev_doc)} -> {week}) — forecast aborted")
            new_files = sorted(
                {p.name for p in chronicle_dir.glob("*.md")} - before) \
                if chronicle_dir.is_dir() else []
            if not new_files:
                raise RuntimeError(
                    f"the engine wrote no chronicle for week {week} — "
                    "forecast aborted")
            n_factions = len(base_doc.get("factions") or [])
            proposal_failed = [ln for ln in lines
                               if "HOLDS (proposal failed" in ln]
            scheduler_held = {m.group(1) for ln in lines
                              if (m := _HOLDS_RE.match(ln))
                              and "proposal failed" not in ln}
            if n_factions and proposal_failed \
                    and (len(proposal_failed) + len(scheduler_held)) \
                    >= n_factions:
                raise RuntimeError(
                    "every faction call failed in projected week "
                    f"{week} — the projection is not a forecast, it is "
                    "an outage (nothing recorded)")
            title = (_chronicle_meta(new_files) or {}).get("title")
            markdown = "\n\n".join(
                (chronicle_dir / fn).read_text(encoding="utf-8")
                for fn in new_files)
            weeks_out.append({
                "week": week, "title": title,
                "markdown": markdown,
                "diff": proposals.diff_states(prev_doc, after_doc, []),
            })
            prev_doc = after_doc
        run_record = {
            "engine": f"agentic-simulator/src/tick.py --live x{n_weeks} "
                      "(scratch forecast)",
            # no seed/rule_version (week bundles carry them): a projection
            # is not replayable by design — each tick re-asks the model
            "model": None, "base_url": None,
        }
        try:
            cfg = llm_client.resolve_config()
            run_record["model"] = cfg.get("model")
            run_record["base_url"] = cfg.get("url")
        except Exception:  # noqa: BLE001 — identity is best-effort, no secrets
            pass
        fid = forecasts.create(name, base_week=base_week, weeks=weeks_out,
                               run_record=run_record)
        log(f"forecast staged ({fid}): {n_weeks} projected week(s), base "
            f"week {base_week} — a projection only; the campaign is "
            "unchanged and nothing here can be accepted into canon")
        return {"forecast_id": fid, "status": "active", "base_week": base_week,
                "weeks": weeks_out}
    finally:
        clear_week_flag(name)


def _chronicle_meta(filenames: list) -> dict | None:
    """The primary chronicle for a run = the newest new file by mtime
    (sorted-by-name is wrong for reruns: 0005-a-2.md sorts before
    0005-a.md). Title parsed from the file's `# Week N — Title` heading."""
    if not filenames:
        return None
    chronicle_dir = engines.engine_chronicle_dir()
    fn = max(filenames, key=lambda f: (chronicle_dir / f).stat().st_mtime)
    title = fn
    try:
        for ln in (chronicle_dir / fn).read_text(encoding="utf-8").splitlines()[:1]:
            m = re.match(r"#\s*Week\s+\d+\s*[—–-]\s*(.+?)\s*$", ln)
            if m:
                title = m.group(1)
    except (OSError, UnicodeDecodeError):
        pass
    week_m = re.match(r"(\d{4})", fn)
    return {"week": int(week_m.group(1)) if week_m else None,
            "title": title, "file": f"simulation/chronicle/{fn}"}


def _held_from_chronicle_path(path: Path) -> set:
    """The chronicle's `Held (scheduler): a, b` line (tick.py write_chronicle)."""
    held = set()
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("Held") and ":" in ln:
            for tok in re.split(r"[,\s]+", ln.split(":", 1)[1].strip()):
                if tok:
                    held.add(tok)
    return held


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


def clear_week_flag(name: str | None = None) -> None:
    """Clear the in-flight flag. With a campaign name, only clear the flag
    THIS campaign wrote: the flag is one shared file, and two queued engine
    jobs (a week behind a forecast) both write it — the first finisher must
    not delete the second's marker, or a crash during the second run skips
    boot restore entirely."""
    flag = engines.week_flag_path()
    if name is not None:
        try:
            doc = json.loads(flag.read_text(encoding="utf-8"))
            if not (isinstance(doc, dict) and doc.get("campaign") == name):
                return  # a later run's flag — leave it alone
        except (OSError, ValueError):
            pass  # unreadable/absent: the unlink below settles it
    try:
        flag.unlink()
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
    state = engines.engine_state_path()
    bak = engines.engine_state_backup_path()
    if bak.is_file():
        engines.copy_file_atomic(bak, state)
        _log.warning("interrupted week run for campaign %s — engine home "
                     "restored", name)
    else:
        _log.warning("interrupted week run for campaign %s — no state.yaml.bak "
                     "in the engine home, leaving it as it stands", name)
    clear_week_flag()
    return {"campaign": name, "restored": bak.is_file()}


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


def build_week_entries(week: int, events: list) -> list:
    """One proposed timeline entry per NON-WORLD event of the proposed week
    (world events are scenery, not faction history). Pure: the entries are
    staged INTO THE PROPOSAL BUNDLE — nothing lands in lore/pending or the
    timeline until the whole week is accepted."""
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
    return entries


# -------------------------------------------------------- proposal routes


@router.get("/api/campaign/{name}/simulation/proposal")
def get_proposal(name: str):
    """The pending proposal (week or session effects), everything the DM
    needs to decide, or {"proposal": null}. Reads the bundle from disk, so
    review works after a server restart."""
    require_campaign(name)
    pending = proposals.find_pending(name)
    if not pending:
        return {"proposal": None}
    pid = str(pending.get("proposal_id"))
    try:
        bundle = proposals.load(name, pid)
    except proposals.ProposalError as e:
        # a damaged bundle must not 500 every poll: name it and point at
        # the exit (discard still works — it flips status only)
        raise HTTPException(
            status_code=422,
            detail=f"the proposal bundle {pid} is damaged and cannot be "
                   f"shown ({e}) — discard it and prepare a fresh one")
    return {
        "proposal": {
            "proposal_id": pid,
            "kind": pending.get("kind") or "simulated_week",
            "status": pending.get("status"),
            "base_week": pending.get("base_week"),
            "proposed_week": pending.get("proposed_week"),
            "created": pending.get("created"),
            "run_record": pending.get("run_record") or {},
            "base_revisions": pending.get("base_revisions") or {},
            "input_set_revisions": pending.get("input_set_revisions") or {},
        },
        "diff": bundle["diff"],
        "entries": bundle["entries"],
        "updates": bundle["updates"],
        "findings": bundle.get("findings") or [],
        "chronicles": [
            {"file": f"simulation/chronicle/{fn}", "title": fn,
             "markdown": text}
            for fn, text in bundle["chronicles"].items()],
    }


@router.post("/api/campaign/{name}/simulation/proposal/{pid}/accept")
def accept_proposal(name: str, pid: str):
    """Publish the WHOLE pending proposal. For a simulated_week that is
    state + chronicles + timeline entries; for session_effects, the new
    state (history events + validated resource/fact changes). One
    transactional publication (publication.py), validated against the base
    revisions recorded when the proposal was created — an external edit
    between then and now is a 409, never a silent overwrite. Idempotent:
    repeating the accept returns the recorded result."""
    require_campaign(name)
    # a stable key for this exact accept: a repeat (double click, retry,
    # restart) returns the recorded result instead of applying twice
    key = hashlib.sha256(
        json.dumps(["accept-proposal", name, pid]).encode("utf-8")).hexdigest()
    with campaign_lock_nowait(name, "accepting the proposal"):
        ensure_publishable_409(name)
        prior = publication.receipt(name, key)
        if prior:
            # the crash window AFTER the receipt: publish landed, the status
            # flip did not. Repair it from the receipt, or a proposal that
            # is actually canon stays "pending" and blocks every new week.
            try:
                b = proposals.load(name, pid)
                if b["manifest"].get("status") == "pending":
                    proposals.set_status(name, pid, "accepted",
                                         decided_note="status repaired from "
                                                      "the accept receipt")
            except proposals.ProposalError:
                pass  # bundle gone or damaged: the receipt still answers
            if "snapshot" not in prior:  # older receipts predate the field
                snap = publication.transaction_snapshot(
                    name, prior.get("transaction"))
                if snap is not None:
                    prior = {**prior, "snapshot": snap}
            return {**prior, "idempotent": True}
        try:
            bundle = proposals.load(name, pid)
        except proposals.ProposalError as e:
            raise HTTPException(status_code=404, detail=str(e))
        manifest = bundle["manifest"]
        if manifest.get("status") != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"proposal {pid} is {manifest.get('status')!r}, "
                       "not pending — refresh and check its state")
        prior_tx = publication.transaction_for_key(name, key)
        if prior_tx is not None:
            # the crash window: this accept was applied and then recovered
            # by boot (receipt never written). The staged outcome IS the
            # original result — finish the bookkeeping and answer once.
            result = _accept_result(manifest, bundle, prior_tx["transaction"])
            publication.record_receipt(name, key, result)
            proposals.set_status(name, pid, "accepted",
                                 decided_note="published via crash recovery")
            snap = canon.snapshot_result(name, _accept_message(name, manifest,
                                                               None))
            publication.after_snapshot(name, prior_tx["transaction"], snap)
            return {**result, "snapshot": snap, "idempotent": True}
        d = spine.campaign_dir(name)

        base = dict(manifest.get("base_revisions") or {})
        writes = [{
            "path": "simulation/state.yaml",
            "text": bundle["state_text"],
        }]
        if manifest.get("kind") in STATE_ONLY_KINDS:
            chron = None
        else:
            entries = bundle["entries"]
            try:
                tdoc, tentries = timeline.load(d)
                new_timeline = timeline.append(tdoc, tentries, entries)
            except timeline.TimelineConflict as e:
                raise HTTPException(status_code=409, detail=str(e))
            except timeline.TimelineBadFile as e:
                raise HTTPException(status_code=422, detail=str(e))
            writes.append({
                "path": "lore/timeline.yaml",
                "text": timeline.dump(new_timeline),
            })
            for fn in bundle["chronicles"]:
                rel = f"simulation/chronicle/{fn}"
                writes.append({"path": rel, "text": bundle["chronicles"][fn]})
                base[rel] = publication.ABSENT  # create-only: a file that
                # appeared in the meantime is an external edit -> conflict
            chron = _chronicle_meta(sorted(bundle["chronicles"]))
        try:
            pub = publication.publish(
                name, writes, operation=_accept_operation(manifest),
                base_revisions=base, idempotency_key=key)
        except publication.RevisionConflict as e:
            raise HTTPException(
                status_code=409,
                detail=f"stale proposal: {e} — the campaign changed since "
                       f"this was generated. Discard it and prepare a fresh "
                       f"one from the new base. Nothing was written.")
        except publication.PublicationConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:  # noqa: BLE001 — mid-flight failure
            raise HTTPException(
                status_code=500,
                detail=f"the proposal could not be published ({e}) — the "
                       f"staged transaction is kept and will be recovered "
                       f"automatically when the studio restarts")

        week = manifest.get("proposed_week")
        result = _accept_result(manifest, bundle, pub["transaction"])
        if pub["state"] == "noop":
            result["noop"] = True
        publication.record_receipt(name, key, result)
        proposals.set_status(name, pid, "accepted",
                             decided_note="published; see the receipt")
        msg = _accept_message(name, manifest, chron)
        # vault stage 4 advisory decoration after publication, as before
        for fn in bundle["chronicles"]:
            try:
                if links.linkify_chronicle(name, fn):
                    _log.info("linked lore names in chronicle/%s", fn)
            except Exception as e:  # noqa: BLE001 — advisory, never blocking
                _log.warning("chronicle linkify skipped for %s: %s", fn, e)
        snap = canon.snapshot_result(name, msg)
        publication.after_snapshot(name, pub["transaction"], snap)
        result["snapshot"] = snap
        return result


def _accept_result(manifest: dict, bundle: dict, tx_id: str | None) -> dict:
    """The API result for an accept — used both live and when reconstructing
    a recovered accept's answer."""
    result = {
        "accepted": manifest.get("proposal_id"),
        "week": manifest.get("proposed_week"),
        "kind": manifest.get("kind") or "simulated_week",
        "transaction": tx_id,
    }
    if manifest.get("kind") in STATE_ONLY_KINDS:
        diff = bundle["diff"]
        result["events_added"] = len(diff.get("events_added") or [])
        result["resources_changed"] = len(diff.get("resources") or {})
        result["goals_changed"] = len(diff.get("fields") or {})
    else:
        result["chronicles"] = sorted(bundle["chronicles"])
        result["timeline_entries"] = len(bundle["entries"])
    return result


def _accept_operation(manifest: dict) -> str:
    kind = manifest.get("kind")
    if kind == "session_effects":
        return f"accept session effects ({manifest.get('run_record', {}) .get('source') or 'session'})"
    if kind == "goal_updates":
        return "accept goal updates from the ledger"
    return f"accept week {manifest.get('proposed_week')} proposal"


def _accept_message(name: str, manifest: dict, chron) -> str:
    kind = manifest.get("kind")
    if kind == "session_effects":
        source = (manifest.get("run_record") or {}).get("source") or "session"
        return f"campaign/{name}: session effects applied ({source})"
    if kind == "goal_updates":
        return f"campaign/{name}: goal updates applied (goal review)"
    msg = f"campaign/{name}: week {manifest.get('proposed_week')}"
    if chron and chron.get("title"):
        msg += f" — {chron['title']}"
    return msg


@router.post("/api/campaign/{name}/simulation/proposal/{pid}/discard")
def discard_proposal(name: str, pid: str):
    """Discard a proposal: flips the bundle's status only. The live campaign
    is untouched (it was never touched — generation staged everything), and
    the bundle stays on disk as an audit receipt. Rejected content is never
    fed into future prompts."""
    require_campaign(name)
    with campaign_lock_nowait(name, "discarding the proposal"):
        try:
            proposals.set_status(name, pid, "discarded",
                                 decided_note="discarded by the DM; no canon "
                                              "change ever happened")
        except proposals.ProposalError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"discarded": pid}


# ------------------------------------------------------- session ingestion


def _effect_id(effect: dict) -> str:
    """Stable effect id: content-derived when the DM did not supply one, so
    re-submitting the same session cannot mint a second identity."""
    if effect.get("id"):
        return str(effect["id"])
    payload = json.dumps(effect, sort_keys=True, ensure_ascii=False)
    return "eff-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@router.post("/api/campaign/{name}/simulation/session", status_code=202)
def post_session(name: str, body: SessionBody):
    """Record what happened at the table: history bullets + structured
    effects, validated by the ENGINE's own code (CLI subprocess — no
    imports across projects) and staged as a pending proposal. Nothing in
    the campaign changes until the proposal is accepted."""
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign — create the "
                   "sandbox first")
    pending = proposals.find_pending(name)
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"a proposal is awaiting review "
                   f"({pending.get('proposal_id')}) — accept or discard it "
                   f"before recording a session")
    ensure_publishable_409(name)
    source = (body.source or "").strip()
    if not source:
        raise HTTPException(status_code=422,
                            detail="source is required (e.g. "
                                   "session:session-08)")
    if not body.events and not body.effects:
        raise HTTPException(
            status_code=422,
            detail="nothing to record — add history events, structured "
                   "effects, or both")
    record = {
        "source": source,
        "events": [e.model_dump() for e in body.events],
        "effects": [],
    }
    if body.week is not None:
        record["week"] = body.week
    for e in body.effects:
        eff = {k: v for k, v in e.model_dump().items() if v is not None}
        eff["id"] = _effect_id(eff)
        eff["source"] = source
        record["effects"].append(eff)

    # the engine's own validator decides — same code the standalone CLI
    # path uses (list-form args; the parent path contains `&`)
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="studio-session-"))
    try:
        record_path = tmp_dir / "session.yaml"
        record_path.write_text(
            yaml.safe_dump(record, sort_keys=False, allow_unicode=True,
                           width=100), encoding="utf-8")

        # capture the base state and its revision TOGETHER under the lock:
        # the after-image must be judged against the exact bytes the engine
        # applied it to. The old order read the revision AFTER the engine
        # ran — a proposal accept landing in between pinned a pre-accept
        # after-image to post-accept revisions, so the accept-time staleness
        # check passed and the publish silently overwrote the accepted week.
        with campaign_lock_nowait(name, "recording the session"):
            state_path = d / "simulation" / "state.yaml"
            state_text, base_revision = _state_snapshot(state_path)
        copy_path = tmp_dir / "state-copy.yaml"
        copy_path.write_text(state_text, encoding="utf-8")

        rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR, "src/effects.py",
                                   "--check", str(record_path),
                                   "--state", str(copy_path), timeout=60)
        if rc != 0:
            detail = " | ".join(lines[-5:]) or "no output"
            raise HTTPException(
                status_code=422,
                detail=f"the engine refused this session record: {detail}")

        # produce the after-image by applying to the captured copy — the
        # campaign is never touched here
        rc, lines = engines.run_py(engines.AGENTIC_SIMULATOR, "src/effects.py",
                                   "--apply", str(record_path),
                                   "--state", str(copy_path), timeout=60)
        if rc != 0:
            detail = " | ".join(lines[-5:]) or "no output"
            raise HTTPException(
                status_code=422,
                detail=f"the engine could not apply this session record: "
                       f"{detail}")
        after_text = copy_path.read_text(encoding="utf-8")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    after_doc = yaml.safe_load(after_text) or {}
    base_doc = yaml.safe_load(state_text) or {}
    base_revisions = {"simulation/state.yaml": base_revision}
    with campaign_lock_nowait(name, "staging the session proposal"):
        # re-check under the lock: the pre-route check raced the moment the
        # request thread didn't hold it (two tabs, session + week together)
        racing = proposals.find_pending(name)
        if racing:
            raise HTTPException(
                status_code=409,
                detail=f"a proposal appeared while this session was being "
                       f"prepared ({racing.get('proposal_id')}) — accept or "
                       f"discard it first; nothing was staged")
        # and the state the engine validated against must still be canon:
        # an accept (or sandbox replace) during the engine run means this
        # after-image was derived from a state that no longer exists —
        # staging it would publish over that change
        current = publication.file_revision(
            d / "simulation" / "state.yaml")
        if current != base_revision:
            raise HTTPException(
                status_code=409,
                detail="simulation/state.yaml changed while this session "
                       "was being prepared (another proposal was accepted?) "
                       "— re-record the session against the current state; "
                       "nothing was staged")
        pid = proposals.create(
            name, kind="session_effects",
            base_week=_clock_week(base_doc), proposed_week=_clock_week(base_doc),
            base_revisions=base_revisions, input_set_revisions={},
            artifacts={"state": after_text, "chronicles": {}, "entries": []},
            run_record={"kind": "session", "source": source,
                        "engine": "agentic-simulator/src/effects.py --apply"},
            before_state=base_doc)
    diff = proposals.diff_states(base_doc, after_doc, [])
    return {
        "proposal_id": pid, "status": "pending", "kind": "session_effects",
        "events_added": len(diff.get("events_added") or []),
        "resources_changed": len(diff.get("resources") or {}),
        "diff": diff,
    }


# one bounded LLM pass + the effects CLI runs. complete()'s honest worst
# case is 3 attempts x 300s x 3 passes (base + two length extensions) plus
# backoff ~= 2700s: a slow-but-working provider must not get the tree
# killed and every retry re-spent, so the watchdog sits just above it.
_GOAL_REVIEW_TIMEOUT = 2800


@router.post("/api/campaign/{name}/simulation/goal-review", status_code=202)
def post_goal_review(name: str):
    """Goal review: the sim proposes faction goal updates grounded in the
    ledger (recorded weeks, rumors, canon deadlines), staged as a
    `goal_updates` proposal the DM reviews exactly like a week. Runs as a
    job (one bounded LLM pass in the engine); nothing changes until the
    proposal is accepted."""
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign — create the "
                   "sandbox first ('Create a simulation' on this tab)")
    pending = proposals.find_pending(name)
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"a proposal is awaiting review "
                   f"({pending.get('proposal_id')}) — accept or discard it "
                   f"before running a goal review")
    ensure_publishable_409(name)

    def job(log):
        # this job runs under the campaign's own lock (submit time) — never
        # re-acquire it in here. The pre-route check raced: re-check.
        racing = proposals.find_pending(name)
        if racing:
            raise RuntimeError(
                f"a proposal appeared while this run was starting "
                f"({racing.get('proposal_id')}) — nothing was run or spent; "
                "accept or discard it first")
        state_path = d / "simulation" / "state.yaml"
        state_text, base_revision = _state_snapshot(state_path)
        base_doc = yaml.safe_load(state_text) or {}

        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="studio-goals-"))
        try:
            copy_path = tmp_dir / "state.yaml"
            copy_path.write_text(state_text, encoding="utf-8")
            updates_path = tmp_dir / "goal-updates.yaml"
            log("asking the engine to propose goal updates from the ledger "
                "(one bounded LLM pass)")
            rc, lines = engines.run_py(
                engines.AGENTIC_SIMULATOR, "src/goals.py",
                "--state", str(copy_path), "--out", str(updates_path),
                timeout=_GOAL_REVIEW_TIMEOUT, on_line=log)
            if rc != 0:
                for ln in lines[-10:]:
                    log(ln)
                raise RuntimeError(
                    "goal review failed rc=%s: %s"
                    % (rc, " | ".join(lines[-10:])))
            try:
                updates = yaml.safe_load(
                    updates_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as e:
                raise RuntimeError(
                    f"the engine wrote no readable updates: {e}")
            if not isinstance(updates, dict):
                raise RuntimeError("the engine's updates file does not parse "
                                   "as a mapping — nothing staged")
            found = updates.get("updates") or []
            findings = updates.get("findings") or []
            if not found:
                log("no goal updates proposed — the ledger does not justify "
                    "any changes; nothing staged")
                return {"proposed": 0, "findings": list(findings),
                        "staged": False}
            found = [u for u in found if isinstance(u, dict)]

            # the engine's own validator decides — the same set_field path a
            # DM-recorded session takes, so a proposed goal cannot carry
            # anything a hand-written effect could not
            source = "goal-review:week-" + str(_clock_week(base_doc))
            record = {"source": source, "events": [], "effects": []}
            for u in found:
                eff = {
                    "type": "set_field",
                    "faction_id": u.get("faction_id"),
                    "field": "goal",
                    "after": u.get("goal"),
                    "reason": u.get("reason"),
                    "source": source,
                }
                eff["id"] = _effect_id(eff)
                record["effects"].append(eff)
            record_path = tmp_dir / "session.yaml"
            record_path.write_text(
                yaml.safe_dump(record, sort_keys=False, allow_unicode=True,
                               width=100), encoding="utf-8")
            rc, lines = engines.run_py(
                engines.AGENTIC_SIMULATOR, "src/effects.py",
                "--check", str(record_path), "--state", str(copy_path),
                timeout=60)
            if rc != 0:
                detail = " | ".join(lines[-5:]) or "no output"
                raise RuntimeError(
                    f"the engine refused the proposed goal updates: {detail}")
            rc, lines = engines.run_py(
                engines.AGENTIC_SIMULATOR, "src/effects.py",
                "--apply", str(record_path), "--state", str(copy_path),
                timeout=60)
            if rc != 0:
                detail = " | ".join(lines[-5:]) or "no output"
                raise RuntimeError(
                    f"the engine could not apply the proposed goal updates: "
                    f"{detail}")
            after_text = copy_path.read_text(encoding="utf-8")
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

        after_doc = yaml.safe_load(after_text) or {}
        # the engine ran for minutes: an accept (or sandbox replace) landing
        # in between means this after-image was derived from a state that no
        # longer exists — staging it would publish over that change
        current = publication.file_revision(state_path)
        if current != base_revision:
            raise RuntimeError(
                "simulation/state.yaml changed while the goal review was "
                "running (another proposal was accepted?) — run it again "
                "against the current state; nothing was staged")
        racing = proposals.find_pending(name)
        if racing:
            raise RuntimeError(
                f"a proposal appeared while this goal review was finishing "
                f"({racing.get('proposal_id')}) — nothing was staged")
        run_record = {"kind": "goal-review",
                      "engine": "agentic-simulator/src/goals.py --state",
                      "model": None, "base_url": None}
        try:
            cfg = llm_client.resolve_config()
            run_record["model"] = cfg.get("model")
            run_record["base_url"] = cfg.get("url")
        except Exception:  # noqa: BLE001 — identity is best-effort, no secrets
            pass
        pid = proposals.create(
            name, kind="goal_updates",
            base_week=_clock_week(base_doc),
            proposed_week=_clock_week(base_doc),
            base_revisions={"simulation/state.yaml": base_revision},
            input_set_revisions={},
            artifacts={"state": after_text, "chronicles": {}, "entries": [],
                       "updates": found, "findings": list(findings)},
            run_record=run_record, before_state=base_doc)
        diff = proposals.diff_states(base_doc, after_doc, [])
        log(f"staged {len(found)} proposed goal update(s) ({pid}) — nothing "
            "in the campaign has changed; review, then accept or discard")
        return {"proposal_id": pid, "status": "pending",
                "kind": "goal_updates", "proposed": len(found),
                "findings": list(findings), "staged": True, "diff": diff}

    return submit_campaign_job("goal-review", name, job)


@router.post("/api/campaign/{name}/simulation/forecast", status_code=202)
def post_forecast(name: str, body: ForecastBody):
    """Forecast: run the real engine forward 2-3 weeks on SCRATCH data and
    stage a projection the DM reads and discards — "where is this heading"
    before canon lands. Never canon: it occupies no pending-proposal slot
    (a real week can be generated while a forecast is on the table), the
    accept route cannot publish it, and the bundle is git-ignored."""
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign — create the "
                   "sandbox first ('Create a simulation' on this tab)")
    pending = proposals.find_pending(name)
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"a proposal is awaiting review "
                   f"({pending.get('proposal_id')}) — accept or discard it "
                   f"before forecasting (the forecast reads the canon the "
                   f"proposal would change)")
    active = forecasts.find_active(name)
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"a forecast is already on the table "
                   f"({active.get('forecast_id')}) — read it and discard it, "
                   f"or wait to re-forecast from a new canon week")
    ensure_publishable_409(name)
    if not engines.ENGINE_LOCK.acquire(timeout=1.0):
        raise HTTPException(
            status_code=409,
            detail="the engine home is busy — a week run is already in "
                   "flight (one engine home, one scratch state at a time)")
    engines.ENGINE_LOCK.release()

    def job(log):
        # same race re-checks as every staging job (the campaign lock this
        # job runs under is submit-time; never re-acquire it in here)
        racing = proposals.find_pending(name)
        if racing:
            raise RuntimeError(
                f"a proposal appeared while this run was starting "
                f"({racing.get('proposal_id')}) — nothing was run or spent")
        racing = forecasts.find_active(name)
        if racing:
            raise RuntimeError(
                f"a forecast appeared while this run was starting "
                f"({racing.get('forecast_id')}) — nothing was run or spent")
        write_week_flag(name)
        log(f"forecast flagged ({engines.WEEK_FLAG_NAME}) — a boot after a "
            "crash restores the engine home from its backup")
        with _engine_lock_logged(log, "a week run or forecast"):
            return _run_forecast(name, d, body.weeks, log)

    return submit_campaign_job("forecast", name, job)


@router.get("/api/campaign/{name}/simulation/forecast")
def get_forecast(name: str):
    """The one active forecast (or {\"forecast\": null}), with every
    projected week's chronicle and diff. Reads disk, so it survives a
    restart."""
    require_campaign(name)
    active = forecasts.find_active(name)
    if not active:
        return {"forecast": None}
    fid = str(active.get("forecast_id"))
    try:
        bundle = forecasts.load(name, fid)
    except forecasts.ForecastError as e:
        raise HTTPException(
            status_code=422,
            detail=f"the forecast bundle {fid} is damaged and cannot be "
                   f"shown ({e}) — discard it to clear the slot")
    return {
        "forecast": {
            "forecast_id": fid,
            "status": active.get("status"),
            "base_week": active.get("base_week"),
            "created": active.get("created"),
            "run_record": active.get("run_record") or {},
        },
        "weeks": bundle["weeks"],
    }


@router.post("/api/campaign/{name}/simulation/forecast/{fid}/discard")
def discard_forecast(name: str, fid: str):
    """Discard a forecast: flips the bundle's status only. The projection
    was never canon; the bundle stays on disk as a receipt. Discarded
    projections are never fed into future prompts."""
    require_campaign(name)
    with campaign_lock_nowait(name, "discarding the forecast"):
        try:
            forecasts.set_status(name, fid, "discarded",
                                 decided_note="discarded by the DM after "
                                              "reading; nothing was ever "
                                              "canon")
        except forecasts.ForecastError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"discarded": fid}


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
    # why.py reads ONE state file: point --state at the campaign's canon —
    # no copy-in, no engine lock (views run while a week runs)
    try:
        rc, lines = engines.run_py(
            engines.AGENTIC_SIMULATOR, "src/why.py",
            str(body.week), body.faction, "--json",
            "--state", str(spine.campaign_dir(name)
                           / "simulation" / "state.yaml"),
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
    previous state first, and clearing the home's run OUTPUTS (chronicles,
    replay trails). The home is shared scratch: leftover projection files
    from a discarded forecast would collide with a real week's chronicle
    names (-2 suffixes) and a stale replay/week-N.yaml would ride into an
    unrelated campaign's proposal as its replay trail."""
    src = spine.campaign_dir(name) / "simulation" / "state.yaml"
    engine_state = engines.engine_state_path()
    engine_state.parent.mkdir(parents=True, exist_ok=True)
    if engine_state.is_file():
        engines.copy_file(engine_state, engines.engine_state_backup_path())
    chronicle_dir = engines.engine_chronicle_dir()
    if chronicle_dir.is_dir():
        for p in chronicle_dir.glob("*.md"):
            p.unlink(missing_ok=True)
    replay_dir = engines.engine_replay_path(0).parent
    if replay_dir.is_dir():
        for p in replay_dir.glob("week-*.yaml"):
            p.unlink(missing_ok=True)
    engines.copy_file(src, engine_state)
