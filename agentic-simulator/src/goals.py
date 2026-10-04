"""Goal review: the ledger moves, goals can too.

A faction's `goal` is written once, at sandbox creation, while the ledger
keeps recording — defeats, windfalls, canon deadlines ("the regency has
four weeks"), rumors that invalidate an aim. This module asks the model to
PROPOSE goal updates grounded in that ledger, for DM review. It proposes
nothing on its own authority: the studio stages the output as a
`goal_updates` proposal and the DM accepts or discards it, exactly like a
week. Rejected output is never fed into future prompts.

Grounding (the model sees only what the engine's own state carries):
  - the clock (week, season)
  - every faction's id/name/kind/goal/secret — the DM-side engine view;
    the proposal bundle lands in the DM's campaign
  - the last LEDGER_WINDOW events (week, actor, text, outcome)
  - rumors still in circulation (dead ones do not move anything)

Validation is deterministic and per-update: a proposal the ledger cannot
carry (unknown faction, empty or multi-line goal, goal unchanged, missing
reason) is dropped with a finding, never applied. Zero valid updates is a
fine answer — a quiet ledger justifies no changes.

  python src/goals.py --state <engine-home>/state.yaml --out goal-updates.yaml

Exit codes: 0 a file was written (updates may still be empty),
1 state/IO error, 2 usage, 3 per-run budget exhausted (nothing written).
"""

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import llm
from llm import BudgetExceeded, _extract_yaml, begin_run, complete, env_limit
from store import faction_map, load_state, places_of, split_argv, validate_state

LEDGER_WINDOW = 40      # most recent events the model sees
RUMOR_WINDOW = 20       # in-circulation claims the model sees
GOAL_MAX = 200          # a goal is one line; store keeps them readable
REASON_MAX = 400

_PROMPT = """\
You are the steward of a faction sandbox in a tabletop campaign. Each \
faction has a standing goal written when the sandbox was created. The \
ledger below records what has happened SINCE — outcomes, defeats, \
windfalls, rumors, and any deadlines the canon established (a deadline \
the ledger states is real for this purpose).

Propose goal updates ONLY where the ledger justifies one: a goal achieved \
or made impossible, a defeat that changes what the faction can plausibly \
want, a canon deadline that reframes the aim, or a discovery that \
invalidates the current goal. Be conservative — most weeks justify most \
goals staying exactly as they are. Never invent facts the ledger does \
not carry; a reason must cite the specific events or clock it rests on.

Reply with a YAML block ONLY, no prose:
```yaml
updates:
  - faction_id: <exact faction id from the roster>
    goal: <one line, at most {goal_max} chars — the replacement goal>
    reason: <one or two sentences citing the ledger (weeks, events, \
deadlines)>
```
Give no entry for a faction whose goal should stay. An empty `updates: []` \
is a fine answer when nothing justifies a change.

Roster and ledger:
```yaml
{digest}
```"""


def digest(state):
    """The grounding the model sees — DM-side engine view of its own state."""
    events = (state.get("events") or [])[-LEDGER_WINDOW:]
    rumors = [r for r in (state.get("rumors") or [])
              if isinstance(r, dict) and r.get("fidelity") != "dead"
              ][:RUMOR_WINDOW]
    return {
        "clock": (state.get("meta") or {}).get("clock"),
        "places": [p.get("name") for p in places_of(state)],
        "factions": [
            {k: f.get(k) for k in ("id", "name", "kind", "goal", "secret")}
            for f in (state.get("factions") or []) if isinstance(f, dict)],
        "ledger": [
            {k: e.get(k) for k in ("week", "actor", "text", "outcome")
             if e.get(k) is not None}
            for e in events if isinstance(e, dict)],
        "rumors": [{"claim": r.get("claim"), "fidelity": r.get("fidelity")}
                   for r in rumors],
    }


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text):
    """Collapse whitespace and strip control characters (tabs, nulls,
    escapes) — a goal is one readable line that must survive the state
    file, every faction prompt, and the UI JSON."""
    return re.sub(r"\s+", " ", _CTRL.sub(" ", str(text or "").strip()))


def propose_updates(state, ask):
    """ask(prompt) -> model text (injected so tests run without network).
    Returns (updates, findings): updates is the validated list
    [{faction_id, goal, reason}], findings the dropped ones with why."""
    roster = faction_map(state)
    raw = ask(_PROMPT.format(goal_max=GOAL_MAX,
                             digest=yaml.safe_dump(
                                 digest(state), sort_keys=False,
                                 allow_unicode=True, width=100)))
    doc = _extract_yaml(raw)
    items = doc.get("updates") if isinstance(doc, dict) else None
    if items is None:
        if isinstance(doc, list):
            items = doc  # a bare list is unambiguous enough to keep
        else:
            return [], ["the model's reply had no updates list — treated "
                        "as no proposal"]
    updates, findings, seen = [], [], set()
    for i, item in enumerate(items or []):
        if not isinstance(item, dict):
            findings.append(f"update {i + 1}: not a mapping — dropped")
            continue
        fid = str(item.get("faction_id") or "").strip()
        faction = roster.get(fid) or next(
            (f for k, f in roster.items() if k.lower() == fid.lower()), None)
        if faction is None:
            findings.append(f"update {i + 1}: unknown faction_id "
                            f"{fid!r} — dropped")
            continue
        fid = str(faction["id"])
        if fid in seen:
            findings.append(f"update {i + 1}: duplicate faction {fid} "
                            "— first proposal kept")
            continue
        goal = _clean(item.get("goal"))
        if not goal:
            findings.append(f"update {i + 1} ({fid}): empty goal — dropped")
            continue
        if not isinstance(item.get("goal"), str):
            findings.append(f"update {i + 1} ({fid}): goal is not text "
                            "— dropped")
            continue
        if "\n" in item["goal"]:
            findings.append(f"update {i + 1} ({fid}): goal is multi-line "
                            "(a goal is one line) — dropped")
            continue
        if len(goal) > GOAL_MAX:
            findings.append(f"update {i + 1} ({fid}): goal over {GOAL_MAX} "
                            "chars — dropped")
            continue
        current = _clean(faction.get("goal"))
        if goal.casefold() == current.casefold():
            findings.append(f"update {i + 1} ({fid}): goal is unchanged "
                            "— dropped")
            continue
        if not isinstance(item.get("reason"), str):
            findings.append(f"update {i + 1} ({fid}): reason is not text "
                            "— dropped")
            continue
        reason = _clean(item["reason"])
        if not reason:
            findings.append(f"update {i + 1} ({fid}): missing reason (the "
                            "DM must see what justifies this) — dropped")
            continue
        if len(reason) > REASON_MAX:
            reason = reason[:REASON_MAX - 1].rstrip() + "…"
        seen.add(fid)
        updates.append({"faction_id": fid, "name": faction.get("name") or fid,
                        "goal": goal, "reason": reason,
                        "current_goal": current})
    return updates, findings


def main(argv):
    values, rest = split_argv(argv, "--state", "--out")
    state_path = values.get("--state")
    out_path = values.get("--out")
    if rest:
        print(f"goals.py: unknown argument {rest[0]!r}", file=sys.stderr)
        return 2
    if not state_path or not out_path:
        print("usage: python src/goals.py --state <state.yaml> "
              "--out <goal-updates.yaml>", file=sys.stderr)
        return 2
    try:
        state = load_state(state_path)
        validate_state(state)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"goals.py: {e}", file=sys.stderr)
        return 1

    begin_run(env_limit())
    try:
        updates, findings = propose_updates(state, complete)
    except BudgetExceeded as e:
        print(f"goals.py: {e}", file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001 — one clean line, not a traceback
        print(f"goals.py: goal review failed: {e}", file=sys.stderr)
        return 1
    for f in findings:
        print(f"  [goals] finding: {f}", file=sys.stderr)
    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(
        {"updates": updates, "findings": findings},
        sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    print(f"[goals] proposed {len(updates)} goal update(s) "
          f"({len(findings)} finding(s) dropped) — DM review pending")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
