"""Fog-of-war views over the canonical state.

Each faction sees itself in full detail. Other factions appear only as they
could plausibly be observed from outside: a vague strength, a public
posture, and nothing else — no goals, no secrets, no knowledge lists.
The asymmetry between views is the engine of emergent behavior; do not
"fix" it by sharing more.

CLI (same state loading as tick.py):
  python src/views.py <faction_id> [--state PATH]
                                      # the faction's view as YAML; exit 0
                                      # known, 2 unknown faction or bad args.
                                      # --state overrides world/state.yaml.
"""

import pathlib
import sys
from copy import deepcopy

import yaml

import rumors

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "world" / "state.yaml"

# What an outsider can roughly estimate by watching, not by reading ledgers.
_OUTSIDER_STRENGTH = {
    "high": "strong",
    "medium": "established",
    "low": "thin",
    "none": "negligible",
}


def faction_view(state, faction_id):
    """Return the slice of `state` that `faction_id` can act on."""
    factions = {f["id"]: f for f in state["factions"]}
    me = factions.get(faction_id)
    if me is None:
        raise KeyError(f"unknown faction id: {faction_id}")

    others = []
    for f in state["factions"]:
        if f["id"] == faction_id:
            continue
        others.append(
            {
                "name": f["name"],
                "kind": f["kind"],
                "apparent_strength": _OUTSIDER_STRENGTH.get(
                    f["resources"].get("muscle", "none")
                ),
                "posture_toward_you": f.get("disposition", {}).get(
                    faction_id, "unknown"
                ),
            }
        )

    view = {
        "week": state["meta"]["clock"]["week"],
        "city": deepcopy(state["city"]),
        "you": deepcopy(me),
        "others": others,
    }
    heard = rumors.heard(state, faction_id)
    if heard:
        view["rumors_you_have_heard"] = heard
    return view


def load_state(path=None):
    """The state file, loaded exactly the way tick.py loads it; `path`
    overrides the default world/state.yaml (absolute, or relative to cwd)."""
    with open(path or STATE_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main(argv):
    """`python src/views.py <faction_id> [--state PATH]`: print the view as
    YAML; 0 known faction, 2 unknown faction or bad args, 1 unreadable state."""
    args = argv
    state_path = None
    if "--state" in args:
        i = args.index("--state")
        if i + 1 >= len(args):
            print("usage: python src/views.py <faction_id> [--state PATH]", file=sys.stderr)
            return 2
        state_path = args[i + 1]
        args = args[:i] + args[i + 2:]
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: python src/views.py <faction_id> [--state PATH]", file=sys.stderr)
        return 2
    try:
        state = load_state(state_path)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        view = faction_view(state, args[0])
    except KeyError:
        print(f"unknown faction id: {args[0]}", file=sys.stderr)
        return 2
    sys.stdout.write(yaml.safe_dump(view, sort_keys=False, allow_unicode=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
