"""Lore generation (M2) — fed or refused, and never auto-applied.

generate_entries() builds ONE prompt from whole-file loads of the campaign's
spine data (geography, timeline, entities, style — no retrieval, by design)
and asks the LLM for 3-6 timeline entries as YAML. The narrator ethos is
verbatim-adapted from agentic-simulator/src/llm.py's narrate_week: only what
the files back, no invented facts, slop is regression to the mean.

Every returned entry is validated mechanically: shape (when/title/text) and
the hallucinated-name check — capitalized multi-word candidates must resolve
to geography, entities, the style name lists, or the entry's own declared
new_names. Bad entries are DROPPED with a finding, never re-asked.

Output is staged to lore/pending-entries-N.yaml (lowest free N). timeline.yaml
is never touched here — approval does that (M4, guardrail 3). After staging,
two advisory findings passes run and are merged into the job result:
verification's structural layer (check.py via subprocess, list-form args —
the parent path contains `&`) and one canon-audit LLM pass. NOTHING blocks:
findings are a review queue for the DM.
"""

import datetime
import json
import pathlib
import re

import yaml

import engines
import llm_client
import spine


class FedOrRefused(Exception):
    """Generation cannot be grounded in spine data — refuse, do not invent
    (guardrail 4)."""


# --------------------------------------------------------------------- files

def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _style_dir(campaign_dir: pathlib.Path) -> pathlib.Path:
    return campaign_dir / "lore" / "style"


def style_texts(campaign_dir: pathlib.Path) -> dict:
    """Every file in lore/style/ as {filename: text} (whole-file loads)."""
    d = _style_dir(campaign_dir)
    if not d.is_dir():
        return {}
    return {p.name: _read(p) for p in sorted(d.iterdir()) if p.is_file()}


def _next_pending_path(campaign_dir: pathlib.Path) -> pathlib.Path:
    """lore/pending-entries-N.yaml at the lowest free integer N."""
    lore = campaign_dir / "lore"
    used = set()
    if lore.is_dir():
        for p in lore.glob("pending-entries-*.yaml"):
            m = re.fullmatch(r"pending-entries-(\d+)\.yaml", p.name)
            if m:
                used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return lore / f"pending-entries-{n}.yaml"


def _next_entry_id(timeline_text: str) -> int:
    """Timeline ids are t0001…; pending entries continue the sequence."""
    top = 0
    for m in re.finditer(r"\bid:\s*t0*(\d+)\b", timeline_text or ""):
        top = max(top, int(m.group(1)))
    return top + 1


# ------------------------------------------------------------------ prompting

# The narrator ethos, verbatim-adapted from agentic-simulator/src/llm.py
# ("only what the ledger backs"), retargeted at the spine files.
_ETHOS = (
    "You may ONLY state things that follow from these files. Invent no new "
    "facts. New place names must be constructed from the culture name list "
    "and voice excerpts. Slop is regression to the mean: no prophecies, no "
    "ancient evils, no tavern meetings, no \"little did they know\"."
)

_ENTRY_SHAPE = """\
- when: <when, in terms the timeline itself uses, e.g. "week 0" or "before week 1">
  title: <3-6 word title>
  text: <ONE to THREE sentences, past tense, dry and concrete>
  new_names: [<names coined for this entry that appear in none of the files above>]"""


def _entries_prompt(geo_text, timeline_text, entities_text, styles, focus):
    voice = styles.get("voice.md") or "(no voice.md — match the timeline's tone)"
    name_lists = "\n\n".join(
        f"--- {fn} ---\n{text}" for fn, text in styles.items()
        if fn.startswith("names-"))
    if not name_lists:
        name_lists = "(no culture name lists staged)"
    return f"""You are the chronicler of this campaign. Write backstory entries for a game
master to review. {('Focus: ' + focus + '.') if focus else ''}

{_ETHOS}

YOU ARE FED THESE FILES (read them whole; they are the only ground truth):

WORLD GEOGRAPHY (nations, cultures, settlements — the logistics grounding):
{geo_text or '(missing)'}

EXISTING TIMELINE (do not contradict it; extend backwards or sideways only):
{timeline_text or '(empty yet)'}

KNOWN ENTITIES (people, factions, places — one-liners):
{entities_text or '(none yet)'}

VOICE EXCERPTS (the tone is defined by these, not by volume of text):
{voice}

CULTURE NAME LISTS (seed names; any new name must be built from these phonetics):
{name_lists}

TASK: write 3 to 6 timeline entries of backstory, past tense, dry and
concrete, each ONE to THREE sentences. Every entry matches this shape:

{_ENTRY_SHAPE}

Reply with the YAML block ONLY."""


def _names_prompt(names_text, voice_text, culture, count, hint):
    hint_line = f"\nDM HINT: {hint}" if hint else ""
    return f"""You are a naming assistant for a tabletop campaign. Produce {count} NEW
{culture} names: evocative but ordinary-sounding, in the culture's phonetics.

{_ETHOS}

SEED NAME LIST for {culture} (never repeat any of these):
{names_text}

VOICE EXCERPTS (the texture new names should sit beside):
{voice_text or '(none)'}
{hint_line}
Reply with the names ONLY — a plain list, one name per line, no numbers, no
commentary. Exactly {count} names."""


# -------------------------------------------------------------------- parsing

def _extract_yaml(text):
    """Same tolerance as the simulator's llm.py: accept a fenced block."""
    match = re.search(r"```(?:ya?ml)?\s*(.*?)```", text, re.DOTALL)
    block = match.group(1) if match else text
    return yaml.safe_load(block)


def _entries_from_doc(doc):
    """Accept a bare list or {entries: [...]} / {timeline: [...]}."""
    if isinstance(doc, dict):
        doc = doc.get("entries") or doc.get("timeline") or []
    return doc if isinstance(doc, list) else []


# --------------------------------------------------------------- name checks

# Capitalized multi-word candidates, allowing lowercase particles between
# capitals ("Sera of Ruravaqan", "House Ferrand"). The FIRST token must be a
# capitalized word — particle-only runs ("of the") are not names. A single
# capitalized word alone is never checked — too noisy.
_CAP = r"[A-Z][A-Za-z0-9'’-]*"
_MULTI_NAME = re.compile(rf"\b{_CAP}(?:\s+(?:{_CAP}|of|the|de))+\b")


def _normalize(name: str) -> str:
    name = name.strip().strip("'\"").removesuffix("'s").removesuffix("’s")
    low = name.lower()
    low = re.sub(r"^the\s+", "", low)
    return re.sub(r"\s+", " ", low).strip()


def _canon_names(geo_text, entities_text, styles):
    """Every name the spine already knows, normalized. Mechanical, not
    clever: exact keys plus quoted/alias tokens from the YAML text."""
    known = set()
    for text in (geo_text, entities_text):
        if not text:
            continue
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            doc = None
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9'’-]*(?:\s+[A-Z][A-Za-z0-9'’-]*)*)\b",
                             _yaml_string_values(doc)):
            for piece in _split_particles(m.group(1)):
                known.add(_normalize(piece))
            known.add(_normalize(m.group(1)))
    for fn, text in styles.items():
        if fn.startswith("names-"):
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    known.add(_normalize(line))
    known.discard("")
    return known


def _yaml_string_values(doc):
    """Flatten a YAML doc back to its string leaves (names live in values)."""
    out = []
    if isinstance(doc, dict):
        for v in doc.values():
            out.append(_yaml_string_values(v))
    elif isinstance(doc, list):
        for v in doc:
            out.append(_yaml_string_values(v))
    elif isinstance(doc, str):
        out.append(doc)
    return "\n".join(out)


def _split_particles(phrase):
    """"Sera of Ruravaqan" -> ["Sera", "Ruravaqan"] — particles (in any
    case: sentence-initial "The" is a particle, not a name token) dropped."""
    out = []
    for p in re.split(r"\s+(?:of|the|de)\s+|\s+", phrase):
        if p and p[:1].isupper() and p.lower() not in ("of", "the", "de"):
            out.append(p)
    return out


def _hallucinated_names(entry, known, log=None):
    """Multi-word capitalized candidates that resolve to nothing known and
    were not declared in the entry's own new_names list."""
    declared = {_normalize(n) for n in (entry.get("new_names") or [])
                if isinstance(n, str)}
    found = []
    for m in _MULTI_NAME.finditer(" ".join(
            str(entry.get(k) or "") for k in ("title", "text"))):
        phrase = m.group(0)
        norm = _normalize(phrase)
        if norm in known or norm in declared:
            continue
        parts = [_normalize(p) for p in _split_particles(phrase)]
        # A canon token inside the phrase ("the Doge of Ruravaqan") grounds it.
        if any(p in known or p in declared for p in parts):
            continue
        found.append(phrase)
    return found


# ------------------------------------------------------------------- findings

def _finding(rule, level, quote, note, file_="?", line=1):
    return {"file": str(file_), "line": line, "rule": rule, "level": level,
            "quote": quote[:120], "note": note}


def _structural_findings(pending_path, log):
    """Run verification/check.py via engines.run_check (the shared
    invocation). The structural layer has no deps; vale may add a skipped
    note. Never blocks."""
    try:
        return engines.run_check(pending_path, timeout=180)
    except Exception as e:  # noqa: BLE001 — advisory, never blocking
        return [_finding("loregen/structural", "suggestion", "",
                         f"structural check could not run: {e}")]


_CANON_PROMPT = engines.VERIFICATION / "prompts" / "canon-audit.md"


def _canon_findings(new_entries_yaml, state_text, pending_name, log):
    """One canon-audit LLM pass over the new entries. JSON tolerates fences;
    a failure to run or parse records one finding — never blocks."""
    prompt_template = _read(_CANON_PROMPT)
    if not prompt_template:
        return [_finding("prompts/canon-audit", "suggestion", "",
                         "canon-audit prompt not found in verification/")]
    prompt = (prompt_template
              .replace("{{STATE}}", state_text or "(none provided)")
              .replace("{{TEXT}}", new_entries_yaml))
    try:
        raw = llm_client.complete(prompt + "\n\nReturn the JSON ONLY.",
                                  temperature=0.2)
    except Exception as e:  # noqa: BLE001
        return [_finding("prompts/canon-audit", "suggestion", "",
                         f"canon-audit could not run: {e}", file_=pending_name)]
    m = re.search(r"\[.*\]|\{.*\}", raw, re.DOTALL)
    if not m:
        return [_finding("prompts/canon-audit", "suggestion", "",
                         "canon-audit could not run: no JSON in response",
                         file_=pending_name)]
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return [_finding("prompts/canon-audit", "suggestion", "",
                         "canon-audit could not run: unparseable JSON",
                         file_=pending_name)]
    findings = out if isinstance(out, list) else [out]
    normalized = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        level = f.get("severity") or f.get("level")
        normalized.append(_finding(
            "prompts/canon-audit",
            level if level in ("error", "warning", "suggestion") else "warning",
            str(f.get("quote") or ""), str(f.get("note") or "unsupported claim"),
            file_=pending_name))
    return normalized


# ------------------------------------------------------------- entry pipeline

def _validate_entries(raw_entries, known):
    """Shape-check and hallucination-check every entry; return (kept, findings)."""
    kept, findings = [], []
    for i, e in enumerate(raw_entries):
        where = f"entry {i + 1}"
        if not isinstance(e, dict):
            findings.append(_finding(
                "loregen/entry-shape", "warning", str(e)[:80],
                f"{where}: not a mapping — dropped"))
            continue
        when, title, text = (str(e.get(k) or "").strip()
                             for k in ("when", "title", "text"))
        if not (when and title and text):
            findings.append(_finding(
                "loregen/entry-shape", "warning", title or where,
                f"{where}: missing when/title/text — dropped"))
            continue
        bad = _hallucinated_names({"title": title, "text": text,
                                   "new_names": e.get("new_names")}, known)
        if bad:
            findings.append(_finding(
                "loregen/hallucinated-name", "error", title,
                f"{where}: names not grounded in geography/entities/new_names: "
                f"{', '.join(bad)} — dropped"))
            continue
        kept.append({"when": when, "title": title, "text": text,
                     "new_names": [str(n) for n in (e.get("new_names") or [])
                                   if isinstance(n, str)]})
    return kept, findings


def generate_entries(campaign_dir, focus="", log=None):
    """The backstory job: fed prompt -> validated entries -> pending file ->
    advisory findings. Returns the API result dict; raises FedOrRefused when
    the world file is missing (the router maps that to 422)."""
    campaign_dir = pathlib.Path(campaign_dir)
    log = log or (lambda line: None)

    geo_path = campaign_dir / "world" / "geography.yaml"
    if not geo_path.is_file():
        raise FedOrRefused(
            "world/geography.yaml is missing — generate a world first "
            "(Maps tab); generation is fed or refused")
    geo_text = _read(geo_path)
    log(f"fed: world/geography.yaml ({len(geo_text.splitlines())} lines)")

    timeline_text = _read(campaign_dir / "lore" / "timeline.yaml")
    entities_text = _read(campaign_dir / "lore" / "entities.yaml")
    styles = style_texts(campaign_dir)
    log(f"fed: timeline ({len(timeline_text.splitlines())} lines), "
        f"entities, style files {sorted(styles)}")

    prompt = _entries_prompt(geo_text, timeline_text, entities_text,
                             styles, (focus or "").strip())
    log("asking the model for 3-6 timeline entries ...")
    raw = llm_client.complete(prompt)

    try:
        doc = _extract_yaml(raw)
    except yaml.YAMLError as e:
        raise RuntimeError(f"could not parse LLM output as YAML: {e}")
    raw_entries = _entries_from_doc(doc)
    log(f"model returned {len(raw_entries)} candidate entries")

    known = _canon_names(geo_text, entities_text, styles)
    kept, findings = _validate_entries(raw_entries, known)
    if not kept:
        raise RuntimeError(
            "every returned entry failed validation (see findings in the log)")

    # ids continue the timeline sequence; source stays "authored" — the DM's
    # approval (M4) is what makes generated lore canon.
    next_id = _next_entry_id(timeline_text)
    entries = [{"id": f"t{next_id + i:04d}", "when": e["when"],
                "title": e["title"], "text": e["text"],
                "source": "authored"}
               for i, e in enumerate(kept[:6])]

    pending_path = _next_pending_path(campaign_dir)
    created = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    # atomic: under the campaign lock (submit_campaign_job), but a concurrent
    # review GET may read this file mid-write — tmp+replace keeps it whole
    spine.write_text_atomic(
        pending_path,
        yaml.safe_dump({"entries": entries, "created": created,
                        "focus": focus or ""},
                       sort_keys=False, allow_unicode=True, width=100))
    pending_name = f"lore/{pending_path.name}"
    log(f"staged {len(entries)} entries to {pending_name} — nothing touches "
        "timeline.yaml until approval")

    new_entries_yaml = yaml.safe_dump(entries, sort_keys=False,
                                      allow_unicode=True)
    state_text = ("KNOWN ENTITIES:\n" + (entities_text or "(none)")
                  + "\n\nGEOGRAPHY PLACE NAMES:\n"
                  + (geo_text or "(none)"))
    log("structural check (verification/check.py) ...")
    findings.extend(_structural_findings(pending_path, log))
    log("canon-audit pass (one LLM pass, advisory) ...")
    findings.extend(_canon_findings(new_entries_yaml, state_text,
                                    pending_name, log))
    log(f"done: {len(entries)} entries, {len(findings)} findings")

    return {"pending": pending_name, "entries": len(entries),
            "findings": findings}


# ---------------------------------------------------------------- the namer

def name_candidates(campaign_dir, culture, count=8, hint=""):
    """In-voice names: seed list + voice.md in the prompt, plain list out.
    Fed-or-refused: no names-<culture>.txt, no generation (guardrail 4)."""
    campaign_dir = pathlib.Path(campaign_dir)
    count = max(1, min(12, int(count or 8)))
    style = _style_dir(campaign_dir)
    names_path = None
    for candidate in (style / f"names-{culture}.txt",
                      style / f"names-{culture.lower()}.txt"):
        if candidate.is_file():
            names_path = candidate
            break
    if names_path is None:
        raise FedOrRefused(
            f"no seed list for culture '{culture}' yet (lore/style/names-"
            f"{culture}.txt is missing) — stage one first: a few names, one "
            "per line. The helper refuses to invent names from nothing "
            "(fed-or-refused)")
    names_text = _read(names_path)
    voice_text = _read(style / "voice.md")

    seeds = {_normalize(line) for line in names_text.splitlines()
             if line.strip() and not line.strip().startswith("#")}

    prompt = _names_prompt(names_text, voice_text, culture, count, hint)
    raw = llm_client.complete(prompt, temperature=0.9, max_tokens=2048)
    block = re.sub(r"```[a-z]*", "", raw).strip("` \n")

    names, seen = [], set()
    for line in block.splitlines():
        line = line.strip()
        # strip list markers (bullets, numbering) from the front only —
        # trailing digits are part of a name
        name = re.sub(r"^(?:[-*•]|\d+[.):]?)\s+", "", line).strip("`* ").strip()
        if not name or len(name) > 40 or not re.search(r"[A-Za-z]", name):
            continue
        norm = _normalize(name)
        if norm in seeds or norm in seen:
            continue
        seen.add(norm)
        names.append(name)
        if len(names) >= count:
            break
    return names
