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
import links
import llm_client
import publication
import spine


class FedOrRefused(Exception):
    """Generation cannot be grounded in spine data — refuse, do not invent
    (guardrail 4)."""


# --------------------------------------------------------------------- files

def _input_provenance(campaign_dir: pathlib.Path, files, dirs=()) -> dict:
    """Revisions of everything a generation run read, written into the
    pending doc: approval re-checks them and refuses a stale draft (409) so
    prose derived from superseded canon never lands. files = campaign-rel
    paths (byte revisions); dirs = campaign-rel directories (membership
    fingerprints — a page created or deleted later invalidates the draft)."""
    return {
        "base_revisions": {
            rel: publication.file_revision(campaign_dir / rel)
            for rel in files},
        "input_fingerprints": {
            rel: publication.fingerprint_dir(campaign_dir / rel)
            for rel in dirs},
    }


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


def _next_pending_path(campaign_dir: pathlib.Path, kind: str = "entries") -> pathlib.Path:
    """lore/pending-<kind>-N.yaml at the lowest free integer N."""
    lore = campaign_dir / "lore"
    used = set()
    if lore.is_dir():
        for p in lore.glob(f"pending-{kind}-*.yaml"):
            m = re.fullmatch(rf"pending-{kind}-(\d+)\.yaml", p.name)
            if m:
                used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return lore / f"pending-{kind}-{n}.yaml"


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


def _entities_text(entities) -> str:
    """The entity list as compact one-liners for the prompt (what the old
    entities.yaml fed whole; now rendered from the canonical page list)."""
    lines = []
    for e in entities:
        head = str(e.get("name") or "").strip()
        if not head:
            continue
        kind = str(e.get("kind") or "").strip()
        if kind:
            head += f" ({kind})"
        bits = [head]
        aliases = [str(a) for a in (e.get("aliases") or []) if str(a).strip()]
        if aliases:
            bits.append("aliases: " + ", ".join(aliases))
        note = str(e.get("note") or "").strip()
        if note:
            bits.append(note)
        lines.append("- " + " — ".join(bits))
    return "\n".join(lines)

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
    """Same tolerance as the simulator's llm.py: accept a fenced block,
    yaml-labeled or json-labeled (models wrap YAML lists in ```json often
    enough that refusing them would fail jobs the engine's weeks survive)."""
    match = re.search(r"```(?:ya?ml|json)?\s*(.*?)```", text, re.DOTALL)
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
# capitalized word alone is never checked — too noisy. (The first token is
# captured so _names_from_text can reuse the pattern.)
_CAP = r"[A-Z][A-Za-z0-9'’-]*"
_MULTI_NAME = re.compile(rf"\b({_CAP})(?:\s+(?:{_CAP}|of|the|de))+\b")


def _normalize(name: str) -> str:
    name = name.strip().strip("'\"").removesuffix("'s").removesuffix("’s")
    low = name.lower()
    low = re.sub(r"^the\s+", "", low)
    return re.sub(r"\s+", " ", low).strip()


def _canon_names(geo_text, entities, styles):
    """Every name the spine already knows, normalized. Mechanical, not
    clever: exact keys plus quoted/alias tokens from the geography YAML, the
    canonical entity list (pages), and the style name lists."""
    known = set()
    if geo_text:
        try:
            doc = yaml.safe_load(geo_text)
        except yaml.YAMLError:
            doc = None
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9'’-]*(?:\s+[A-Z][A-Za-z0-9'’-]*)*)\b",
                             _yaml_string_values(doc)):
            for piece in _split_particles(m.group(1)):
                known.add(_normalize(piece))
            known.add(_normalize(m.group(1)))
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9'’-]*(?:\s+[A-Z][A-Za-z0-9'’-]*)*)\b",
                         _yaml_string_values(list(entities))):
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

def _names_from_text(text):
    """Capitalized multi-word phrases in free markdown text, normalized —
    the grounding set for generator output that merely repeats what its
    own input pages say (those pages are not YAML the canon scan sees)."""
    out = set()
    for m in _MULTI_NAME.finditer(text or ""):
        for piece in _split_particles(m.group(1)):
            out.add(_normalize(piece))
        out.add(_normalize(m.group(1)))
    return out


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
    llm_client.begin_run()  # per-run call ceiling; a run stops between calls

    geo_path = campaign_dir / "world" / "geography.yaml"
    if not geo_path.is_file():
        raise FedOrRefused(
            "world/geography.yaml is missing — generate a world first "
            "(Maps tab); generation is fed or refused")
    geo_text = _read(geo_path)
    log(f"fed: world/geography.yaml ({len(geo_text.splitlines())} lines)")

    timeline_text = _read(campaign_dir / "lore" / "timeline.yaml")
    entities = spine.load_entities(campaign_dir.name)
    entities_text = _entities_text(entities)
    styles = style_texts(campaign_dir)
    log(f"fed: timeline ({len(timeline_text.splitlines())} lines), "
        f"entities ({len(entities)} pages), style files {sorted(styles)}")

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

    known = _canon_names(geo_text, entities, styles)
    kept, findings = _validate_entries(raw_entries, known)
    if not kept:
        # the findings ARE the diagnosis — swallow them and the DM only
        # sees "failed validation" with nothing to fix (found 2026-09-25)
        for f in findings:
            log(f"finding: {f['rule']} — {f['note']}")
        raise RuntimeError(
            "every returned entry failed validation: "
            + " | ".join(f"{f['rule']}: {f['note']}"[:200]
                         for f in findings[:3]))

    # ids continue the timeline sequence; source stays "authored" — the DM's
    # approval (M4) is what makes generated lore canon. new_names ride along
    # so the approver can stub their pages (vault stage 4).
    next_id = _next_entry_id(timeline_text)
    entries = [{"id": f"t{next_id + i:04d}", "when": e["when"],
                "title": e["title"], "text": e["text"],
                "source": "authored", "new_names": e["new_names"]}
               for i, e in enumerate(kept[:6])]

    pending_path = _next_pending_path(campaign_dir)
    created = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    provenance = _input_provenance(
        campaign_dir,
        files=["world/geography.yaml", "lore/timeline.yaml"]
              + [f"lore/style/{fn}" for fn in styles],
        dirs=["lore/entities"])
    # atomic: under the campaign lock (submit_campaign_job), but a concurrent
    # review GET may read this file mid-write — tmp+replace keeps it whole
    spine.write_text_atomic(
        pending_path,
        yaml.safe_dump({"entries": entries, "created": created,
                        "focus": focus or "", **provenance},
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


# --------------------------------------------------- the missing-page writer

def _pages_prompt(target_blocks, styles):
    voice = styles.get("voice.md") or "(no voice.md — match the pages' tone)"
    name_lists = "\n\n".join(
        f"--- {fn} ---\n{text}" for fn, text in styles.items()
        if fn.startswith("names-"))
    if not name_lists:
        name_lists = "(no culture name lists staged)"
    return f"""You are the chronicler of this campaign. The lore contains dangling
wiki-links: names mentioned on real pages that have no page of their own.
Write the missing pages.

{_ETHOS}

YOU ARE FED THE PAGES THAT MENTION EACH MISSING NAME (their text is the ONLY
grounding — read them whole):

{target_blocks}

VOICE EXCERPTS (the tone is defined by these, not by volume of text):
{voice}

CULTURE NAME LISTS (seed names; any new name must be built from these phonetics):
{name_lists}

TASK: write ONE short page per missing name fed above, grounded only in what
those pages say. Each page matches this shape:

- name: <the missing name, exactly as linked>
  kind: <freeform — person, place, thing, faction…>
  aliases: [<other spellings the pages use>]
  body: <ONE to THREE sentences, past tense, dry and concrete>
  new_names: [<names coined for this page that appear in none of the files above>]

Reply with the YAML block ONLY."""


def _missing_page_findings(raw_pages, groups, known):
    """Shape-check, target-match, and hallucination-check page drafts;
    return (kept, findings). The name MUST be one of the requested dangling
    targets — a draft for anything else is dropped, not renamed."""
    kept, findings = [], []
    seen = set()
    for i, p in enumerate(raw_pages):
        where = f"page draft {i + 1}"
        if not isinstance(p, dict):
            findings.append(_finding(
                "loregen/page-shape", "warning", str(p)[:80],
                f"{where}: not a mapping — dropped"))
            continue
        name = str(p.get("name") or "").strip()
        body = str(p.get("body") or "").strip()
        if not (name and body):
            findings.append(_finding(
                "loregen/page-shape", "warning", name or where,
                f"{where}: missing name/body — dropped"))
            continue
        norm = links.normalize_key(name)
        if norm not in groups:
            findings.append(_finding(
                "loregen/page-target", "warning", name,
                f"{where}: {name!r} is not one of the dangling links — dropped"))
            continue
        if norm in seen:
            continue  # duplicate draft for the same target: first wins
        bad = _hallucinated_names({"title": name, "text": body,
                                   "new_names": p.get("new_names")}, known)
        if bad:
            findings.append(_finding(
                "loregen/hallucinated-name", "error", name,
                f"{where}: names not grounded in the linking pages/new_names: "
                f"{', '.join(bad)} — dropped"))
            continue
        seen.add(norm)
        aliases = [str(a) for a in (p.get("aliases") or [])
                   if isinstance(a, str) and a.strip()]
        kept.append({"name": name, "kind": str(p.get("kind") or "").strip(),
                     "aliases": aliases, "body": body,
                     "new_names": [str(n) for n in (p.get("new_names") or [])
                                   if isinstance(n, str)]})
    return kept, findings


def generate_missing_pages(campaign_dir, targets=None, log=None, limit=None):
    """Vault stage 4: dangling wiki-links become fed-or-refused generation
    prompts (obsidian-integration.md §3). The grounding is already present —
    the pages that link the missing name — so the prompt is fed those pages
    whole. Drafts stage to lore/pending-pages-N.yaml; nothing lands
    unreviewed. Raises FedOrRefused when there are no dangling links (or
    none of the requested targets is one)."""
    campaign_dir = pathlib.Path(campaign_dir)
    log = log or (lambda line: None)
    llm_client.begin_run()  # per-run call ceiling; a run stops between calls
    cname = campaign_dir.name

    groups = {}
    for t in links.missing_page_targets(cname):
        groups[links.normalize_key(t["target"])] = t
    if not groups:
        raise FedOrRefused(
            "no dangling wiki-links in the lore — nothing to write "
            "(fed-or-refused). Type [[Something New]] on an entity page "
            "(or in Obsidian) and it becomes a generation target")
    if targets:
        wanted = [links.normalize_key(t) for t in targets
                  if links.normalize_key(t) in groups]
        if not wanted:
            raise FedOrRefused(
                "none of the requested targets is a dangling link — the "
                "missing-page list may have changed; refresh and retry")
        groups = {k: groups[k] for k in wanted}
    chosen = list(groups.values())[:6]  # the most-linked first
    if limit is not None:
        chosen = chosen[:max(0, int(limit))]  # bound the run before it burns calls

    blocks = []
    for t in chosen:
        parts = [f"[[{t['target']}]] is mentioned on: "
                 f"{', '.join(t['sources'])}"]
        for rel in t["sources"]:
            text = _read(campaign_dir / rel)[:2400]
            parts.append(f"--- {rel} ---\n{text}")
        blocks.append("\n".join(parts))
    target_blocks = "\n\n".join(blocks)
    styles = style_texts(campaign_dir)
    entities = spine.load_entities(cname)
    log(f"fed: {len(chosen)} dangling link(s) with their linking pages")

    prompt = _pages_prompt(target_blocks, styles)
    log("asking the model for the missing pages ...")
    raw = llm_client.complete(prompt)

    try:
        doc = _extract_yaml(raw)
    except yaml.YAMLError as e:
        raise RuntimeError(f"could not parse LLM output as YAML: {e}")
    raw_pages = doc if isinstance(doc, list) else []

    known = _canon_names(_read(campaign_dir / "world" / "geography.yaml"),
                         entities, styles)
    # the linking pages are the prompt's grounding — a draft that repeats a
    # name straight out of them is not a hallucination (they are markdown,
    # invisible to the YAML-based canon scan) (found 2026-09-25)
    known |= _names_from_text(target_blocks)
    kept, findings = _missing_page_findings(raw_pages, groups, known)
    if not kept:
        for f in findings:
            log(f"finding: {f['rule']} — {f['note']}")
        raise RuntimeError(
            "every returned page draft failed validation: "
            + " | ".join(f"{f['rule']}: {f['note']}"[:200]
                         for f in findings[:3]))

    pending_path = _next_pending_path(campaign_dir, kind="pages")
    created = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    provenance = _input_provenance(
        campaign_dir,
        files=sorted({t_rel for t in chosen for t_rel in t["sources"]}
                     | {"world/geography.yaml"}
                     | {f"lore/style/{fn}" for fn in styles}),
        dirs=["lore/entities"])
    entries = [{"id": f"page-{i + 1}", "title": p["name"],
                "text": p["body"], "source": "authored",
                "page": {"kind": p["kind"], "aliases": p["aliases"]},
                "new_names": p["new_names"]}
               for i, p in enumerate(kept)]
    spine.write_text_atomic(
        pending_path,
        yaml.safe_dump({"entries": entries, "created": created,
                        "mode": "missing-pages", **provenance},
                       sort_keys=False, allow_unicode=True, width=100))
    pending_name = f"lore/{pending_path.name}"
    log(f"staged {len(entries)} page drafts to {pending_name} — nothing "
        "lands until approval in the Review tab")

    pages_yaml = yaml.safe_dump(entries, sort_keys=False, allow_unicode=True)
    state_text = "KNOWN ENTITIES:\n" + _entities_text(entities)
    log("structural check (verification/check.py) ...")
    findings.extend(_structural_findings(pending_path, log))
    log("canon-audit pass (one LLM pass, advisory) ...")
    findings.extend(_canon_findings(pages_yaml, state_text,
                                    pending_name, log))
    log(f"done: {len(entries)} page drafts, {len(findings)} findings")
    return {"pending": pending_name, "pages": len(entries),
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
