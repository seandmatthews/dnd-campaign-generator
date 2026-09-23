"""The weekly tick.

Modes:
  python src/tick.py            # inspect: print each faction's fog-of-war view
  python src/tick.py --live     # run the week: scheduler gate, LLM proposals
                                # (+ consistency warnings), dice, state diff,
                                # rumor spread, chronicle. Advances the clock —
                                # review the diff it leaves in world/state.yaml.
  python src/tick.py --why W F  # causality: why faction F's week-W event happened

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

Robustness: a faction whose LLM proposal fails holds instead of aborting the
week; a narrator failure leaves the ledger as the record. Dice roll from a
per-week seeded RNG recorded in meta.rng_seeds — a week is replayable by
seeding resolve with that number.
"""

import pathlib
import random
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import llm
import rumors
import scheduler
import consistency
import resolve
from store import STATE_FILE, load_state, save_state
from why import why_cli
from views import faction_view
from resolve import resolve_tick

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE_FILE = ROOT / "agents" / "faction.md"
CHRONICLE_DIR = ROOT / "chronicle"

_LEVELS = ("none", "low", "medium", "high")


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
    never re-parse the prose."""
    succeeded = "succeeded" if outcome["success"] else "failed"
    vs = outcome.get("vs")
    text = (f"{outcome['action']} — {succeeded}"
            + (f" (contested vs {vs})" if outcome.get("contested") else "")
            + f", margin {outcome['margin']:+d}")
    ev = {
        "week": state["meta"]["clock"]["week"],
        "actor": faction_id,
        "text": text,
        "action": outcome["action"],
        "outcome": succeeded,
        "contested": bool(outcome.get("contested")),
        "margin": outcome["margin"],
    }
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
    gets a single event and at most one resource step, judged by their worst
    margin (ties: the outcome recorded first)."""
    by_actor = {p["actor"]: p for p in proposals}
    worst = {}
    for o in outcomes:
        cur = worst.get(o["actor"])
        if cur is None or o["margin"] < cur["margin"]:
            worst[o["actor"]] = o
    for actor, o in worst.items():
        o = dict(o)
        o["committed"] = by_actor[actor].get("resources_committed", {})
        apply_mechanical(state, actor, o)


def run_live(state):
    act = scheduler.activation(state)          # "last week" = the clock as-is
    held = scheduler.holds(state, act)
    week = state["meta"]["clock"]["week"] + 1
    state["meta"]["clock"]["week"] = week
    template = TEMPLATE_FILE.read_text(encoding="utf-8")

    seed = random.SystemRandom().getrandbits(31)
    resolve.set_rng(seed)
    state.setdefault("meta", {}).setdefault("rng_seeds", {})[str(week)] = seed

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
        except Exception as e:  # noqa: BLE001 — one dead agent must not lose the week
            print(f"  {faction['id']:<14} HOLDS (proposal failed: {e})")
            continue
        for w in consistency.check(p, state, faction["id"]):
            print(f"  [consistency] {faction['id']}: {w['kind']} — {w['detail']}")
        proposals.append(p)
        print(f"  {faction['id']:<14} target={p['target']:<14} risk={p['risk_acceptance']:<6} {p['action']}")

    print("resolution:")
    outcomes = resolve_tick(proposals)
    for o in outcomes:
        kind = "CONTESTED WIN" if o["contested"] and o["success"] else \
               "CONTESTED LOSS" if o["contested"] else \
               "success" if o["success"] else "fail"
        print(f"  {o['actor']:<14} {kind:<14} margin {o['margin']:+d}")
    apply_outcomes(state, outcomes, proposals)

    state["rumors"] = state.get("rumors", []) + rumors.spawn_from_events(state)

    chronicle = write_chronicle(state, week, outcomes, held)
    save_state(state)
    print(f"week {week} written (rng seed {seed}); chronicle: {chronicle.name}")


def _free_chronicle_path(week, slug):
    """Same-week reruns never overwrite: the first free -N suffix wins."""
    out = CHRONICLE_DIR / f"{week:04d}-{slug}.md"
    n = 2
    while out.exists():
        out = CHRONICLE_DIR / f"{week:04d}-{slug}-{n}.md"
        n += 1
    return out


def write_chronicle(state, week, outcomes, held=()):
    CHRONICLE_DIR.mkdir(exist_ok=True)
    city = state["city"]["name"]
    summary = yaml.safe_dump(
        {f["id"]: {"goal": f["goal"], "resources": f["resources"]}
         for f in state["factions"]},
        sort_keys=False, allow_unicode=True,
    )
    try:
        prose = llm.narrate_week(city, week, outcomes, summary)
    except Exception as e:  # noqa: BLE001 — the ledger below is the record
        print(f"[live] narration failed, chronicle carries the ledger only: {e}",
              file=sys.stderr)
        prose = ""
    title = None
    match = re.search(r"^TITLE:\s*(.+)$", prose, re.MULTILINE)
    if match:
        title = match.group(1).strip()
        prose = prose[:match.start()].rstrip()
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "the week").lower()).strip("-")
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
    out.write_text(
        f"# Week {week} — {title or city}\n\n{prose}\n{chr(10).join(table)}\n",
        encoding="utf-8",
    )
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    state = load_state()
    if "--live" in argv:
        run_live(state)
        return 0
    if "--why" in argv:
        i = argv.index("--why")
        if i + 2 >= len(argv):
            print("usage: python src/tick.py --why <week> <actor>", file=sys.stderr)
            return 2
        sys.exit(why_cli(state, ["tick.py", argv[i + 1], argv[i + 2]]))
    clock = state["meta"]["clock"]
    city = state["city"]["name"]
    print(f"{city}, week {clock['week']} ({clock['season']})")
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
