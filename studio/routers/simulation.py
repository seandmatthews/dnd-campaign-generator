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

Layout: the accept/discard SERVICE and the staging helpers live in
studio/proposal_flows.py (typed errors — the CLI reuses them without
touching FastAPI); the engine-home machinery (copy-in, week flag, the
week/forecast run flows, chronicle meta over the home's files) lives in
studio/engines.py; week-entry derivation lives in studio/timeline.py; the
create-form schema validation lives in spine.validate_faction_docs. This
file is routes and request/response shapes only.
"""

import json
from pathlib import Path

import canon
import engines
import forecasts
import jobs
import llm_client
import proposal_flows
import proposals
import simdesign
import spine
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool, field_validator

from routers.common import (
    blocked_by_line,
    blocking_findings,
    campaign_lock_nowait,
    ensure_publishable_409,
    pending_proposal_409,
    require_campaign,
    store_errors,
    strict_confirm,
    submit_campaign_job,
)

router = APIRouter()


def _auto_accept_proposal(name: str, pid: str, findings: list, log) -> dict:
    """The auto-accept half of a staging job (ticket 02): the job itself
    accepts the proposal it just staged, through the SAME accept path the
    button uses (same publication transactions, receipts, snapshots),
    recorded in the transaction as auto: true — the job is the approver.
    Error-level findings in the bundle block it: the proposal stays
    pending, as today. The caller already holds the campaign lock (the
    staging job runs under it), which is why this is accept_locked. An
    accept failure never fails the run: the staged proposal is the
    deliverable, and the note says so."""
    blockers = blocking_findings(findings)
    if blockers:
        log(f"auto-accept blocked: {len(blockers)} error-level finding(s) — "
            "the proposal stays pending for review")
        return {"auto_accept": "blocked", "blocking_findings": blockers}
    log("auto-accepting the staged proposal (the job is the approver; "
        "recorded as auto in the publication)")
    try:
        out = proposal_flows.accept_locked(name, pid, auto=True)
    except proposal_flows.ProposalFlowError as e:
        log(f"auto-accept failed — the proposal stays pending for review: "
            f"{e.detail}")
        return {"auto_accept": "error", "auto_accept_error": e.detail}
    log("auto-accepted: the proposal is published (see the receipt)")
    return {"auto_accept": "approved", "accept_result": out}


class WeekBody(BaseModel):
    # StrictBool: the string "true" must not pass the expensive-button gate
    confirm: StrictBool
    # opt-in auto-accept (ticket 02): publish the week without reading it
    # first. StrictBool + default off — reading the chronicle is the point
    # of the feature, so the checkbox is loud and the default is no.
    auto_accept: StrictBool = False
    _confirm_is_true = strict_confirm(
        "this runs a real week: it spends API credits and advances the "
        "canon clock")


class ForecastBody(BaseModel):
    """A forecast runs the real engine forward N weeks on scratch data and
    stages a projection the DM reads and discards — never canon, never
    occupying the pending-proposal slot. N× a week's API spend."""
    confirm: StrictBool
    weeks: int = 2
    _confirm_is_true = strict_confirm(
        "this runs a real forecast: it spends N weeks of API credits")

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


class PlaceBody(BaseModel):
    """An extra place factions can belong to — another burg, a town, the
    wider world. The primary city arrives through `city` and always leads
    the places list; `places` carries the rest."""
    name: str
    kind: str = "burg"
    population: int | None = None
    districts: list[str] = []


class FactionBody(BaseModel):
    name: str
    kind: str = ""
    goal: str
    resources: dict[str, str] = {}
    # a place NAME (matched case-insensitively; blank = the primary city) —
    # resolved to the place's id for state.yaml, so a world-tier faction can
    # be born in the sandbox instead of via a hand edit
    place: str = ""


class SessionEffectBody(BaseModel):
    """One structured consequence of play (Phase 3): typed, reviewed,
    deduplicated by stable id. `resource`/`field` choose the set_field vs
    set_resource shape; expected_before is the precondition the DM saw.
    Project effects (set_project_progress / set_project_status) key on
    project_id instead of faction_id."""
    type: str
    faction_id: str | None = None
    # int for project progress counters, text for everything else — the
    # engine's validator is the range/type authority
    after: str | int
    reason: str
    id: str | None = None
    resource: str | None = None
    field: str | None = None
    expected_before: str | None = None
    week: int | None = None
    project_id: str | None = None


class SessionEventBody(BaseModel):
    actor: str = "world"
    text: str
    # optional explicit WHERE (place ids) — validated by the engine against
    # the state's places; never inferred from the actor's current place
    place_ids: list[str] | None = None


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
    # chronicle and faction views read the places and crash after the spend
    city: CityBody | None = None
    # extra places beyond the city (other burgs, the wider world) — a
    # sandbox with any of these gets the engine's multi-place `places`
    # schema instead of the legacy single-city shape
    places: list[PlaceBody] = []
    factions: list[FactionBody]
    force: StrictBool = False


_PREMISE_MAX = 600


def _validated_faction_doc(factions: list[FactionBody]) -> list[dict]:
    """pydantic bodies -> spine.validate_faction_docs (the schema validator
    lives with the schema; CampaignError maps to 422 here)."""
    plain = [{"name": f.name, "goal": f.goal, "kind": f.kind,
              "resources": f.resources, "place": f.place}
             for f in factions]
    with store_errors(spine.CampaignError):
        return spine.validate_faction_docs(plain)


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

    factions = _validated_faction_doc(body.factions)

    # Multi-place: the city is always the primary place (factions without a
    # `place` live here); extras follow in the order given. A sandbox with
    # no extras keeps the legacy single-`city` state shape; one with extras
    # gets the engine's `places` list.
    primary_id = spine.slugify(city_name)
    if not primary_id:
        raise HTTPException(status_code=422,
                            detail=f"city name '{city_name}' has no usable "
                                   "id characters")
    places: list[dict] = [dict(city, id=primary_id, kind="city")]
    names_by_id: dict[str, str] = {primary_id: city_name}
    for p in body.places:
        pname = (p.name or "").strip()
        if not pname:
            raise HTTPException(status_code=422,
                                detail="every place needs a name")
        pid = spine.slugify(pname)
        if not pid:
            raise HTTPException(status_code=422,
                                detail=f"place name '{pname}' has no usable "
                                       "id characters")
        if pid in names_by_id:
            raise HTTPException(
                status_code=422,
                detail=f"place '{pname}' duplicates "
                       f"'{names_by_id[pid]}' — places need distinct names")
        entry: dict = {"id": pid, "name": pname,
                       "kind": (p.kind or "burg").strip() or "burg"}
        if p.population is not None:
            if p.population <= 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"place '{pname}': population must be positive")
            entry["population"] = p.population
        districts = [d.strip() for d in (p.districts or []) if d.strip()]
        if districts:
            entry["districts"] = districts
        places.append(entry)
        names_by_id[pid] = pname

    # resolve each faction's place name to the place id; blank = primary.
    # Names are matched through the same slug the id carries, so "The Weir"
    # and "the weir" both find it — and a typo gets the valid list, not a
    # state the engine refuses after the spend.
    valid_places = ", ".join(names_by_id.values())
    for f in factions:
        want = (f.get("place") or "").strip()
        if not want:
            f.pop("place", None)
            continue
        pid = spine.slugify(want)
        if pid not in names_by_id:
            raise HTTPException(
                status_code=422,
                detail=f"faction '{f.get('name')}' names place '{want}', "
                       f"which is not one of this sandbox's places: "
                       f"{valid_places}")
        f["place"] = pid

    doc["factions"] = factions
    if len(places) > 1:
        doc["places"] = places
    else:
        doc["city"] = city
    doc["events"] = []
    doc["rumors"] = []

    replaced = state_file.is_file()
    # Lock across write+snapshot: a concurrent week job would copy its
    # premise-derived state back over the new sandbox and lose the replace.
    # Fast-fail — a blocked create reads as a hung page.
    with campaign_lock_nowait(name, "creating the simulation sandbox"):
        spine.write_state(name, doc)
        snap = canon.snapshot_result(
            name, f"campaign/{name}: simulation "
                  f"{'replaced' if replaced else 'created'}")
    return {"ok": True, "week": 0,
            "factions": [f["id"] for f in doc["factions"]],
            "snapshot": snap}


class DesignBody(BaseModel):
    premise: str
    places: list[str] = []


@router.post("/api/campaign/{name}/simulation/design", status_code=202)
def post_simulation_design(name: str, body: DesignBody):
    """Premise -> a designed sandbox draft (ONE metered LLM pass, re-asked
    once when the reply fails validation, then a failed job carrying the
    validator's words — the world-options posture). The design PREFILLS
    the Create a simulation form; nothing is staged and
    simulation/state.yaml is not touched — Create stays the accept, on
    the unchanged free path. Secrets come back separately as proposed
    DM-only knowledge drafts and never ride in the faction docs.
    Fed-or-refused: with no world file and no named places there is
    nothing to ground the design in (422, synchronously — the cheap
    refusals never cost a queued job).

    A QUEUED JOB on the no-lock lane (ticket 42, superseding ticket 15's
    synchronous posture: with a real provider the pass is minutes long,
    and a reload mid-run used to destroy a paid result). The job queues
    through the campaign's FIFO (kind `sim-design`, queue strip names it,
    cancellable while queued) but takes the campaign lock at no point —
    the design writes nothing and must never block, nor be blocked by, a
    canon writer. The result lands in the job record, so a reload costs
    the WAIT, never the spend: the design block restores a finished pass
    from the record on next load."""
    require_campaign(name)
    try:
        simdesign.grounding(name, body.premise, body.places)
    except simdesign.FedOrRefused as e:
        raise HTTPException(status_code=422, detail=str(e))

    def job(log):
        try:
            out = simdesign.design_simulation(name, body.premise,
                                              body.places, log=log)
        except simdesign.FedOrRefused as e:  # raced ground truth since submit
            raise RuntimeError(f"refused: {e}") from e
        except simdesign.DesignError as e:
            raise RuntimeError(str(e)) from e
        except llm_client.BudgetExceeded as e:
            raise RuntimeError(str(e)) from e
        except RuntimeError as e:  # llm_client's readable provider errors
            raise RuntimeError(str(e)) from e
        # the requested grounding places ride in the record: the design
        # block's reload restore prefills the city/other-places fields
        # from them (the record is the only copy after a reload)
        out["places"] = body.places or []
        return out

    return submit_campaign_job("sim-design", name, job, take_lock=False)


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
        "places": spine.state_places(state),
        "factions": factions,
        "projects": state.get("projects") or [],
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
    # no copy-in: views.py reads the campaign's own state via --state (see
    # module note above the get_view routes)
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
        raise pending_proposal_409(name, pending, "generating another")
    ensure_publishable_409(name)  # a parked conflict blocks campaign writes
    auto_accept = bool(body.auto_accept)
    # fast-fail courtesy for CROSS-campaign contention: the engine home is
    # shared across campaigns, and the job's blocking acquire would queue a
    # this-campaign week behind another campaign's half-hour run with no
    # signal at all. That contention cannot join the queue (the queue is
    # per-campaign; the engine home is global), so the 409 carries the
    # honest wait estimate from the rolling history. SAME-campaign
    # contention is the queue's whole reason to exist (GM ruling, ticket
    # 24: weeks chain back-to-back behind the running one; the in-job
    # re-checks below guard correctness) — the lock records its holder
    # precisely so this probe can tell the two apart.
    if not engines.ENGINE_LOCK.acquire(timeout=1.0):
        if engines.ENGINE_LOCK.holder != name:
            line = blocked_by_line(
                jobs.running_jobs(kinds=("sim-week", "forecast")))
            raise HTTPException(
                status_code=409,
                detail="the engine home is busy — a week run is already in "
                       "flight (one engine home, one week at a time)"
                       + (line + "." if line else ""))
        # the holder is this campaign's own week/forecast: queue behind it
    else:
        engines.ENGINE_LOCK.release()  # probe only — the job takes it for real

    def job(log):
        # the pre-route check raced: re-check under the campaign lock this
        # job runs in — two quick requests must never stage two pendings
        racing = proposals.find_pending(name)
        if racing:
            raise RuntimeError(
                f"a proposal appeared while this run was starting "
                f"({racing.get('proposal_id')}) — nothing was run or spent; "
                "accept or discard it first")
        # Lock order: campaign lock (held by this job) outside, engine
        # lock inside — views/why take only the engine lock. The week flag
        # is written only once the engine lock is HELD (ticket 26): a
        # queued run whose bounded wait gives up must not leave a flag
        # behind (GET status would report a week in flight until reboot),
        # and a run still waiting must not overwrite the running job's
        # attribution (boot restore would blame the wrong campaign).
        # run_week_proposal clears it in its finally on every exit path.
        with engines.engine_lock_logged(log, "a week run or forecast",
                                        holder=name):
            engines.write_week_flag(name)
            log(f"week run flagged ({engines.WEEK_FLAG_NAME}) — a boot after "
                "a crash restores the engine home from its backup")
            result = engines.run_week_proposal(name, d, log)
        if auto_accept:
            # loud, and only behind the explicit checkbox: publish the
            # week without reading it first. The engine already refuses
            # invalid candidates, git keeps the undo, and the receipt
            # records the auto-accept.
            log("AUTO-ACCEPT is ON: the week will be published the moment "
                "it stages, without reading the chronicle first")
            bundle = proposals.load(name, result["proposal_id"])
            result.update(_auto_accept_proposal(
                name, result["proposal_id"], bundle.get("findings") or [],
                log))
        return result

    return submit_campaign_job("sim-week", name, job)


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
            # advisory, deliberately not enforced at accept (unlike review
            # fingerprints): lore-page churn does not stale a week. UI
            # display only.
            "input_set_revisions": pending.get("input_set_revisions") or {},
        },
        "diff": bundle["diff"],
        "entries": bundle["entries"],
        "updates": bundle["updates"],
        "changes": bundle.get("changes") or [],
        "findings": bundle.get("findings") or [],
        # session_changes after-images (knowledge/disclosure/decision) —
        # the Sessions review UI reads these to show what acceptance will
        # write beyond state
        "extra": bundle.get("extra") or {},
        "chronicles": [
            {"file": f"simulation/chronicle/{fn}", "title": fn,
             "markdown": text}
            for fn, text in bundle["chronicles"].items()],
    }


@router.post("/api/campaign/{name}/simulation/proposal/{pid}/accept")
def accept_proposal(name: str, pid: str):
    """Publish the WHOLE pending proposal (service: proposal_flows.accept —
    idempotent, transactional, revision-validated; ProposalFlowError maps
    to its status via the server handler)."""
    require_campaign(name)
    return proposal_flows.accept(name, pid)


@router.post("/api/campaign/{name}/simulation/proposal/{pid}/discard")
def discard_proposal(name: str, pid: str):
    """Discard a proposal: flips the bundle's status only (service:
    proposal_flows.discard). The live campaign is untouched — generation
    staged everything — and the bundle stays on disk as an audit receipt."""
    require_campaign(name)
    return proposal_flows.discard(name, pid)


# ------------------------------------------------------- session ingestion


def _effect_id(effect: dict) -> str:
    """Stable effect id: content-derived when the DM did not supply one, so
    re-submitting the same session cannot mint a second identity."""
    return proposal_flows.effect_id(effect)


@router.post("/api/campaign/{name}/simulation/session")
def post_session(name: str, body: SessionBody):
    """Record what happened at the table: history bullets + structured
    effects, validated by the ENGINE's own code (CLI subprocess — no
    imports across projects) and staged as a pending proposal. Nothing in
    the campaign changes until the proposal is accepted.

    Synchronous (200, not a job): the engine validation usually answers in
    seconds; the worst case is two bounded subprocess calls (~2 min), which
    a browser waits out fine but a 202-implied async contract would not —
    there is no job to poll, so the status code must not promise one."""
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign — create the "
                   "sandbox first")
    pending = proposals.find_pending(name)
    if pending:
        raise pending_proposal_409(name, pending, "recording a session")
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
    # path uses (list-form args; the parent path contained `&` until
    # 2026-10-04)
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="studio-session-"))
    try:
        record_path = tmp_dir / "session.yaml"
        record_path.write_text(spine.dump_yaml(record), encoding="utf-8")

        # capture base + revision TOGETHER under the lock — the after-image
        # is judged against these exact bytes. Reading the revision after the
        # engine ran let an accept land in between and the publish silently
        # overwrite the accepted week.
        with campaign_lock_nowait(name, "recording the session"):
            state_path = d / "simulation" / "state.yaml"
            state_text, base_revision = proposal_flows.capture_state(state_path)
        copy_path = tmp_dir / "state-copy.yaml"
        # EXACT bytes: text mode would re-translate \n and turn the CRLF a
        # Windows state.yaml carries into \r\r\n — YAML folds one break in
        # a plain scalar to a space but two to a literal newline, so every
        # dumper-wrapped value (width 100) would silently mutate in what
        # the engine parses (and accept would publish that corruption)
        copy_path.write_bytes(state_text.encode("utf-8"))

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
        # request thread didn't hold it (two tabs, session + week together),
        # and the state the engine validated against must still be canon —
        # staging a stale after-image would publish over the change
        proposal_flows.recheck_for_staging(
            name, base_revision=base_revision, what="this session")
        pid = proposals.create(
            name, kind="session_effects",
            base_week=spine.state_clock_week(base_doc), proposed_week=spine.state_clock_week(base_doc),
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


# ------------------------------------------------------- faction projects


class ProjectOpBody(BaseModel):
    """One typed DM operation: create | edit | pause | resume | abandon.
    Unknown fields fail validation in the ENGINE (the CLI is the single
    rules implementation — the studio never re-implements project rules)."""
    op: str
    reason: str
    id: str | None = None
    project_id: str | None = None
    project: dict | None = None
    set: dict | None = None


class ProjectPrepareBody(BaseModel):
    operations: list[ProjectOpBody]


def _projects_report(name: str) -> dict:
    """The engine's own --report: the DM project list with computed
    requirement/completion findings (one fast subprocess, no LLM, no lock —
    the same read-only pattern as views/why)."""
    rc, lines = engines.run_py(
        engines.AGENTIC_SIMULATOR, "src/projects.py", "--report",
        "--state", str(spine.campaign_dir(name) / "simulation" / "state.yaml"),
        timeout=60)
    if rc != 0:
        raise HTTPException(
            status_code=502,
            detail=f"projects engine failed (rc={rc})"
                   + (f": {' | '.join(lines[-3:])}" if lines else ""))
    try:
        doc = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as e:
        raise HTTPException(status_code=502,
                            detail=f"projects engine printed bad YAML: {e}")
    if not isinstance(doc, dict):
        raise HTTPException(status_code=502,
                            detail="projects engine output was not a mapping")
    return doc


@router.get("/api/campaign/{name}/simulation/projects")
def get_projects(name: str):
    require_campaign(name)
    if not (spine.campaign_dir(name) / "simulation" / "state.yaml").is_file():
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign — create the "
                   "sandbox first")
    return _projects_report(name)


@router.get("/api/campaign/{name}/simulation/projects/{project_id}")
def get_project(name: str, project_id: str):
    """One project's detail plus its sourced event history — every event
    and completion this project id carries, newest last."""
    require_campaign(name)
    report = _projects_report(name)
    project = next((p for p in (report.get("projects") or [])
                    if p.get("id") == project_id), None)
    if project is None:
        raise HTTPException(status_code=404,
                            detail=f"no such project: {project_id}")
    state = spine.load_state(name) or {}
    history = [e for e in (state.get("events") or [])
               if isinstance(e, dict) and e.get("project_id") == project_id]
    history.sort(key=lambda e: (e.get("week") is None, e.get("week") or 0))
    return {"project": project, "history": history,
            "week": report.get("week")}


@router.post("/api/campaign/{name}/simulation/projects/prepare")
def prepare_projects(name: str, body: ProjectPrepareBody):
    """Stage DM project operations (create/edit/pause/resume/abandon) as a
    `project_changes` proposal — the engine validates against a captured
    copy and produces the after-image; nothing in the campaign changes
    until the proposal is accepted. Synchronous 200: two bounded
    subprocess calls, no LLM, no engine-lock critical section."""
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(status_code=404,
                            detail="no simulation/state.yaml in this campaign")
    pending = proposals.find_pending(name)
    if pending:
        raise pending_proposal_409(name, pending, "preparing project changes")
    ensure_publishable_409(name)
    if not body.operations:
        raise HTTPException(status_code=422,
                            detail="nothing to prepare — add an operation")
    operations = []
    for op in body.operations:
        record = {k: v for k, v in op.model_dump().items() if v is not None}
        operations.append(record)

    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="studio-projects-"))
    try:
        # capture base + revision together under the lock (same rule as the
        # session flow: one read, or a hand edit lands between text and hash)
        with campaign_lock_nowait(name, "preparing project changes"):
            state_path = d / "simulation" / "state.yaml"
            state_text, base_revision = proposal_flows.capture_state(state_path)
        copy_path = tmp_dir / "state-copy.yaml"
        # EXACT bytes — same rule as the session flow: the copy must be
        # byte-identical to what was captured, or CRLF doubling mutates
        # every wrapped scalar the engine validates against
        copy_path.write_bytes(state_text.encode("utf-8"))
        changes_path = tmp_dir / "changes.yaml"
        changes_path.write_text(
            spine.dump_yaml({"operations": operations}), encoding="utf-8")

        # the engine's --preview validates AND produces the candidate in one
        # run; rc 1 means the batch is refused whole (no partial edits)
        rc, lines = engines.run_py(
            engines.AGENTIC_SIMULATOR, "src/projects.py",
            "--preview", str(changes_path),
            "--state", str(copy_path),
            "--out", str(tmp_dir / "preview.yaml"), timeout=60)
        if rc != 0:
            detail = " | ".join(lines[-5:]) or "no output"
            raise HTTPException(
                status_code=422,
                detail=f"the engine refused these project changes: {detail}")
        preview = yaml.safe_load(
            (tmp_dir / "preview.yaml").read_text(encoding="utf-8")) or {}
        norm_ops = preview.get("operations") or []
        findings = preview.get("findings") or []
        candidate_doc = preview.get("state") or {}
        if not candidate_doc:
            raise RuntimeError("the engine's preview carried no candidate "
                               "state — nothing staged")
        candidate_text = spine.dump_yaml(candidate_doc)

        # the state the engine validated against must still be canon
        with campaign_lock_nowait(name, "staging the project proposal"):
            proposal_flows.recheck_for_staging(
                name, base_revision=base_revision,
                what="these project changes")
            pid = proposals.create(
                name, kind="project_changes",
                base_week=spine.state_clock_week(candidate_doc),
                proposed_week=spine.state_clock_week(candidate_doc),
                base_revisions={"simulation/state.yaml": base_revision},
                input_set_revisions={},
                artifacts={"state": candidate_text, "chronicles": {},
                           "entries": [], "changes": norm_ops,
                           "findings": list(findings)},
                run_record={"kind": "project_changes",
                            "engine": "agentic-simulator/src/projects.py "
                                      "--preview"},
                before_state=yaml.safe_load(state_text) or {})
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    diff = proposals.diff_states(yaml.safe_load(state_text) or {},
                                 candidate_doc, [])
    return {
        "proposal_id": pid, "status": "pending", "kind": "project_changes",
        "operations": len(norm_ops), "findings": list(findings),
        "diff": diff,
    }


class GoalReviewBody(BaseModel):
    """Opt-in auto-accept (ticket 02): the staged goal_updates proposal is
    accepted by the job itself the moment it lands — unless an error-level
    finding blocks it (then it stays pending, as today). Default off."""
    auto_accept: bool = False


# one bounded LLM pass + the effects CLI runs. complete()'s honest worst
# case is 3 attempts x 300s x 3 passes (base + two length extensions) plus
# backoff ~= 2700s: a slow-but-working provider must not get the tree
# killed and every retry re-spent, so the watchdog sits just above it.
_GOAL_REVIEW_TIMEOUT = 2800


@router.post("/api/campaign/{name}/simulation/goal-review", status_code=202)
def post_goal_review(name: str, body: GoalReviewBody | None = None):
    """Goal review: the sim proposes faction goal updates grounded in the
    ledger (recorded weeks, rumors, canon deadlines), staged as a
    `goal_updates` proposal the DM reviews exactly like a week. Runs as a
    job (one bounded LLM pass in the engine); nothing changes until the
    proposal is accepted — or auto-accepted (opt-in, ticket 02)."""
    auto_accept = bool(body.auto_accept) if body is not None else False
    d = require_campaign(name)
    if not (d / "simulation" / "state.yaml").is_file():
        raise HTTPException(
            status_code=404,
            detail="no simulation/state.yaml in this campaign — create the "
                   "sandbox first ('Create a simulation' on this tab)")
    pending = proposals.find_pending(name)
    if pending:
        raise pending_proposal_409(name, pending, "running a goal review")
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
        state_text, base_revision = proposal_flows.capture_state(state_path)
        base_doc = yaml.safe_load(state_text) or {}

        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="studio-goals-"))
        try:
            copy_path = tmp_dir / "state.yaml"
            # EXACT bytes — same rule as the session flow: the copy must
            # be byte-identical to what was captured (no CRLF doubling)
            copy_path.write_bytes(state_text.encode("utf-8"))
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
                    % (rc, engines.tail_detail(lines, 10)))
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
            source = "goal-review:week-" + str(spine.state_clock_week(base_doc))
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
            record_path.write_text(spine.dump_yaml(record), encoding="utf-8")
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
        proposal_flows.recheck_for_staging(
            name, base_revision=base_revision, what="the goal review")
        run_record = {**engines.run_record_identity(
                          "agentic-simulator/src/goals.py --state"),
                      "kind": "goal-review"}
        pid = proposals.create(
            name, kind="goal_updates",
            base_week=spine.state_clock_week(base_doc),
            proposed_week=spine.state_clock_week(base_doc),
            base_revisions={"simulation/state.yaml": base_revision},
            input_set_revisions={},
            artifacts={"state": after_text, "chronicles": {}, "entries": [],
                       "updates": found, "findings": list(findings)},
            run_record=run_record, before_state=base_doc)
        diff = proposals.diff_states(base_doc, after_doc, [])
        log(f"staged {len(found)} proposed goal update(s) ({pid}) — nothing "
            "in the campaign has changed; review, then accept or discard")
        result = {"proposal_id": pid, "status": "pending",
                  "kind": "goal_updates", "proposed": len(found),
                  "findings": list(findings), "staged": True, "diff": diff}
        if auto_accept:
            result.update(_auto_accept_proposal(
                name, pid, list(findings), log))
        return result

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
        raise pending_proposal_409(
            name, pending,
            "forecasting (the forecast reads the canon the proposal would "
            "change)")
    active = forecasts.find_active(name)
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"a forecast is already on the table "
                   f"({active.get('forecast_id')}) — read it and discard it, "
                   f"or wait to re-forecast from a new canon week")
    ensure_publishable_409(name)
    # same cross-campaign contention as the week: cannot join a per-
    # campaign queue, so the 409 says what is running and how long it takes.
    # A SAME-campaign holder queues (GM ruling, ticket 24 — a forecast
    # chains behind this campaign's running week); the in-job re-checks
    # below guard correctness.
    if not engines.ENGINE_LOCK.acquire(timeout=1.0):
        if engines.ENGINE_LOCK.holder != name:
            line = blocked_by_line(
                jobs.running_jobs(kinds=("sim-week", "forecast")))
            raise HTTPException(
                status_code=409,
                detail="the engine home is busy — a week run is already in "
                       "flight (one engine home, one scratch state at a time)"
                       + (line + "." if line else ""))
        # the holder is this campaign's own week/forecast: queue behind it
    else:
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
        # flag AFTER the engine lock is held, same rule as the week job
        # (ticket 26): no flag while queued, no overwritten attribution,
        # and run_forecast's finally always covers the clear.
        with engines.engine_lock_logged(log, "a week run or forecast",
                                        holder=name):
            engines.write_week_flag(name)
            log(f"forecast flagged ({engines.WEEK_FLAG_NAME}) — a boot after "
                "a crash restores the engine home from its backup")
            return engines.run_forecast(name, d, body.weeks, log)

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
    with campaign_lock_nowait(name, "discarding the forecast"), \
            store_errors(conflict=(forecasts.ForecastError,)):
        forecasts.set_status(
            name, fid, "discarded",
            decided_note="discarded by the DM after "
                         "reading; nothing was ever "
                         "canon")
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
        if "no recorded event" in out.lower():
            # why.py exits 1 with that line when the faction has no history
            # at this week — a normal state for week 0, not an engine crash
            raise HTTPException(
                status_code=404,
                detail="no recorded events for this faction in week "
                       f"{body.week} yet — run or accept a week first")
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
