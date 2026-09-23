"""The causality inspector.

Every event explains itself from the recorded events it collided with or
built upon. Two links, one sentence each:
  - collision: the event's contested-vs field (or, for events recorded
    before structured fields, the "(contested vs <id>)" suffix in the text)
    names the same-week event this one was rolled against; the margin is
    this event's own.
  - build-up: an earlier event is a build-on when it shares at least two
    content words (length>3, lowercase, plural-s folded) with this event's
    action text, outcome suffix ignored; each step takes the single best
    candidate (week desc, shared count desc, actor id asc) and the walk
    stops after 3 links.

  python src/why.py <week> <actor>    # print the block; exit 0 known, 1 not
  python src/why.py <week> <actor> --json
                                      # one JSON object instead:
                                      # {"week": <int>, "faction": "<id>",
                                      #  "lines": [<the exact strings above>]}
"""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import events
from store import STATE_FILE, load_state

_BAR = 2        # content tokens two events must share to count as a build-on
_MAX_LINKS = 3  # build-on links rendered at most, walking back from the anchor


def find_event(state, week, actor):
    for ev in state["events"]:
        if ev["week"] == week and ev["actor"] == actor:
            return ev
    return None


def _name(state, actor):
    for f in state["factions"]:
        if f["id"] == actor:
            return f["name"]
    return actor


def _tokens(text):
    """Content words: lowercase, split on non-letters, drop len<=3, fold plural s."""
    out = set()
    for w in re.split(r"[^a-z]+", text.lower()):
        if len(w) > 3:
            out.add(w[:-1] if len(w) >= 5 and w.endswith("s") else w)
    return out


def _best_buildon(state, week, toks):
    """Best earlier event sharing >= _BAR tokens; order (week desc, shared desc, actor asc)."""
    best_key, best = None, None
    for ev in state["events"]:
        if ev["week"] >= week:
            continue
        shared = len(toks & _tokens(events.action_text(ev)))
        if shared < _BAR:
            continue
        key = (-ev["week"], -shared, ev["actor"])
        if best_key is None or key < best_key:
            best_key, best = key, ev
    return best


def _walk(state, ev, depth, lines):
    vs = events.vs_of(ev)
    if depth == 0 and vs:
        line = f"contested against {_name(state, vs)}"
        margin = events.margin_of(ev)
        if margin is not None:
            line += f", margin {margin:+d}"
        partner = find_event(state, ev["week"], vs)
        if partner is not None:
            line += f" — {partner['text']}"
        lines.append("  " + line)
    if depth >= _MAX_LINKS:
        return
    nxt = _best_buildon(state, ev["week"], _tokens(events.action_text(ev)))
    if nxt is None:
        return
    lines.append("  " * (depth + 1)
                 + f"built on: {nxt['text']} (week {nxt['week']}, {nxt['actor']})")
    _walk(state, nxt, depth + 1, lines)


def explain(state, week, actor):
    """The causal chain backward from event (week, actor) as lines; anchor first."""
    anchor = find_event(state, week, actor)
    if anchor is None:
        return [f"no recorded event: week {week}, actor {actor}"]
    lines = [f"WEEK {week} — {_name(state, actor)}: {anchor['text']}"]
    _walk(state, anchor, 0, lines)
    return lines


def why_cli(state, argv):
    """`python src/why.py <week> <actor> [--json]`: print the block; 0 known, 1 not.
    With --json (anywhere in the args): one JSON object {"week", "faction",
    "lines"} — the exact lines the human format prints, nothing reformatted."""
    args = argv[1:]
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if len(args) != 2 or not args[0].isdigit():
        print("usage: python src/why.py <week> <actor>", file=sys.stderr)
        return 1
    week, actor = int(args[0]), args[1]
    lines = explain(state, week, actor)
    if as_json:
        print(json.dumps({"week": week, "faction": actor, "lines": lines},
                         ensure_ascii=False))
    else:
        print("\n".join(lines))
    return 0 if find_event(state, week, actor) else 1


if __name__ == "__main__":
    sys.exit(why_cli(load_state(), sys.argv))
