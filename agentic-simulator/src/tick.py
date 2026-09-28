"""The weekly tick.

Modes:
  python src/tick.py            # inspect: print each faction's fog-of-war view
  python src/tick.py --live     # run the week: scheduler gate, LLM proposals
                                # (+ consistency warnings), dice, state diff,
                                # rumor spread, chronicle. Advances the clock —
                                # review the diff it leaves in world/state.yaml.
  python src/tick.py --why W F  # causality: why faction F's week-W event happened
  python src/tick.py --replay F # re-resolve a saved week (world/replay/week-N.yaml)
                                # from its stored seed + proposals, no network:
                                # exit 0 when the resolver reproduces the record

Stages in --live (one sentence each):
  scheduler   — you act if the world touched you last week or you are at
                someone's throat; otherwise you hold
  rumors      — every retelling drops fidelity one grade; dead rumors stop moving
  consistency — a proposal may only assert facts its faction has a path to know
                (advisory warnings, printed, never auto-rejected)

Mechanical consequence rules (one sentence each, applied by code):
  - contested loss, or margin <= -5: the faction's strongest committed
    resource steps down one grade (high->medium->low->none)
  - win with margin >= +5: the strongest committed resource steps up one
    grade (capped at high)
  - commit nothing, nothing steps — standing you did not stake is not won
    or lost
  - anything else: no resource change.
One proposal, one consequence: a faction caught in several contests gets a
single event and at most one resource step, judged by its worst margin. Every
outcome still reaches the chronicle table.
Semantic consequences (knowledge gained, dispositions shifted) are NOT
applied by code — they land in the chronicle for human review (M4's loop).

Robustness: a faction whose LLM proposal fails holds instead of aborting
the week; per-run budget exhaustion (LLM_MAX_CALLS) is different — it
aborts the whole week BEFORE anything is written (exit 3, no state, no
chronicle, no replay), because an all-holds week saved as canon would be
indistinguishable from a quiet week. Every metered POST attempt spends the
ceiling, retries included, so a hard-failing provider can exhaust the
budget with zero proposals — that aborts too (raise LLM_MAX_CALLS and
retry). A narrator failure leaves the ledger as the record. Dice roll from
a per-week seeded RNG recorded in meta.rng_seeds — a week is replayable by
seeding resolve with that number.
"""

import copy
import hashlib
import pathlib
import random
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import llm
import projects
import rumors
import scheduler
import consistency
import resolve
from store import STATE_FILE, load_state, save_state, validate_state, places_of
from why import why_cli
from views import faction_view
from resolve import resolve_tick

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE_FILE = ROOT / "agents" / "faction.md"
CHRONICLE_DIR = ROOT / "chronicle"

_LEVELS = ("none", "low", "medium", "high")


class WeekAborted(RuntimeError):
    """The week stopped before any canon write — currently only per-run
    budget exhaustion. main() maps it to exit 3; the studio refuses to
    stage a proposal from a non-zero tick run."""


def step_resource(resources, committed, direction):
    """Shift the strongest resource the proposal COMMITTED one grade;
    direction is +1 / -1. Commit nothing, nothing steps."""
    staked = [
        k for k, v in (committed or {}).items()
        if k in resources and v in _LEVELS and v != "none"
    ]
    if not staked:
        return
    strongest = max(staked, key=lambda k: _LEVELS.index(committed[k]))
    i = _LEVELS.index(resources[strongest])
    resources[strongest] = _LEVELS[max(0, min(len(_LEVELS) - 1, i + direction))]


def event_record(state, faction_id, outcome):
    """The canonical event for one resolved proposal: a human-readable `text`
    line plus the structured fields (`action`, `outcome`, `vs`, `contested`,
    `margin`) that the scheduler, why-chain, and rumor mill read — machines
    never re-parse the prose. Project intents ride along (`intent`,
    `project_id`) plus the provenance pair (`event_id`, `action_id`) the
    studio's project history joins on; `public_action`, when present, is
    the rumor-safe claim (a restricted project's work never names it)."""
    succeeded = "succeeded" if outcome["success"] else "failed"
    vs = outcome.get("vs")
    text = (f"{outcome['action']} — {succeeded}"
            + (f" (contested vs {vs})" if outcome.get("contested") else "")
            + f", margin {outcome['margin']:+d}")
    week = state["meta"]["clock"]["week"]
    n = sum(1 for ev in state.get("events", [])
            if ev.get("week") == week and ev.get("actor") == faction_id)
    ev = {
        "event_id": f"ev-{week:04d}-{faction_id}-{n + 1}",
        "week": week,
        "actor": faction_id,
        "text": text,
        "action": outcome["action"],
        "outcome": succeeded,
        "contested": bool(outcome.get("contested")),
        "margin": outcome["margin"],
    }
    if outcome.get("action_id"):
        ev["action_id"] = outcome["action_id"]
    if outcome.get("intent"):
        ev["intent"] = outcome["intent"]
    if outcome.get("project_id"):
        ev["project_id"] = outcome["project_id"]
    if outcome.get("public_action"):
        ev["public_action"] = outcome["public_action"]
    if vs:
        ev["vs"] = vs
    return ev


def apply_mechanical(state, faction_id, outcome):
    """Consequences of ONE resolved proposal, applied once: the strongest
    committed resource steps one grade on a decisive result (module
    docstring), and the event lands in state["events"]."""
    faction = next(f for f in state["factions"] if f["id"] == faction_id)
    committed = outcome.get("committed", {})
    if outcome["success"] and outcome["margin"] >= 5:
        step_resource(faction["resources"], committed, +1)
    elif (outcome.get("contested") and not outcome["success"]) or outcome["margin"] <= -5:
        step_resource(faction["resources"], committed, -1)
    state["events"].append(event_record(state, faction_id, outcome))


def apply_outcomes(state, outcomes, proposals):
    """One proposal, one consequence: an actor caught in several contests
    gets a single event and at most one resource step, judged by their
    consolidated outcome — the shared resolve.consolidate_outcomes (worst
    margin; at equal margin a loss defeats a success, so a tied defensive
    win cannot hide a tied attacking loss). Defensive outcomes — an
    inactive faction pulled into a contest by rule version 2 — apply
    nothing: the defender proposed nothing, committed nothing, and a
    defense is not an action (the attacker's own outcome carries the
    contest's consequences). The proposal's project intent rides along on
    the outcome copy so the event record can carry it."""
    consolidated = resolve.consolidate_outcomes(outcomes, proposals)
    by_actor = {p["actor"]: p for p in proposals}
    for actor, o in consolidated.items():
        p = by_actor[actor]
        o = dict(o)
        o["committed"] = p.get("resources_committed", {})
        for key in ("intent", "project_id", "public_action", "action_id"):
            if p.get(key):
                o[key] = p[key]
        apply_mechanical(state, actor, o)


def run_live(state):
    # fail before anything expensive: a hand-edited or partial state used to
    # crash AFTER the faction calls' spend (apply_mechanical's events
    # append, write_chronicle's city/faction reads) — validate_state names
    # the missing piece instead
    try:
        validate_state(state)
    except ValueError as e:
        print(f"error: {e} — fix state.yaml before running a week")
        sys.exit(2)
    project_issues = projects.find_state_issues(state)
    if project_issues:
        for issue in project_issues:
            print(f"error: {issue}")
        print("error: fix state.yaml's projects before running a week")
        sys.exit(2)
    act = scheduler.activation(state)          # "last week" = the clock as-is
    held = scheduler.holds(state, act)
    # the just-completed week, for consistency's "last week" contract — the
    # bump below makes clock.week the week being simulated, not the past one
    prev_week = state["meta"]["clock"]["week"]
    week = prev_week + 1
    state["meta"]["clock"]["week"] = week
    template = TEMPLATE_FILE.read_text(encoding="utf-8")

    # replay identity: the state the week was built on, hashed before any
    # mutation (clock bump, rumor spread, resolution)
    base_state_sha = hashlib.sha256(
        pathlib.Path(STATE_FILE).read_bytes()).hexdigest()

    seed = random.SystemRandom().getrandbits(31)
    resolve.set_rng(seed)
    state.setdefault("meta", {}).setdefault("rng_seeds", {})[str(week)] = seed
    # explicit per-run call ceiling, checked between calls: LLM_MAX_CALLS
    # when set, else a default that scales with the roster — a week is ~2
    # calls per faction (proposal + one validation retry) plus narration,
    # and every metered POST attempt spends the ceiling, retries included,
    # so the default leaves one spare attempt-set per faction (the old flat
    # 24 exhausted itself mid-week on a large roster)
    llm.begin_run(limit=llm.env_limit()
                  or max(24, 3 * len(state["factions"]) + 6))

    rumors.spread(state, state.get("rumors", []))   # rumors move before actions do

    proposals, views = [], []
    print(f"proposals for week {week}:")
    for faction in state["factions"]:
        if faction["id"] in held:
            reasons = "; ".join(act[faction["id"]]["reasons"])
            print(f"  {faction['id']:<14} HOLDS ({reasons})")
            continue
        view = faction_view(state, faction["id"])
        views.append(view)
        try:
            p = llm.propose(view, template, faction["id"])
        except llm.BudgetExceeded:
            # exhaustion is not a dead agent: swallowing it would save an
            # all-holds week as canon (exit 0, clock advanced) — a spent-out
            # run indistinguishable from a quiet one. Abort instead: nothing
            # has been written yet (clock bump and rumor spread live only in
            # memory), and the non-zero exit keeps the studio from staging
            # the half-run week as a proposal.
            usage = llm.run_usage()
            raise WeekAborted(
                f"per-run LLM ceiling reached ({usage['calls']} calls, "
                f"~{usage['tokens']} tokens metered) while proposing for "
                f"{faction['id']} — the week was NOT written: no state, no "
                "chronicle, no replay; raise LLM_MAX_CALLS and re-run"
            ) from None
        except Exception as e:  # noqa: BLE001 — one dead agent must not lose the week
            print(f"  {faction['id']:<14} HOLDS (proposal failed: {e})")
            continue
        for w in consistency.check(p, state, faction["id"], last_week=prev_week):
            print(f"  [consistency] {faction['id']}: {w['kind']} — {w['detail']}")
        # project actions are validated against the pre-week state BEFORE
        # resolution (llm.propose checked the faction's view first; this is
        # the state-truth backstop). An invalid project action is refused —
        # the faction holds — never silently resolved as an ordinary one.
        updates, pfindings = projects.validate_action(p, state, faction["id"])
        if pfindings:
            print(f"  {faction['id']:<14} HOLDS (project action refused: "
                  f"{pfindings[0]})")
            continue
        p.update(updates)
        # a restricted project's work must not announce itself: without a
        # model-supplied public_action, the event carries a project-free
        # line and the rumor mill quotes THAT (events.claim_text)
        if p.get("intent") in ("work_project", "sabotage_project") \
                and not p.get("public_action"):
            proj = projects.find_project(state, p.get("project_id"))
            if proj is not None and proj.get("visibility") != "public":
                p["public_action"] = (
                    f"{faction['id']} was seen moving against "
                    f"{proj.get('faction_id')}"
                    if p["intent"] == "sabotage_project"
                    else f"{faction['id']} attended to private business")
        proposals.append(p)
        print(f"  {faction['id']:<14} target={p['target']:<14} risk={p['risk_acceptance']:<6} {p['action']}")

    print("resolution:")
    # every proposal gets a stable action id once, before resolution: the
    # event ledger, the replay trail, and the project history join on it
    for p in proposals:
        p.setdefault("action_id", f"act-{week:04d}-{p['actor']}")
    # the resolver's exact input, frozen before it (or apply_outcomes) can
    # touch the live object — the replay stores this so a week can be
    # re-resolved AFTER the state has moved on (the next bug the old
    # hash-only scheme had: verification failed immediately post-week)
    replay_input = copy.deepcopy(state)
    outcomes = resolve_tick(proposals, state)
    for o in outcomes:
        kind = "defensive" if o.get("defensive") else \
               "CONTESTED WIN" if o["contested"] and o["success"] else \
               "CONTESTED LOSS" if o["contested"] else \
               "success" if o["success"] else "fail"
        print(f"  {o['actor']:<14} {kind:<14} margin {o['margin']:+d}")
    apply_outcomes(state, outcomes, proposals)
    project_report = projects.apply_week(state, proposals, outcomes)
    for line in projects.report_lines(project_report):
        print(f"  [projects] {line}")

    state["rumors"] = state.get("rumors", []) + rumors.spawn_from_events(state)

    # write order: the state is the canon commit point — save it first, then
    # the replay trail, then the chronicle prose. A crash after the state
    # write loses only derived artifacts (a re-run re-copies canon and
    # re-proposes); the old order (chronicle first) could leave a chronicle
    # for a week the state never recorded.
    save_state(state)
    save_replay(week, seed, proposals, outcomes, base_state_sha, replay_input,
                projects_report=project_report)
    chronicle = write_chronicle(state, week, outcomes, held,
                                projects_report=project_report)
    print(f"week {week} written (rng seed {seed}); chronicle: {chronicle.name}")


# ------------------------------------------------------------- replay trail

def save_replay(week, seed, proposals, outcomes, base_state_sha,
                base_state=None, projects_report=None):
    """Persist everything the resolver consumed — normalized proposals,
    the exact input state it resolved against, base-state identity, rule
    version, seed, and the outcomes it produced — so the week can be
    re-resolved offline (`--replay`) with no network and verified to
    produce the same result, even after the live state has moved on.

    Same-week reruns version like chronicles (week-N-2.yaml after a rewind
    and re-run) so an old chronicle's replay trail is never silently
    replaced by the new run's record. Schema 2 (base_state embedded) is
    written only when a base_state was supplied; verification against such
    a replay is self-consistency — the file attests to itself, there is no
    external hash to check. Schema 3 adds the project report (input
    counters, resulting deltas, completions, project-rule version) so
    verification recomputes the project mechanics too."""
    replay_dir = ROOT / "world" / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "rule_version": resolve.RULE_VERSION,
        "week": week,
        "seed": seed,
        "base_state_sha256": base_state_sha,
        "proposals": proposals,
        "outcomes": outcomes,
    }
    if base_state is not None:
        doc["schema_version"] = 3 if projects_report is not None else 2
        doc["base_state"] = base_state
    if projects_report is not None:
        doc["project_rule_version"] = projects.RULE_VERSION
        doc["projects"] = projects_report
    path = replay_dir / f"week-{week}.yaml"
    n = 2
    while path.exists():
        path = replay_dir / f"week-{week}-{n}.yaml"
        n += 1
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True, width=100)
    return path


def replay_verify(path):
    """Re-resolve a saved week from its replay file — fixed seed, stored
    proposals, no model — and compare against the recorded outcomes.
    Returns (ok, message); a structurally broken file is a message, never a
    traceback. A rule_version mismatch refuses the comparison: the rules
    changed, so sameness would be meaningless.

    Schema 2 replays carry the resolver's exact input state (`base_state`),
    so verification is self-contained — the live state may have moved on
    (the next week ran; a completed week ALWAYS has) and the replay still
    verifies. Schema 1 replays carried only the input state's file hash,
    which is compared against the live state file: any later change refuses
    the comparison, because the re-resolution would otherwise judge the
    outcomes against data the week never saw — 'verified' would be a lie.

    Schema 3 replays also carry the week's project report; when present,
    the project mechanics are recomputed from the same inputs and compared.
    An old replay without the section still verifies its dice — and says
    so, never claiming mechanics it never stored."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        if not isinstance(doc, dict):
            raise ValueError("not a mapping")
        missing = [k for k in ("seed", "proposals", "outcomes")
                   if k not in doc]
        if missing:
            raise ValueError(f"missing {', '.join(missing)}")
        if doc.get("rule_version") != resolve.RULE_VERSION:
            return False, (
                f"replay file is rule version {doc.get('rule_version')}, "
                f"resolver is {resolve.RULE_VERSION} — not comparable")
        base_state = doc.get("base_state")
        if isinstance(base_state, dict):
            state = copy.deepcopy(base_state)
        else:
            base_sha = doc.get("base_state_sha256")
            if base_sha:
                current_sha = hashlib.sha256(
                    pathlib.Path(STATE_FILE).read_bytes()).hexdigest()
                if current_sha != base_sha:
                    return False, (
                        f"week {doc.get('week')} cannot be verified: "
                        "world/state.yaml has changed since the week ran "
                        "(base-state hash mismatch) — the outcomes were "
                        "resolved against a state that no longer exists")
            state = load_state(STATE_FILE)
        resolve.set_rng(doc["seed"])
        recomputed = resolve_tick(doc["proposals"], state)
    except (OSError, yaml.YAMLError, KeyError, ValueError, TypeError) as e:
        return False, f"replay file is unreadable or malformed: {e}"
    if recomputed != doc["outcomes"]:
        return _divergence_message(doc, recomputed)
    project_doc = doc.get("projects")
    if not isinstance(project_doc, dict):
        return True, (f"week {doc.get('week')} replays identically (dice; "
                      "recorded before project mechanics)")
    if not isinstance(doc.get("base_state"), dict):
        return False, ("week "
                       f"{doc.get('week')} carries a project report but no "
                       "embedded base state — project mechanics cannot be "
                       "recomputed")
    try:
        pstate = copy.deepcopy(state)
        resolve.set_rng(doc["seed"])
        poutcomes = resolve_tick(doc["proposals"], pstate)
        apply_outcomes(pstate, poutcomes, doc["proposals"])
        reproject = projects.apply_week(pstate, doc["proposals"], poutcomes)
    except (KeyError, ValueError, TypeError) as e:
        return False, (f"week {doc.get('week')} project mechanics could not "
                       f"be recomputed: {e}")
    if reproject == project_doc:
        return True, f"week {doc.get('week')} replays identically (dice and projects)"
    return False, (f"week {doc.get('week')} diverged in project mechanics: "
                   f"recorded {project_doc.get('deltas')}/"
                   f"{project_doc.get('completions')} vs recomputed "
                   f"{reproject.get('deltas')}/{reproject.get('completions')}")


def _divergence_message(doc, recomputed):
    # outcomes are index-aligned by construction (same proposals, same
    # order) — diff pairwise, never positionally against a filtered list
    if len(recomputed) != len(doc["outcomes"]):
        return False, (f"week {doc.get('week')} diverged: "
                       f"{len(doc['outcomes'])} recorded outcomes vs "
                       f"{len(recomputed)} recomputed")
    diffs = [f"{r.get('actor', '?')}:{r.get('margin', 0):+d}"
             f"!={s.get('margin', 0):+d}"
             for r, s in zip(doc["outcomes"], recomputed) if r != s][:5]
    return False, f"week {doc.get('week')} diverged ({diffs})"


def _free_chronicle_path(week, slug):
    """Same-week reruns never overwrite: the first free -N suffix wins."""
    out = CHRONICLE_DIR / f"{week:04d}-{slug}.md"
    n = 2
    while out.exists():
        out = CHRONICLE_DIR / f"{week:04d}-{slug}-{n}.md"
        n += 1
    return out


def write_chronicle(state, week, outcomes, held=(), projects_report=None):
    CHRONICLE_DIR.mkdir(exist_ok=True)
    place_label = place_label_of(state)
    summary = yaml.safe_dump(
        {f["id"]: {"goal": f["goal"], "resources": f["resources"]}
         for f in state["factions"]},
        sort_keys=False, allow_unicode=True,
    )
    try:
        prose = llm.narrate_week(place_label, week, outcomes, summary)
    except Exception as e:  # noqa: BLE001 — the ledger below is the record
        print(f"[live] narration failed, chronicle carries the ledger only: {e}",
              file=sys.stderr)
        prose = ""
    title = None
    match = re.search(r"^TITLE:\s*(.+)$", prose, re.MULTILINE)
    if match:
        title = match.group(1).strip()
        prose = prose[:match.start()].rstrip()
    # cap the slug: the model was asked for 3-6 words but a disobedient
    # 150-char title line must not push the filename past Windows MAX_PATH
    # and kill the week AFTER its spend (spine caps entity stems at 80 too)
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "the week").lower()) \
        .strip("-")[:80].rstrip("-")
    out = _free_chronicle_path(week, slug or "untitled")

    table = ["", "---", "", "## Behind the screen", "",
             "| faction | action | roll+bonus | contested | margin | outcome |",
             "|---|---|---|---|---|---|"]
    for o in outcomes:
        table.append(
            f"| {o['actor']} | {o['action']} | {o['roll']}+{o['bonus']}"
            f"{' vs ' + o['vs'] if o.get('contested') else ' vs DC ' + str(o['dc'])} "
            f"| {'yes' if o.get('contested') else 'no'} | {o['margin']:+d} "
            f"| {'success' if o['success'] else 'fail'} |"
        )
    if held:
        table += ["", f"Held (scheduler): {', '.join(held)}"]
    # the DM's chronicle may carry private mechanics: the week's project
    # movement, completions, and any blocked completion findings
    project_lines = projects.report_lines(projects_report or {})
    if project_lines:
        table += ["", "## Projects", ""]
        table += [f"- {ln}" for ln in project_lines]
    out.write_text(
        f"# Week {week} — {title or place_label}\n\n{prose}\n{chr(10).join(table)}\n",
        encoding="utf-8",
    )
    return out


def place_label_of(state):
    """The chronicle/inspect label: every place's name, comma-joined —
    "Tasospon" for the legacy single-city shape, "Tasospon, Harmouth and
    the Kingdom of Skyleia" once a campaign simulates more than one."""
    names = [str(p.get("name") or "").strip() for p in places_of(state)]
    names = [n for n in names if n]
    return ", ".join(names) if names else "nowhere"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--replay" in argv:
        i = argv.index("--replay")
        if i + 1 >= len(argv):
            print("usage: python src/tick.py --replay <world/replay/week-N.yaml>",
                  file=sys.stderr)
            return 2
        ok, message = replay_verify(argv[i + 1])
        print(message)
        return 0 if ok else 1
    try:
        state = load_state()
    except yaml.YAMLError as e:
        # a corrupt state file (a hand edit, a bad merge) must not surface
        # as a raw traceback — and certainly not after a spend
        print(f"error: world/state.yaml does not parse as YAML: {e}",
              file=sys.stderr)
        return 2
    if "--live" in argv:
        try:
            run_live(state)
        except WeekAborted as e:
            print(f"WEEK ABORTED: {e}")
            return 3
        return 0
    if "--why" in argv:
        i = argv.index("--why")
        if i + 2 >= len(argv):
            print("usage: python src/tick.py --why <week> <actor>", file=sys.stderr)
            return 2
        sys.exit(why_cli(state, ["tick.py", argv[i + 1], argv[i + 2]]))
    clock = state["meta"]["clock"]
    print(f"{place_label_of(state)}, week {clock['week']} ({clock['season']})")
    print(f"Premise: {state['meta']['premise'].strip()}")
    print()
    for faction in state["factions"]:
        view = faction_view(state, faction["id"])
        print(f"--- {faction['name']} sees: ---")
        print(yaml.safe_dump(view, sort_keys=False).strip())
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
