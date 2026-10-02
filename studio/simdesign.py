"""Premise-to-sandbox design (ticket 03) — the loregen pattern, retargeted.

design_simulation() builds ONE prompt from the campaign's geography plus the
places the DM names, asks the LLM for a faction-sandbox draft as JSON, and
validates it against the validators that already own the shapes: the
create-form's premise rule, spine.validate_faction_docs for factions, and
the ENGINE's project normalizer (src/projects.py --check via subprocess —
no imports across projects) for suggested projects. A reply that fails is
re-asked ONCE with the validator's words; a second failure raises
DesignError (the router maps it to a 502 carrying those words — the same
posture as the world-options gate).

Nothing is staged and nothing is canon: the returned design only PREFILLS
the Create a simulation form, and secrets travel OUTSIDE the faction docs —
they are proposed DM-only knowledge-fact drafts, never part of any state
payload. Create stays the accept, on the unchanged free (no-LLM) path.

CLI (the guardrail "every app feature has a CLI equivalent"):

  python studio/simdesign.py <campaign> --design --premise "..." \
      [--place NAME]... [--out FILE]     # prints/ writes the design JSON

Exit codes: 0 ok, 1 refused or failed validation, 2 usage.
"""

import argparse
import json
import pathlib
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import engines
import llm_client
import spine
from loregen import FedOrRefused


class DesignError(Exception):
    """The model's suggestion failed validation twice — carries the
    validators' words; the router maps it to 502."""


PREMISE_MAX = 600          # the create form's premise cap (one tension, one line)
_REQUEST_PREMISE_MAX = 2000  # the input paragraph cap (same as world-options)
_GEO_TEXT_CAP = 6000       # the geography grounding is fed whole, capped

_ETHOS = (
    "Ground every faction in the premise and the grounding places. Slop is "
    "regression to the mean: no ancient evils, no prophecies — asymmetry "
    "over symmetry."
)

_DESIGN_SHAPE = """\
{
  "premise": "<the tension, ONE line, under 600 characters>",
  "factions": [
    {"name": "<faction name>",
     "kind": "<short type, e.g. toll brotherhood>",
     "goal": "<ONE line: what it wants>",
     "resources": {"coin": "none|low|medium|high",
                   "muscle": "none|low|medium|high",
                   "legitimacy": "none|low|medium|high"},
     "place": "<one of the grounding places, or omit for world-tier>"}
  ],
  "projects": [
    {"faction": "<that faction's name>", "title": "<short>",
     "objective": "<what done looks like, one line>",
     "target": <integer 2-8, steps to complete>}
  ],
  "secrets": [
    {"faction": "<that faction's name>",
     "text": "<ONE line: the hidden true motive>"}
  ]
}"""


def _design_prompt(premise: str, geo_text: str, places: list) -> str:
    return f"""You are the sandbox designer for a tabletop campaign. From the DM's
premise, design the OPENING faction sandbox: the premise line, 2-5 factions
that want things from each other, and optionally per-faction projects
(concrete undertakings with a step counter) and secrets (hidden true
motives, for the DM only).

{_ETHOS}

THE PREMISE (the tension the sandbox exists to escalate):
{premise}

YOU ARE FED THIS GROUNDING (the world the sandbox must live in — use these
names; invent no new world):

{geo_text or '(no world file yet — the named places below are the grounding)'}

PLACES THE DM NAMED (a faction's "place", when it has one, must be one of these):
{", ".join(places) if places else '(none — leave "place" off every faction)'}

TASK: reply with ONLY a JSON object — no prose, no fences — of exactly this
shape:

{_DESIGN_SHAPE}

Guidance proven at the table: give ONE faction a long-game goal whose
project target is 6-8 (that project becomes the campaign's clock), and give
ONE faction a hidden true motive as its secret — the villain twist the DM
knows and the world does not. The goals must collide: two factions cannot
both get what they want."""


def _one_line(text, cap) -> str:
    """One readable line — the same collapse the engine's fields get."""
    return re.sub(r"\s+", " ", str(text or "").strip())[:cap].strip()


def _extract_json_object(text):
    """The outermost JSON object in a model reply (fences tolerated) —
    same tolerance as the world-options gate."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the model's reply")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- validation

def _validate_premise(raw) -> str:
    """The create form's premise rule: the tension, one line, <=600 chars."""
    premise = _one_line(raw, PREMISE_MAX + 1)
    if not premise:
        raise ValueError(
            "the sandbox needs a premise — one line on what the tension is "
            "about (e.g. 'the toll bridge burned the night the wardens "
            "raised the toll')")
    if len(premise) > PREMISE_MAX:
        raise ValueError(
            f"premise is over {PREMISE_MAX} chars — keep it to one tension, "
            "one line")
    return premise


def _faction_index(factions: list) -> dict:
    """slug -> (name, id) for matching a project/secret's `faction` field."""
    return {spine.slugify(f["name"]): (f["name"], f["id"])
            for f in factions}


def _match_faction(ref, factions: list):
    """A model `faction` reference -> the validated faction doc, or None."""
    ref = spine.slugify(ref)
    for f in factions:
        if ref and ref in (spine.slugify(f["name"]), f.get("id", "")):
            return f
    return None


def _validate_projects(raw_projects, factions: list, issues: list) -> list:
    """The suggested projects in engine-normalizable shape; problems land
    in `issues` — the engine's own CLI is the authority (below)."""
    if raw_projects is None:
        return []
    if not isinstance(raw_projects, list):
        issues.append("projects is not a list")
        return []
    out = []
    for i, p in enumerate(raw_projects):
        if not isinstance(p, dict):
            issues.append(f"project {i + 1}: not a mapping")
            continue
        owner = _match_faction(str(p.get("faction") or p.get("faction_id")
                                   or ""), factions)
        if owner is None:
            names = ", ".join(f["name"] for f in factions)
            issues.append(
                f"project {i + 1} names faction "
                f"{p.get('faction')!r}, which this design does not define "
                f"(factions: {names})")
            continue
        out.append({
            "faction": owner["name"],
            "faction_id": owner["id"],
            "title": str(p.get("title") or "").strip(),
            "objective": str(p.get("objective") or "").strip(),
            "target": p.get("target", 4),  # the engine's own default
        })
        if not out[-1]["title"] or not out[-1]["objective"]:
            issues.append(
                f"project {i + 1}: the engine needs a title and an "
                "objective (what done looks like)")
    return out


def _validate_secrets(raw_secrets, factions: list, issues: list) -> list:
    """Secrets as DM-only knowledge-fact drafts — matched to factions but
    NEVER merged into them."""
    if raw_secrets is None:
        return []
    if not isinstance(raw_secrets, list):
        issues.append("secrets is not a list")
        return []
    out = []
    for i, s in enumerate(raw_secrets):
        if not isinstance(s, dict):
            issues.append(f"secret {i + 1}: not a mapping")
            continue
        owner = _match_faction(str(s.get("faction") or s.get("faction_id")
                                   or ""), factions)
        if owner is None:
            names = ", ".join(f["name"] for f in factions)
            issues.append(
                f"secret {i + 1} names faction {s.get('faction')!r}, which "
                f"this design does not define (factions: {names})")
            continue
        text = _one_line(s.get("text") or s.get("secret"), PREMISE_MAX)
        if not text:
            issues.append(f"secret {i + 1}: empty — one line, the hidden "
                          "angle")
            continue
        out.append({"faction": owner["name"], "text": text})
    return out


def _check_projects_with_engine(faction_docs: list, projects: list,
                                log) -> list:
    """The ENGINE's project normalizer is the authority for suggested
    projects: run src/projects.py --check against a candidate state built
    from the designed factions (temp dir; nothing of the campaign is read
    or written). Returns [] when every suggestion normalizes, else the
    engine's refusal lines."""
    if not projects:
        return []
    operations = [{
        "op": "create",
        "reason": "suggested by the premise design",
        "project": {"faction_id": p["faction_id"], "title": p["title"],
                    "objective": p["objective"], "target": p["target"]},
    } for p in projects]
    candidate = {
        "meta": {"clock": {"week": 0, "season": "early spring"}},
        "factions": faction_docs,
    }
    with tempfile.TemporaryDirectory(prefix="studio-simdesign-") as td:
        tmp = Path(td)
        state_path = tmp / "state.yaml"
        state_path.write_text(spine.dump_yaml(candidate), encoding="utf-8")
        changes_path = tmp / "changes.yaml"
        changes_path.write_text(spine.dump_yaml({"operations": operations}),
                                encoding="utf-8")
        rc, lines = engines.run_py(
            engines.AGENTIC_SIMULATOR, "src/projects.py",
            "--check", str(changes_path), "--state", str(state_path),
            timeout=120)
    if rc == 0:
        return []
    refusals = [ln.split("refusing:", 1)[1].strip() for ln in lines
                if "refusing:" in ln]
    log("engine refused the suggested projects: "
        + " | ".join(refusals or lines[-3:]))
    return refusals or [engines.tail_detail(lines, 3)]


def _validate_reply(raw: str, places: list, log):
    """One model reply -> (design dict, notes), or raise ValueError carrying
    the validator's words (what the re-ask and the 502 quote)."""
    try:
        doc = _extract_json_object(raw)
    except ValueError as e:
        raise ValueError(f"unusable reply: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"unusable reply: unparseable JSON ({e})") from e
    if not isinstance(doc, dict):
        raise ValueError("the reply's JSON is not an object")

    issues: list = []
    try:
        premise = _validate_premise(doc.get("premise"))
    except ValueError as e:
        premise = ""
        issues.append(str(e))

    raw_factions = doc.get("factions")
    faction_docs = []
    if not isinstance(raw_factions, list) or not raw_factions:
        issues.append("the reply carries no factions list")
    else:
        plain, notes = [], []
        place_slugs = {spine.slugify(p) for p in places if spine.slugify(p)}
        for i, f in enumerate(raw_factions):
            if not isinstance(f, dict):
                issues.append(f"faction {i + 1}: not a mapping")
                continue
            entry = {"name": f.get("name"), "goal": f.get("goal"),
                     "kind": f.get("kind"),
                     "resources": f.get("resources") or {}}
            want = str(f.get("place") or "").strip()
            if want:
                if spine.slugify(want) in place_slugs:
                    entry["place"] = want
                else:
                    # the form would refuse a faction that names a place
                    # the sandbox does not have — clear it to the primary
                    # city and say so, rather than prefill a dead end
                    notes.append(
                        f"faction {f.get('name')!r}: place {want!r} is not "
                        "one of the grounding places — cleared to the "
                        "primary city")
            plain.append(entry)
        try:
            faction_docs = spine.validate_faction_docs(plain)
        except spine.CampaignError as e:
            issues.append(str(e))

    if faction_docs:
        projects = _validate_projects(doc.get("projects"), faction_docs,
                                      issues)
        secrets = _validate_secrets(doc.get("secrets"), faction_docs, issues)
    else:
        projects, secrets = [], []

    issues.extend(_check_projects_with_engine(faction_docs, projects, log))
    if issues:
        raise ValueError(" | ".join(issues[:4]))

    form_factions = []
    for f in faction_docs:
        out = {"name": f["name"], "goal": f["goal"],
               "resources": dict(f["resources"])}
        if f.get("kind"):
            out["kind"] = f["kind"]
        if f.get("place"):
            out["place"] = f["place"]
        form_factions.append(out)
    return ({"premise": premise, "factions": form_factions,
             "projects": projects, "secrets": secrets}, notes)


# ------------------------------------------------------------------- service

def design_simulation(campaign: str, premise: str, places=None, log=None):
    """Premise -> a validated, form-shaped sandbox draft. One metered LLM
    pass (begin_run; a failing reply is re-asked once — two complete() calls
    at most). Raises FedOrRefused when there is no world AND no places to
    ground the design in (the create form itself stays free and no-LLM),
    DesignError when the model cannot produce a valid draft.

    Returns {"design": {...}, "notes": [...], "usage": {...}} — nothing is
    staged; simulation/state.yaml is never touched here."""
    campaign_dir = spine.require_campaign(campaign)
    log = log or (lambda line: None)
    places = [str(p or "").strip() for p in (places or []) if str(p or "").strip()]
    premise = (premise or "").strip()
    if not premise:
        raise FedOrRefused(
            "the design needs a premise — one paragraph on what the tension "
            "is about")
    if len(premise) > _REQUEST_PREMISE_MAX:
        raise FedOrRefused(
            f"premise must be under {_REQUEST_PREMISE_MAX} characters")

    geo_path = campaign_dir / "world" / "geography.yaml"
    geo_text = ""
    if geo_path.is_file():
        geo_text = geo_path.read_text(encoding="utf-8",
                                      errors="replace")[:_GEO_TEXT_CAP]
        log(f"fed: world/geography.yaml ({len(geo_text.splitlines())} lines)")
    elif not places:
        raise FedOrRefused(
            "there is no world to design against: world/geography.yaml is "
            "missing and no places were named — name the places (or "
            "generate a world first, Maps tab); generation is fed or "
            "refused")
    if places:
        log(f"fed: {len(places)} named place(s) as grounding")

    llm_client.begin_run()  # the run's call ceiling: 1 pass, +1 bounded re-ask
    prompt = _design_prompt(premise, geo_text, places)
    design, notes = None, []
    for attempt in (1, 2):
        log("asking the model for a faction-sandbox design "
            + ("(re-ask after validation failure)" if attempt == 2 else "")
            + " ...")
        try:
            raw = llm_client.complete(prompt)
            design, attempt_notes = _validate_reply(raw, places, log)
            notes.extend(attempt_notes)
            break
        except ValueError as e:
            # the validator's words are the diagnosis: feed them back once,
            # then surface them (never a bare "failed validation")
            if attempt == 2:
                raise DesignError(
                    "the model's suggestion failed validation twice: "
                    f"{e}") from e
            log(f"validation failed: {e}")
            prompt = (f"Your previous reply failed validation:\n  {e}\n\n"
                      "Fix those problems and reply again with the "
                      "corrected JSON object ONLY.\n\n" + prompt)
    log(f"designed {len(design['factions'])} faction(s), "
        f"{len(design['projects'])} project hint(s), "
        f"{len(design['secrets'])} secret draft(s)")
    for n in notes:
        log(f"note: {n}")
    return {"design": design, "notes": notes,
            "usage": llm_client.run_usage()}


# ----------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="simdesign.py",
        description="Premise -> faction-sandbox design draft (one metered "
                    "LLM pass; prefill material only — nothing is created)")
    p.add_argument("campaign")
    p.add_argument("--design", action="store_true",
                   help="run the design pass and print the JSON")
    p.add_argument("--premise", required=True,
                   help="the tension, one paragraph")
    p.add_argument("--place", action="append", default=[],
                   help="a grounding place (repeatable)")
    p.add_argument("--out", help="write the JSON here (default: stdout)")
    args = p.parse_args(argv)
    if not args.design:
        print("error: give --design (the only mode this CLI has)",
              file=sys.stderr)
        return 2

    try:
        result = design_simulation(args.campaign, args.premise,
                                   places=args.place,
                                   log=lambda line: print(f"[design] {line}",
                                                          file=sys.stderr))
    except FedOrRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except spine.CampaignNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except DesignError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except llm_client.BudgetExceeded as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:  # llm_client's readable provider errors
        print(f"error: {e}", file=sys.stderr)
        return 1

    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"design written: {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
