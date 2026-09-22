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
  - anything else: no resource change.
Semantic consequences (knowledge gained, dispositions shifted) are NOT
applied by code — they land in the chronicle for human review (M4's loop).
"""

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import llm
import rumors
import scheduler
import consistency
from why import why_cli
from views import faction_view
from resolve import resolve_tick

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "world" / "state.yaml"
TEMPLATE_FILE = ROOT / "agents" / "faction.md"
CHRONICLE_DIR = ROOT / "chronicle"

_LEVELS = ("none", "low", "medium", "high")


def load_state():
    with open(STATE_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(state, f, sort_keys=False, allow_unicode=True, width=100)


def step_resource(resources, direction):
    """Shift a faction's strongest committed resource one grade. `committed`
    is the proposal's resources_committed dict; direction is +1 / -1."""
    if not resources:
        return
    strongest = max(resources, key=lambda k: _LEVELS.index(resources[k]))
    i = _LEVELS.index(resources[strongest])
    resources[strongest] = _LEVELS[max(0, min(len(_LEVELS) - 1, i + direction))]


def apply_mechanical(state, faction_id, outcome):
    faction = next(f for f in state["factions"] if f["id"] == faction_id)
    committed = outcome.get("committed", {})
    if outcome["success"] and outcome["margin"] >= 5:
        step_resource(faction["resources"], +1)
    elif (outcome.get("contested") and not outcome["success"]) or outcome["margin"] <= -5:
        step_resource(faction["resources"], -1)
    state["events"].append({
        "week": state["meta"]["clock"]["week"],
        "actor": faction_id,
        "text": f"{outcome['action']} — "
                + ("succeeded" if outcome["success"] else "failed")
                + (f" (contested vs {outcome['vs']})" if outcome.get("contested") else "")
                + f", margin {outcome['margin']:+d}",
    })


def run_live(state):
    act = scheduler.activation(state)          # "last week" = the clock as-is
    held = scheduler.holds(state, act)
    week = state["meta"]["clock"]["week"] + 1
    state["meta"]["clock"]["week"] = week
    template = TEMPLATE_FILE.read_text(encoding="utf-8")

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
        p = llm.propose(view, template, faction["id"])
        for w in consistency.check(p, state, faction["id"]):
            print(f"  [consistency] {faction['id']}: {w['kind']} — {w['detail']}")
        proposals.append(p)
        print(f"  {faction['id']:<14} target={p['target']:<14} risk={p['risk_acceptance']:<6} {p['action']}")

    print("resolution:")
    outcomes = resolve_tick(proposals)
    for o in outcomes:
        o["committed"] = next(p for p in proposals if p["actor"] == o["actor"])["resources_committed"]
        kind = "CONTESTED WIN" if o["contested"] and o["success"] else \
               "CONTESTED LOSS" if o["contested"] else \
               "success" if o["success"] else "fail"
        print(f"  {o['actor']:<14} {kind:<14} margin {o['margin']:+d}")
        apply_mechanical(state, o["actor"], o)

    state["rumors"] = state.get("rumors", []) + rumors.spawn_from_events(state)

    chronicle = write_chronicle(state, week, outcomes, held)
    save_state(state)
    print(f"week {week} written; chronicle: {chronicle.name}")


def write_chronicle(state, week, outcomes, held=()):
    CHRONICLE_DIR.mkdir(exist_ok=True)
    city = state["city"]["name"]
    summary = yaml.safe_dump(
        {f["id"]: {"goal": f["goal"], "resources": f["resources"]}
         for f in state["factions"]},
        sort_keys=False, allow_unicode=True,
    )
    prose, title = llm.narrate_week(city, week, outcomes, summary), None
    match = re.search(r"^TITLE:\s*(.+)$", prose, re.MULTILINE)
    if match:
        title = match.group(1).strip()
        prose = prose[:match.start()].rstrip()
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "the week").lower()).strip("-")
    out = CHRONICLE_DIR / f"{week:04d}-{slug}.md"

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


def main():
    state = load_state()
    if "--live" in sys.argv:
        run_live(state)
        return
    if "--why" in sys.argv:
        i = sys.argv.index("--why")
        week, actor = sys.argv[i + 1], sys.argv[i + 2]
        sys.exit(why_cli(state, ["tick.py", week, actor]))
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


if __name__ == "__main__":
    main()
