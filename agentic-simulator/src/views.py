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

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import projects
import rumors
from store import STATE_FILE, faction_map, faction_place, places_of, \
    load_state, split_argv

# What an outsider can roughly estimate by watching, not by reading ledgers.
_OUTSIDER_STRENGTH = {
    "high": "strong",
    "medium": "established",
    "low": "thin",
    "none": "negligible",
}

# Rumors heard within this many weeks ride in the prompt; older ones stay
# in state.yaml (rumors.spread, why.py, and the studio's board read the full
# record). Unfiltered, ~3 new claims/week meant a week-50 faction re-sent
# ~150 stale lines each week. No fidelity filter: spread() never adds a
# listener at "dead".
RUMOR_WINDOW_WEEKS = 8


def _recent_rumors(state, faction_id):
    """faction_id's rumors heard inside the window, oldest first, plus the
    honest omitted count (the briefing-cap pattern): the view says what was
    left out instead of silently shrinking the world."""
    week = state["meta"]["clock"]["week"]
    cutoff = week - RUMOR_WINDOW_WEEKS + 1
    heard = rumors.heard(state, faction_id)
    recent = [r for r in heard if r["heard_week"] >= cutoff]
    return recent, len(heard) - len(recent)


def facts_for(state, faction_id):
    """The approved facts this faction may see: `public` facts (public in
    the world — still not automatically what the PARTY knows) and facts
    explicitly addressed to this faction (`known_to`). Everything else —
    dm_only, superseded, or addressed to a rival — is invisible here. The
    studio compiles this list into state.yaml's `knowledge` section
    (already excluding superseded facts — filtered again here so a
    hand-edited state cannot resurrect one); a campaign with no knowledge
    section simply has no facts."""
    out = []
    for fact in state.get("knowledge") or []:
        if not isinstance(fact, dict):
            continue
        if fact.get("status") not in (None, "current"):
            continue
        text = str(fact.get("text") or "").strip()
        if not text:
            continue
        known_to = fact.get("known_to") or []
        if fact.get("visibility") == "public" or faction_id in known_to:
            out.append({"id": fact.get("id"), "text": text})
    return out


def _project_tables(state, faction_id):
    """The two project tables a faction's view carries: `your_projects` in
    full owner detail (minus completion EFFECTS — mechanics are DM
    information; the completion text is what the owner works toward), and
    `visible_projects` as coarse rival records — id, owner, title, places,
    status, and a progress LABEL, never exact counters, requirements, or
    completion effects. Hidden projects appear in neither."""
    place_names = {p["id"]: p.get("name") or p["id"]
                   for p in places_of(state)}
    factions = faction_map(state)
    own, visible = [], []
    for project in projects.projects_of(state):
        if str(project.get("faction_id")) == str(faction_id):
            own.append(projects.owner_view(project,
                                           factions.get(str(faction_id))))
        elif projects.visible_to(project, faction_id):
            visible.append(projects.outsider_view(project, place_names))
    return own, visible


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
        observed = {
            "id": f["id"],
            # name/resources may be missing from a state that never went
            # through validate_state (inspect mode, old snapshots) — the
            # view degrades as a backstop even though --live now refuses
            # such a faction before the spend
            "name": f.get("name") or f["id"],
            "kind": f.get("kind") or "unknown",
        }
        # Ancestry is as observable as a kind: you can see a troll is a
        # troll. Optional free text on the faction ("dwarven", "troll
        # warband"); legacy factions omit it — never a null in the prompt.
        if f.get("species"):
            observed["species"] = str(f["species"])
        observed.update({
            # where this rival operates: a name, not an id — the view
            # is prose for a model, and cross-level contention (a guild
            # vs the kingdom next door) needs to know where rivals stand
            "place": faction_place(state, f).get("name") or "unknown",
            "apparent_strength": _OUTSIDER_STRENGTH.get(
                (f.get("resources") or {}).get("muscle", "none")
            ),
            # or {}: a hand-written `disposition:` (null) degrades like an
            # absent one (the neighboring resources read does the same) —
            # this CLI serves states validate_state never saw (inspect,
            # the studio's view route), and null must not AttributeError
            "posture_toward_you": (f.get("disposition") or {}).get(
                faction_id, "unknown"
            ),
        })
        others.append(observed)

    # Fog-of-war one level up: your place in full detail, every other place
    # as public identity only. A world-tier faction's own-place detail is
    # deliberately thin — interior happenings reach it via events, rumors,
    # and DM-addressed knowledge, not the state's own ledgers.
    own = faction_place(state, me)
    other_places = [
        {"id": p["id"], "name": p["name"], "kind": p.get("kind") or "place"}
        for p in places_of(state) if p["id"] != own.get("id")
    ]

    view = {
        "week": state["meta"]["clock"]["week"],
        "your_place": deepcopy(own),
        "you": deepcopy(me),
        "others": others,
    }
    if other_places:
        view["other_places"] = other_places
    recent_rumors, omitted = _recent_rumors(state, faction_id)
    if recent_rumors:
        view["rumors_you_have_heard"] = recent_rumors
    if omitted:
        view["older_rumors_not_shown"] = omitted
    own_projects, visible_projects = _project_tables(state, faction_id)
    if own_projects:
        view["your_projects"] = own_projects
    if visible_projects:
        view["visible_projects"] = visible_projects
    facts = facts_for(state, faction_id)
    if facts:
        view["facts_you_know"] = facts
    return view


def main(argv):
    """`python src/views.py <faction_id> [--state PATH]`: print the view as
    YAML; 0 known faction, 2 unknown faction or bad args, 1 unreadable state."""
    args = argv
    values, args = split_argv(args, "--state")
    state_path = values.get("--state")
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: python src/views.py <faction_id> [--state PATH]", file=sys.stderr)
        return 2
    try:
        state = load_state(state_path)
    except (OSError, yaml.YAMLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not isinstance(state, dict):
        print(f"error: {state_path or 'the engine-home state.yaml'} is empty or not "
              "a mapping", file=sys.stderr)
        return 1
    ids = {f["id"] for f in state.get("factions") or []
           if isinstance(f, dict) and f.get("id")}
    if args[0] not in ids:
        print(f"unknown faction id: {args[0]}", file=sys.stderr)
        return 2
    try:
        view = faction_view(state, args[0])
    except KeyError as e:
        # the id was verified above, so this is a state missing a key the
        # view needs (a rumor without id/heard_by/transmissions, say) —
        # report the schema gap, not a wrong diagnosis of the faction id
        print(f"state is missing a key the view needs: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(yaml.safe_dump(view, sort_keys=False, allow_unicode=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
