"""The link index over the campaign vault (obsidian-integration.md §2).

One pass over the campaign's markdown builds a resolver (name / alias /
filename -> page), a backlink index, and two advisory finding lists
(dangling links, name/alias collisions). Findings are findings, never
verdicts: they surface in the review queue and the doctor, nothing blocks.

What is in the graph: lore/entities/*.md pages, simulation/chronicle/*.md
(chronicles are resolvable targets — the Appearances pattern
[[0004-the-coffers-stay-shut|week 4]] must not dangle), and the maps'
companion notes (maps/cities|sites/*/<slug>.md — their `place:` links are
the map↔place join, so a place page's backlinks include its maps).
timeline.yaml stays OUT of the graph on purpose (§2, settled): the
chronicles carry the story-side links.

Frontmatter links count: Obsidian resolves [[...]] in YAML values too, so
the scanner runs over the raw page text, frontmatter included.
"""

import re
from pathlib import Path

import spine

# ![[embed]] and [[link]] resolve the same way; target = text before |,
# section anchor (#Wants) stripped. Obsidian also matches .md-suffixed
# targets ("[[House Ferrand.md]]"), so the suffix is stripped too.
_WIKI_LINK_RE = re.compile(r"!?\[\[([^\[\]#|]+)(?:#[^\[\]#|]*)?(?:\|[^\[\]]*)?\]\]")


def normalize_key(name: str) -> str:
    """The resolution key for a name/alias/filename — same normalization
    loregen applies to canon names (case, leading 'the', possessives)."""
    name = (name or "").strip().strip("'\"").removesuffix("'s").removesuffix("’s")
    low = name.lower()
    low = re.sub(r"^the\s+", "", low)
    return re.sub(r"\s+", " ", low).strip()


def _scan_text(text: str):
    """[(line_no, target)] for every wiki-link in a page of text."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in _WIKI_LINK_RE.finditer(line):
            target = m.group(1).strip()
            if target:
                out.append((n, target))
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""  # an unreadable file is skipped, never a failed index


def build_index(name: str) -> dict:
    """The vault index for one campaign:

    pages      every entity page (spine.entity_pages shape) + chronicles,
               each with `kind` ("entity" | "chronicle") and `key`
    resolve    normalized key -> page dict (names, aliases, filename stems)
    backlinks  page file -> [{source_file, source_kind, source_name, line,
                              target}]
    dangling   [{file, line, target}] — targets resolving to nothing
    collisions [{key, files}] — one key claiming two pages

    A page whose frontmatter does not parse raises CampaignFileError
    (readable 422 via the server handler) — same contract as the loaders.
    """
    campaign = spine.require_campaign(name)
    # `source` is the index's discriminator (entity page vs chronicle); a
    # page's own freeform `kind` (banking house, place, district…) stays
    # untouched — it is the DM's data, not the index's.
    pages = [dict(p, source="entity") for p in spine.entity_pages(name)]

    chron_dir = campaign / "simulation" / "chronicle"
    chronicles = []
    if chron_dir.is_dir():
        for c in spine.chronicles(name):
            chronicles.append({
                "source": "chronicle",
                "kind": "",
                "name": c["title"],
                "file": c["file"],
                "aliases": [],
                "week": c["week"],
                "body": "",
                "note": "",
                "id": "",
            })
    pages.extend(chronicles)

    # companion notes: resolvable targets (vault-unique <slug>.md basenames)
    # and backlink sources — this is the map↔place join
    for map_kind in ("cities", "sites"):
        base = campaign / "maps" / map_kind
        if not base.is_dir():
            continue
        for slug_dir in sorted(base.iterdir()):
            if not slug_dir.is_dir():
                continue
            note = slug_dir / f"{slug_dir.name}.md"
            if note.is_file():
                pages.append({
                    "source": "map",
                    "kind": map_kind.rstrip("s"),
                    "name": slug_dir.name,
                    "file": f"maps/{map_kind}/{slug_dir.name}/{slug_dir.name}.md",
                    "aliases": [],
                    "body": "",
                    "note": "",
                    "id": "",
                })

    resolve: dict = {}
    claiming: dict = {}
    for p in pages:
        stem = Path(p["file"]).stem
        keys = [normalize_key(stem), normalize_key(p["name"])]
        keys += [normalize_key(a) for a in (p.get("aliases") or [])]
        keys = [k for k in dict.fromkeys(keys) if k]
        p["key"] = keys[0] if keys else ""
        for k in keys:
            if k in resolve and resolve[k]["file"] != p["file"]:
                claiming.setdefault(k, {resolve[k]["file"]}).add(p["file"])
                continue  # first page wins resolution; the collision is a finding
            resolve.setdefault(k, p)

    backlinks: dict = {}
    dangling: list = []

    def scan(rel: str, kind: str, source_name: str) -> None:
        text = _read(campaign / rel)
        for n, target in _scan_text(text):
            page = resolve.get(normalize_key(target))
            if page is not None:
                backlinks.setdefault(page["file"], []).append({
                    "source_file": rel,
                    "source_kind": kind,
                    "source_name": source_name,
                    "line": n,
                    "target": target,
                })
            else:
                dangling.append({"file": rel, "line": n, "target": target})

    for p in pages:
        if p["source"] in ("entity", "map"):
            scan(p["file"], p["source"], p["name"])
    for c in chronicles:
        scan(c["file"], "chronicle", c["name"])

    collisions = [{"key": k, "files": sorted(files)}
                  for k, files in sorted(claiming.items())]
    return {"pages": pages, "resolve": resolve, "backlinks": backlinks,
            "dangling": dangling, "collisions": collisions}


def backlinks_for(name: str, page_file: str, idx: dict | None = None) -> list:
    """The 'mentioned in' list for one page file, most interesting source
    first (chronicles before entities, then by file/line). Pass `idx` (a
    build_index result already in hand) to skip the rebuild."""
    return sorted(
        (idx or build_index(name))["backlinks"].get(page_file, []),
        key=lambda b: (0 if b["source_kind"] == "chronicle" else 1,
                       b["source_file"], b["line"]))


def findings(name: str) -> list:
    """Dangling links and collisions as review-queue findings (loregen's
    finding shape: file/line/rule/level/quote/note). Advisory by contract."""
    idx = build_index(name)
    out = []
    for d in idx["dangling"]:
        out.append({
            "file": d["file"], "line": d["line"], "rule": "links/dangling",
            "level": "warning", "quote": f"[[{d['target']}]]",
            "note": f"[[{d['target']}]] resolves to no entity page or "
                    "chronicle — create the page, or fix the link",
        })
    for c in idx["collisions"]:
        out.append({
            "file": c["files"][0], "line": 1, "rule": "links/collision",
            "level": "warning", "quote": c["key"],
            "note": f"name/alias '{c['key']}' is claimed by "
                    f"{len(c['files'])} pages ({', '.join(c['files'])}) — "
                    "links resolve to the first; rename or de-alias",
        })
    return out


def missing_page_targets(name: str) -> list:
    """Dangling wiki-links grouped by target, most-linked first — the
    fed-or-refused generation targets of vault stage 4 (every group has at
    least one source page: the link itself is the grounding)."""
    idx = build_index(name)
    groups: dict = {}
    for d in idx["dangling"]:
        key = normalize_key(d["target"])
        if not key:
            continue
        g = groups.setdefault(key, {"target": d["target"], "sources": set()})
        g["sources"].add(d["file"])
    out = [{"target": g["target"], "sources": sorted(g["sources"])}
           for g in groups.values()]
    out.sort(key=lambda t: (-len(t["sources"]), t["target"].lower()))
    return out


# ------------------------------------------------------- chronicle linkify

_SKIP_LINE_RE = re.compile(r"^\s*(#|\|)")


def linkify_markdown(text: str, entities) -> str:
    """Wrap the first occurrence of each known name/alias in a wiki-link
    (vault stage 4: chronicles linkified on write). Longest names first so
    "House Ferrand" wins over "Ferrand"; the matched text rides as the
    display, since the resolver normalizes case/articles/possessives.
    Headings and table rows are skipped (roll tables stay clean), and text
    inside an existing [[link]] is never touched."""
    candidates = []
    for e in entities:
        for form in [str(e.get("name") or "")] + \
                [str(a) for a in (e.get("aliases") or [])]:
            form = form.strip()
            if len(form) >= 3 and not form.startswith("[["):
                candidates.append(form)
    candidates = sorted(set(candidates), key=len, reverse=True)
    if not candidates:
        return text

    for form in candidates:
        pattern = re.compile(
            r"\b" + re.escape(form).replace(r"\ ", r"\s+") + r"\b",
            re.IGNORECASE)
        for m in pattern.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            if _SKIP_LINE_RE.match(text[line_start:]):
                continue  # heading or table row — try the next occurrence
            before = text[:m.start()]
            if before.count("[[") > before.count("]]"):
                break  # already linked once — one link per name per file
            text = text[:m.start()] + "[[" + m.group(0) + "]]" + text[m.end():]
            break
    return text


def linkify_chronicle(name: str, filename: str) -> bool:
    """Linkify one campaign chronicle in place (atomic write). True when
    the file changed. Names that resolve to nothing are left alone — the
    hallucination guard in reverse."""
    path = spine.campaign_dir(name) / "simulation" / "chronicle" / filename
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    linked = linkify_markdown(text, spine.load_entities(name))
    if linked == text:
        return False
    spine.write_text_atomic(path, linked)
    return True
