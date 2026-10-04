"""Place-centered campaign view (shipped 2026-09-28; the plan is retired).

One deterministic read model that answers the DM's real question when the
party is headed somewhere: "what is here, what changed, and what needs my
attention?" — lore, maps, residents, faction presence, projects, recent
events, facts, and unresolved proposals, every item labeled with WHY it
appears (an explicit relationship, a suggested join, or a mere mention)
and where it came from. No LLM, no inferred locations: an event belongs to
a place only when the event record itself carries the place id.

Identity: one campaign-local place_id. A lore page with `place_id:` in its
frontmatter OWNS identity; simulation places (spine.state_places, including
the normalized legacy city) are provisional records that join to it —
explicitly when the ids match, as a SUGGESTED association when only a
unique normalized name matches (a name match never merges identity on its
own; the Link action does). Old campaigns read without writing anything.

Sections degrade independently: a campaign with no simulation, geography,
or maps still gets a useful page (lore + mentions + an explanation of what
linking would add). Damaged optional sources become findings and section
errors, never a 500; unknown place references become findings, never
silent drops. Pending proposals and forecasts are labeled pending /
hypothetical and are never blended into canon counts.

All mutations (link / patch / associate) go through publication.publish —
campaign-lock + revision-checked + transactional, preserving page bodies
and unknown frontmatter keys.

CLI (same service the HTTP routes use):
    python studio/places.py show <campaign> <place-id> [--json|--markdown]
    python studio/places.py list <campaign> [--json]
    python studio/places.py link <campaign> <place-id> --page "Ferry Crossing"
    python studio/places.py associate <campaign> <place-id> \\
        --source event --id 12 --relationship occurred_at --add ferry-crossing

The printed Markdown is DM material — it identifies itself as such and
labels private / pending / hypothetical sections. It is not a handout.
"""

import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

import briefing
import forecasts
import knowledge
import links
import proposals
import publication
import spine
import yaml

RECENT_WINDOW_WEEKS = 8
DEFAULT_LIMIT = 20


class PlaceError(Exception):
    """A place-view problem that maps to a 422."""


class PlaceNotFound(PlaceError):
    """Unknown place id — the server maps this to 404."""


class PlaceConflict(PlaceError):
    """A stale-revision or identity conflict — the server maps this to 409."""


# ----------------------------------------------------------------- raw files


def _frontmatter_of(text: str) -> tuple[dict | None, str]:
    """(raw frontmatter dict | None, body) for a markdown file — the same
    fence contract spine.parse_entity_page uses, but the FULL meta dict
    survives, so a mutation can preserve keys it does not know about."""
    m = spine._FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        first = str(e).splitlines()[0] if str(e) else type(e).__name__
        raise PlaceError(f"frontmatter does not parse ({first}) — a "
                         "hand-edit slipped?")
    if not isinstance(meta, dict):
        raise PlaceError("frontmatter must be a mapping of fields")
    return meta, text[m.end():].lstrip("\n")


def _page_text(meta: dict, body: str, newline: str = "\n") -> str:
    """The exact bytes a page write produces — frontmatter first, then
    body (same shape spine.entity_page_text makes, but meta here is the
    FULL raw dict with unknown keys already in it).

    This is the RE-SERIALIZING path (ticket 10): for changes that are not
    line-local — list-valued keys, deletions, a page with no frontmatter
    block — where yaml.safe_dump must rebuild the block and by nature
    drops empty-valued keys and comments. Single-scalar changes go
    through _set_frontmatter_scalars instead, which touches one line.
    The page's dominant line ending (from _page_snapshot) is honored."""
    meta = {k: v for k, v in meta.items() if v not in (None, "", [], {})}
    if not meta:
        out = f"{body.strip()}\n"
    else:
        text = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True,
                              default_flow_style=False).strip()
        out = f"---\n{text}\n---\n\n{body.strip()}\n"
    return out if newline == "\n" else out.replace("\n", newline)


# a top-level frontmatter key line ("status: hooked", "given_by:")
_FM_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*:")


def _scalar_yaml(value) -> str:
    """The canonical YAML text for one scalar value, quoting decided by
    yaml.safe_dump itself ('The Troll: Tithe' comes out quoted, 'active'
    does not), so a surgically written line parses exactly like a
    re-serialized one."""
    line = yaml.safe_dump({"k": value}, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)
    return line[len("k:"):].strip()


def _scalar_closes_on_line(v: str) -> bool:
    """Whether a value that OPENS with a quote also closes it on the same
    line. 'status: "hooked' with the closing quote two lines down is one
    YAML scalar spanning lines (it parses, to 'hooked '): rewriting only
    the first line would leave the dangling continuation folding into the
    NEW value ('active "' — a silent wrong-write, ticket 22 R3). Values
    that do not open with a quote close on the line by construction."""
    if v[:1] not in ('"', "'"):
        return True
    q, j = v[0], 1
    while j < len(v):
        if q == '"' and v[j] == "\\":
            j += 2              # \" — an escaped quote inside the value
            continue
        if v[j] == q:
            if q == "'" and v[j + 1:j + 2] == q:
                j += 2          # '' — the single-quote escape
                continue
            return True
        j += 1
    return False


def _inline_comment(rest: str) -> str:
    """The ` # comment` tail of a frontmatter value line, or ''. A `#`
    inside the value's own quotes is NOT a comment ('name: "Shop #5"' —
    ticket 22 P3-21): the scan steps over the closed quoted scalar first
    and only then looks for the space-hash. In a plain scalar a space-hash
    can only BE the comment (YAML ends the value there), so the first
    find is correct."""
    v = rest.strip()
    if v[:1] in ('"', "'"):
        q, j = v[0], 1
        while j < len(v):
            if q == '"' and v[j] == "\\":
                j += 2
                continue
            if v[j] == q:
                if q == "'" and v[j + 1:j + 2] == q:
                    j += 2
                    continue
                break               # the closing quote
            j += 1
        v = v[j + 1:] if j < len(v) else ""
    at = v.find(" #")
    return v[at:] if at != -1 else ""


def _set_frontmatter_scalars(text: str, changes: dict,
                             newline: str = "\n") -> str | None:
    """Frontmatter LINE SURGERY (ticket 10): set non-empty scalar keys by
    editing or inserting their single lines in the ORIGINAL text, so
    every other byte — inline comments, blank-valued keys, spacing, the
    body — survives the write untouched.

    None when the change is not line-local surgery's to make, and the
    caller re-serializes with _page_text: a non-scalar/blank value, a key
    whose current value is a list or mapping (block or flow) or otherwise
    SPANS lines (a quote that closes further down — ticket 22 R3), or a
    page with no frontmatter block. A `#`-prefixed comment line is safe
    unchanged bytes and passes the pre-scan like a blank line (R4); mixed
    line endings fold to the page's dominant style, which _page_snapshot
    captured at the read."""
    for v in changes.values():
        if isinstance(v, str):
            if not v.strip():
                return None  # a deletion in disguise
        elif not isinstance(v, (int, float, bool)):
            return None
    if not changes:
        return None
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None  # no frontmatter block — _page_text creates one
    try:
        fm_end = next(i for i in range(1, len(lines))
                      if lines[i].strip() == "---")
    except StopIteration:
        return None  # unclosed block: not ours to fix silently
    for i in range(1, fm_end):
        line = lines[i]
        if not line.strip() or line[:1] in (" ", "\t"):
            continue  # blank, or a continuation of the key above it
        if line[:1] == "#":
            continue  # a comment line — safe unchanged bytes, keep it (R4)
        if not _FM_KEY_RE.match(line):
            # top-level content that is not a key line (e.g. a stray '{}'
            # an old generator once wrote): editing around it can produce
            # invalid YAML — rebuild the block instead
            return None
    done: set[str] = set()
    for i in range(1, fm_end):
        m = _FM_KEY_RE.match(lines[i])
        if not m:
            continue
        key = m.group(0)[:-1]
        if key not in changes:
            continue
        rest = lines[i][len(m.group(0)):]
        v = rest.strip()
        nxt = lines[i + 1] if i + 1 < fm_end else ""
        # an indented non-comment line below the key continues its value
        # (a block collection, or a plain/quoted scalar folding across
        # lines) — the value is not line-local
        block_follows = nxt[:1] in (" ", "\t") and nxt.lstrip()[:1] != "#"
        if v[:1] in ("[", "{", "|", ">") or block_follows \
                or not _scalar_closes_on_line(v):
            return None  # current value spans beyond its line — rebuild
        comment = _inline_comment(rest)
        lines[i] = f"{key}: {_scalar_yaml(changes[key])}{comment}"
        done.add(key)
    insert_at = fm_end
    for key, value in changes.items():
        if key in done:
            continue
        lines.insert(insert_at, f"{key}: {_scalar_yaml(value)}")
        insert_at += 1
    return newline.join(lines)


def _changed_page_text(raw: str, meta: dict, body: str, changes: dict,
                       newline: str = "\n") -> str:
    """The page text for a frontmatter change (ticket 10): line surgery
    when the change is pure non-empty scalars — comments, blank keys,
    spacing and line endings the GM wrote all survive — and the
    re-serializing _page_text for anything else. `meta` is the parsed
    frontmatter (already carrying the change for the fallback path)."""
    surgical = _set_frontmatter_scalars(raw, changes, newline)
    if surgical is not None:
        return surgical
    return _page_text(meta, body, newline)


def _id_list(value) -> list[str]:
    """A frontmatter/state list of place ids; a scalar degrades to a
    one-item list (readers accept both; new writes are always lists)."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _page_snapshot(path: Path, expected: str | None) -> tuple[str, str, str]:
    """(page text, base revision, newline style) from ONE byte read:
    hashing a separate read after the parse could pair old content with a
    newer revision and let publication accept a stale write over an
    external edit. The decode applies universal-newline translation like
    read_text, so a CRLF hand-edit still parses its frontmatter; the
    file's dominant newline (by COUNT, not mere presence — one pasted
    CRLF line in an LF page must not flip the whole file) rides along so
    a byte-preserving write (ticket 10) can restore the endings the GM's
    editor wrote."""
    data = path.read_bytes()
    revision = expected or hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    crlf = data.count(b"\r\n")
    return text, revision, ("\r\n" if crlf > data.count(b"\n") - crlf
                            else "\n")


# --------------------------------------------------------------- attachments


def _map_attachments(name: str) -> list[dict]:
    """Every map attachment with its explicit/implicit place references:
    the companion note's `place:` wiki-link (the human-readable join) and
    `place_id:` (the precise one, the place-centered view)."""
    d = spine.campaign_dir(name)
    out = []
    for map_kind, scale in (("cities", "city map"), ("sites", "battlemap")):
        base = d / "maps" / map_kind
        if not base.is_dir():
            continue
        for slug_dir in sorted(base.iterdir()):
            if not slug_dir.is_dir():
                continue
            note = slug_dir / f"{slug_dir.name}.md"
            if not note.is_file():
                continue
            try:
                meta, _body = _frontmatter_of(
                    note.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, PlaceError):
                meta = None
            meta = meta if isinstance(meta, dict) else {}
            place_link = str(meta.get("place") or "").strip()
            if place_link.startswith("[[") and place_link.endswith("]]"):
                place_link = place_link[2:-2].split("|")[0].strip()
            out.append({
                "slug": slug_dir.name,
                "kind": map_kind.rstrip("s"),
                "scale": scale,
                "note_file": f"maps/{map_kind}/{slug_dir.name}/{slug_dir.name}.md",
                "dir": f"maps/{map_kind}/{slug_dir.name}",
                "place_ref": place_link or "",
                "place_id": str(meta.get("place_id") or "").strip(),
            })
    return out


def _fingerprint(name: str) -> str:
    """The input fingerprint for one view build: stat-only (rel, mtime, size)
    over every file the sections read, plus the vault fingerprint. Keyed
    into a cache by ALL consumed revisions or not at all — currently the
    view is built per request (the plan: measure before caching)."""
    d = spine.campaign_dir(name)
    stat_files = ("simulation/state.yaml", "simulation/knowledge.yaml",
                  "simulation/party-knowledge.yaml")
    out = []
    for rel in stat_files:
        p = d / rel
        if p.is_file():
            try:
                st = p.stat()
            except OSError:
                continue
            out.append((rel, st.st_mtime_ns, st.st_size))
    for pattern in ("simulation/proposals/*/proposal.yaml",
                    "simulation/forecasts/*/manifest.yaml",
                    "simulation/forecasts/*/week-*.yaml"):
        for p in sorted(d.glob(pattern)):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append((p.relative_to(d).as_posix(), st.st_mtime_ns,
                        st.st_size))
    return repr(sorted(out + [tuple(t) for t in links._fingerprint(d)]))


# ----------------------------------------------------------------- registry


def registry(name: str) -> dict:
    """Every place record in the campaign, normalized into identity groups.

    groups   [{id, name, kind, aliases, parent_place_id, explicit,
               provisional, association, lore, sim, maps}]
    by_id    group id -> group (conflicted ids included, flagged)
    findings [{rule, level, message, source}] — advisory
    conflicts [{id, files}] — two+ lore pages claiming one place_id
    """
    spine.require_campaign(name)
    state = spine.load_state(name)
    sim_places = spine.state_places(state)
    findings: list[dict] = []

    def finding(rule, level, message, source=""):
        findings.append({"rule": rule, "level": level,
                         "message": message, "source": source})

    # --- lore pages (identity owners) ---
    lore_records = []
    try:
        pages = spine.entity_pages(name)
    except spine.CampaignFileError as e:
        pages = []
        finding("places/lore-unreadable", "error", str(e), "lore/entities/")
    for p in pages:
        if p["kind"] != "place":
            continue
        lore_records.append(p)

    conflicts: list[dict] = []
    by_place_id: dict[str, list[dict]] = {}
    for p in lore_records:
        if p.get("place_id"):
            by_place_id.setdefault(p["place_id"], []).append(p)
    for pid, claimants in sorted(by_place_id.items()):
        if len(claimants) > 1:
            files = sorted(c["file"] for c in claimants)
            conflicts.append({"id": pid, "files": files})
            finding("places/duplicate-id", "error",
                    f"{len(claimants)} place pages claim place_id "
                    f"'{pid}' ({', '.join(files)}) — identity is a "
                    "conflict, never first-match-wins")

    # --- map attachments ---
    attachments = _map_attachments(name)
    idx = None
    try:
        idx = links.build_index(name)
    except spine.CampaignFileError as e:
        finding("places/links-unreadable", "error", str(e), "lore/entities/")

    def lore_page_by_file(file: str):
        for p in lore_records:
            if p["file"] == file:
                return p
        return None

    attach_by_page: dict[str, list[dict]] = {}
    attach_by_id: dict[str, list[dict]] = {}
    unattached: list[dict] = []
    for a in attachments:
        if a["place_id"]:
            attach_by_id.setdefault(a["place_id"], []).append(a)
            continue
        if a["place_ref"] and idx is not None:
            target = idx["resolve"].get(links.normalize_key(a["place_ref"]))
            page = lore_page_by_file(target["file"]) if target else None
            if page is not None:
                attach_by_page.setdefault(page["file"], []).append(a)
                continue
        if a["place_ref"] or a["place_id"]:
            unattached.append(a)

    # --- build groups ---
    def make_group(**kw):
        return {
            "id": "", "name": "", "kind": "place", "aliases": [],
            "parent_place_id": "", "explicit": False, "provisional": True,
            "association": None, "lore": None, "sim": None, "maps": [],
            **kw,
        }

    groups: list[dict] = []
    sim_taken: set[str] = set()

    # 1. explicit lore ids own identity
    for pid, claimants in by_place_id.items():
        page = claimants[0]
        sim = next((s for s in sim_places if s["id"] == pid), None)
        if sim is not None:
            sim_taken.add(sim["id"])
        groups.append(make_group(
            id=pid, explicit=True, provisional=False,
            name=page["name"], kind=page["kind"] or "place",
            aliases=list(page["aliases"]),
            parent_place_id=page.get("parent_place_id") or "",
            lore=page, sim=sim, association="explicit" if sim else None,
            maps=list(attach_by_id.get(pid, []))
            + list(attach_by_page.get(page["file"], []))))

    # 2. lore pages without an id: suggested sim join by unique normalized
    # name/alias, else a provisional lore-only record
    for page in lore_records:
        if page.get("place_id"):
            continue
        keys = {links.normalize_key(page["name"])}
        keys |= {links.normalize_key(a) for a in page["aliases"]}
        keys.discard("")
        matches = [s for s in sim_places if s["id"] not in sim_taken and (
            links.normalize_key(s["name"]) in keys
            or any(links.normalize_key(a) in keys
                   for a in (s.get("aliases") or [])))]
        if len(matches) == 1:
            sim = matches[0]
            sim_taken.add(sim["id"])
            groups.append(make_group(
                id=sim["id"], name=page["name"],
                kind=page["kind"] or sim.get("kind") or "place",
                aliases=list(page["aliases"]),
                parent_place_id=page.get("parent_place_id") or "",
                lore=page, sim=sim, association="suggested",
                maps=list(attach_by_page.get(page["file"], [])))
            )
        else:
            if len(matches) > 1:
                finding("places/ambiguous-name", "warning",
                        f"place page '{page['name']}' matches "
                        f"{len(matches)} simulated places by name "
                        f"({', '.join(m['name'] for m in matches)}) — no "
                        "auto-merge; link it explicitly",
                        page["file"])
            stem = Path(page["file"]).stem
            groups.append(make_group(
                id=f"page:{stem}", name=page["name"],
                kind=page["kind"] or "place",
                aliases=list(page["aliases"]),
                parent_place_id=page.get("parent_place_id") or "",
                lore=page,
                maps=list(attach_by_page.get(page["file"], []))))

    # 3. simulated places no lore record claimed — provisional sim records
    # (explicit-id map attachments still join: a map can attach to a place
    # the DM has not written a lore page for yet)
    for sim in sim_places:
        if sim["id"] in sim_taken:
            continue
        groups.append(make_group(
            id=sim["id"], name=sim["name"],
            kind=sim.get("kind") or "place",
            aliases=[str(a) for a in (sim.get("aliases") or [])],
            lore=None, sim=sim,
            maps=list(attach_by_id.get(sim["id"], []))))

    for pid, atts in sorted(attach_by_id.items()):
        if pid not in by_place_id and \
                not any(g["sim"] and g["sim"]["id"] == pid for g in groups):
            finding("places/unknown-map-place-id", "warning",
                    f"map attachment {atts[0]['note_file']} carries "
                    f"place_id '{pid}', which no place record owns",
                    atts[0]["note_file"])
    for a in unattached:
        finding("places/unresolved-map-place", "warning",
                f"map attachment {a['note_file']} references place "
                f"'{a['place_ref'] or a['place_id']}', which resolves to no "
                "place page — attach it from the place page or fix the note",
                a["note_file"])

    # duplicate display names across groups stay DISTINCT (two "New Town"s
    # never merge) but are worth a finding so the DM sees the ambiguity
    by_name: dict[str, list[dict]] = {}
    for g in groups:
        by_name.setdefault(links.normalize_key(g["name"]), []).append(g)
    for key, same in sorted(by_name.items()):
        if key and len(same) > 1:
            finding("places/duplicate-name", "warning",
                    f"{len(same)} places are named '{same[0]['name']}' "
                    f"({', '.join(g['id'] for g in same)}) — they stay "
                    "distinct; aliases decide what a wiki-link resolves to")

    groups.sort(key=lambda g: (not g["explicit"], g["name"].lower()))

    # unknown place references become findings with their source record —
    # never silent drops, and never hard errors (a hand-edit in one fact
    # must not 500 the whole page)
    known = {g["id"] for g in groups if not g["id"].startswith("page:")}

    def unknown_ref(where, ids, source):
        for pid in ids:
            if pid and pid not in known:
                finding("places/unknown-reference", "warning",
                        f"{where} references place id '{pid}', which no "
                        "place record owns", source)

    unknown_ref("parent_place_id of a place",
                [g["parent_place_id"] for g in groups],
                "lore/entities/")
    try:
        pages = spine.entity_pages(name)
    except spine.CampaignFileError:
        pages = []
    for page in pages:
        for rel in ("resides_at", "present_at"):
            unknown_ref(f"{rel} on '{page['name']}'",
                        _id_list(page.get(rel)), page["file"])
    for f in state.get("factions") or []:
        if isinstance(f, dict):
            unknown_ref(f"faction '{f.get('id')}' interest",
                        _id_list(f.get("interest_place_ids")),
                        "simulation/state.yaml")
            home = f.get("place")
            if home and home not in known:
                finding("places/unknown-reference", "warning",
                        f"faction '{f.get('id')}' names home place "
                        f"'{home}', which no place record owns",
                        "simulation/state.yaml")
    for ev in state.get("events") or []:
        if isinstance(ev, dict):
            unknown_ref("event", _id_list(ev.get("place_ids")),
                        "simulation/state.yaml")
    for fact in knowledge.load(name).get("facts", []):
        unknown_ref(f"fact '{fact.get('id')}'",
                    _id_list(fact.get("place_ids")), knowledge.KNOWLEDGE_FILE)

    findings.extend(_parent_cycle_findings(
        {"groups": groups, "by_id": {g["id"]: g for g in groups}}))

    return {"groups": groups,
            "by_id": {g["id"]: g for g in groups},
            "findings": findings,
            "conflicts": conflicts}


def _descendant_ids(reg: dict, root_id: str) -> list[str]:
    """root_id + every descendant via parent_place_id, cycle-safe. A cycle
    cannot make this loop forever: once visited, a node never re-enters."""
    by_parent: dict[str, list[str]] = {}
    for g in reg["groups"]:
        parent = g["parent_place_id"]
        if parent:
            by_parent.setdefault(parent, []).append(g["id"])
    out, stack = [root_id], [root_id]
    seen = {root_id}
    while stack:
        for child in by_parent.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                out.append(child)
                stack.append(child)
    return out


def _parent_cycle_findings(reg: dict) -> list[dict]:
    """Cyclic parent chains (A->B->A) cannot be walked to a root — each
    cycle member gets one finding. Detection is per-component: walk up from
    each group with a bound on steps; if we re-visit the start, it cycles."""
    out = []
    reported: set[frozenset] = set()
    for g in reg["groups"]:
        chain, cur, cycles = [], g["id"], False
        for _ in range(len(reg["groups"]) + 1):
            nxt = reg["by_id"].get(cur, {}).get("parent_place_id") or ""
            if not nxt or nxt in chain:
                cycles = nxt in chain
                break
            chain.append(nxt)
            cur = nxt
        else:
            cycles = True  # walked the whole registry without reaching a root
        if cycles:
            # report the CYCLE only: the walk's lead-in (nodes that merely
            # point INTO the loop, like C in C -> A -> B -> A) is not a
            # member — the cycle is the repeated portion of the chain
            members = chain[chain.index(nxt):] if nxt in chain else chain
            ring = frozenset(members)
            if ring not in reported:
                reported.add(ring)
                out.append({
                    "rule": "places/parent-cycle", "level": "error",
                    "message": "parent_place_id chain is cyclic through: "
                               + ", ".join(sorted(ring)),
                    "source": g["lore"]["file"] if g.get("lore") else g["id"],
                })
    return out


# ---------------------------------------------------------------- view parts


def _event_items(name: str, state: dict, state_rev: str, ids: set[str],
                 window: str, limit: int, offset: int) -> tuple[list, dict]:
    """Events explicitly located at one of `ids` (never inferred from an
    actor's current place). Old events without place_ids stay unlocated —
    historical unknown locations remain unknown. Each item carries a
    read-only locator (state revision + list index), not a fake stable id.
    state_rev is the revision of the EXACT bytes `state` was parsed from —
    capturing it separately (a later file read) could pair an old index
    with a newer file's hash and let a later association pass the revision
    check while pointing at a different event."""
    clock = ((state.get("meta") or {}).get("clock") or {})
    current = clock.get("week")
    hits = []
    for i, ev in enumerate(state.get("events") or []):
        if not isinstance(ev, dict):
            continue
        eplaces = set(_id_list(ev.get("place_ids")))
        if not (eplaces & ids):
            continue
        week = ev.get("week")
        if window == "recent" and isinstance(current, int) \
                and isinstance(week, int) and week <= current - RECENT_WINDOW_WEEKS:
            continue
        hits.append((i, ev))
    total = len(hits)
    counts = {"total": total, "returned": min(limit, max(0, total - offset)),
              "more": max(0, total - offset - limit)}
    out = []
    for i, ev in hits[offset:offset + limit]:
        item = {"week": ev.get("week"), "actor": ev.get("actor") or "world",
                "text": str(ev.get("text") or ""),
                "relationship": "occurred_at", "canonical": True,
                "place_ids": _id_list(ev.get("place_ids"))}
        for k in ("source", "effect", "project_id", "progress_before",
                  "progress_after"):
            if ev.get(k) is not None:
                item[k] = ev.get(k)
        if ev.get("id"):
            item["id"] = str(ev["id"])
        else:
            item["locator"] = {"index": i, "state_revision": state_rev}
        out.append(item)
    return out, counts


def _fact_items(name: str, ids: set[str]) -> list[dict]:
    """Facts ABOUT the place, with the visibility badge and the
    party-disclosure state read from the separate disclosure record —
    `visibility: public` never implies the party knows it."""
    disclosed = {r.get("fact_id") for r in
                 briefing.load_disclosure(name).get("revealed", [])}
    out = []
    for fact in knowledge.load(name).get("facts", []):
        if fact.get("status") not in (None, "current"):
            continue
        if not (set(_id_list(fact.get("place_ids"))) & ids):
            continue
        out.append({
            "id": fact.get("id"), "text": fact.get("text"),
            "relationship": "about", "canonical": True,
            "visibility": fact.get("visibility") or "dm_only",
            "disclosed": fact.get("id") in disclosed,
            "source": {"file": knowledge.KNOWLEDGE_FILE,
                       "record_id": fact.get("id")},
        })
    return out


def _faction_items(state: dict, ids: set[str]) -> list[dict]:
    sim_places = spine.state_places(state)
    primary = sim_places[0]["id"] if sim_places else None
    out = []
    for f in state.get("factions") or []:
        if not isinstance(f, dict):
            continue
        home = f.get("place")
        if home is None and primary is not None:
            # no explicit place -> the engine's faction_place fallback: the
            # primary place (the file's first). Labeled not-explicit so the
            # page shows it as the default, not a stated base.
            home = primary
        if home in ids:
            out.append({"id": f.get("id"), "name": f.get("name"),
                        "relationship": "home", "canonical": True,
                        "explicit": f.get("place") is not None,
                        "source": {"file": "simulation/state.yaml",
                                   "record_id": f.get("id")}})
        for pid in _id_list(f.get("interest_place_ids")):
            if pid in ids:
                out.append({"id": f.get("id"), "name": f.get("name"),
                            "relationship": "interest", "canonical": True,
                            "explicit": True,
                            "source": {"file": "simulation/state.yaml",
                                       "record_id": f.get("id")}})
    return out


def _project_items(state: dict, ids: set[str]) -> list[dict]:
    out = []
    for p in state.get("projects") or []:
        if not isinstance(p, dict):
            continue
        if not (set(_id_list(p.get("place_ids"))) & ids):
            continue
        out.append({
            "id": p.get("id"), "title": p.get("title"),
            "objective": p.get("objective"),
            "faction_id": p.get("faction_id"),
            "progress": p.get("progress"), "target": p.get("target"),
            "status": p.get("status"), "relationship": "operates_at",
            "canonical": True,
            "completion": p.get("completion"),
            "completion_blocked": p.get("completion_blocked"),
            "source": {"file": "simulation/state.yaml",
                       "record_id": p.get("id")},
        })
    return out


def _entity_items(name: str, ids: set[str]) -> list[dict]:
    out = []
    try:
        pages = spine.entity_pages(name)
    except spine.CampaignFileError:
        return []
    for p in pages:
        for rel in ("resides_at", "present_at"):
            if set(_id_list(p.get(rel))) & ids:
                out.append({
                    "name": p["name"], "file": p["file"],
                    "relationship": rel, "canonical": True,
                    "note": p.get("note") or "",
                    "source": {"file": p["file"]},
                })
                break
    return out


def _pending_items(name: str, ids: set[str]) -> list[dict]:
    """The one pending proposal, when its ADDED records explicitly touch the
    place — labeled pending, canonical: false, never part of canon counts."""
    pending = proposals.find_pending(name)
    if not pending:
        return []
    diff_path = spine.campaign_dir(name) / proposals.PROPOSALS_DIR / \
        str(pending.get("proposal_id")) / "artifacts" / "diff.json"
    try:
        diff = json.loads(diff_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for ev in diff.get("events_added") or []:
        if set(_id_list(ev.get("place_ids"))) & ids:
            out.append({"kind": "event", "week": ev.get("week"),
                        "actor": ev.get("actor"), "text": ev.get("text"),
                        "relationship": "occurred_at", "canonical": False,
                        "hypothetical": False,
                        "proposal_id": pending.get("proposal_id"),
                        "proposal_kind": pending.get("kind"),
                        "proposal_status": pending.get("status")})
    for pr in diff.get("projects_added") or []:
        if set(_id_list(pr.get("place_ids"))) & ids:
            out.append({"kind": "project", "title": pr.get("title"),
                        "faction_id": pr.get("faction_id"),
                        "relationship": "operates_at", "canonical": False,
                        "hypothetical": False,
                        "proposal_id": pending.get("proposal_id"),
                        "proposal_kind": pending.get("kind"),
                        "proposal_status": pending.get("status")})
    return out


def _forecast_items(name: str, ids: set[str]) -> list[dict]:
    """The ACTIVE forecast's projected events for this place — labeled
    hypothetical: true and never mixed into canon history."""
    active = forecasts.find_active(name)
    if not active:
        return []
    bdir = spine.campaign_dir(name) / "simulation" / "forecasts" / \
        str(active.get("forecast_id"))
    out = []
    for fn in sorted(bdir.glob("week-*.yaml")):
        try:
            week = yaml.safe_load(fn.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        for ev in ((week or {}).get("diff") or {}).get("events_added") or []:
            if set(_id_list(ev.get("place_ids"))) & ids:
                out.append({"kind": "event", "week": ev.get("week"),
                            "actor": ev.get("actor"), "text": ev.get("text"),
                            "relationship": "occurred_at", "canonical": False,
                            "hypothetical": True,
                            "forecast_id": active.get("forecast_id")})
    return out


def _mention_items(name: str, group: dict, limit: int,
                   offset: int) -> tuple[list[dict], dict]:
    """Wiki-link MENTIONS of the place's page — a note discussing the place
    is a mention, never residence/ownership/an event location. Carries a
    source excerpt."""
    if not group.get("lore"):
        return [], {"total": 0, "returned": 0, "more": 0}
    page_file = group["lore"]["file"]
    try:
        backlinks = links.backlinks_for(name, page_file)
    except spine.CampaignFileError:
        return [], {"total": 0, "returned": 0, "more": 0}
    total = len(backlinks)
    d = spine.campaign_dir(name)
    out = []
    for b in backlinks[offset:offset + limit]:
        excerpt = ""
        src = d / Path(*b["source_file"].split("/"))
        try:
            lines = src.read_text(encoding="utf-8").splitlines()
            if 0 < b.get("line", 0) <= len(lines):
                excerpt = lines[b["line"] - 1].strip()[:200]
        except (OSError, UnicodeDecodeError):
            pass
        out.append({
            "relationship": "mentions", "canonical": True,
            "source": {"file": b["source_file"], "line": b["line"],
                       "source_kind": b["source_kind"]},
            "source_name": b.get("source_name") or "",
            "excerpt": excerpt,
        })
    return out, {"total": total,
                 "returned": len(out),
                 "more": max(0, total - offset - limit)}


# --------------------------------------------------------------------- view


def place_view(name: str, place_id: str, *, window: str = "recent",
               include_descendants: bool = False,
               event_limit: int = DEFAULT_LIMIT, event_offset: int = 0,
               mention_limit: int = DEFAULT_LIMIT,
               mention_offset: int = 0) -> dict:
    """The consolidated DM view for one place. Unknown id -> PlaceNotFound
    (404); an id two lore pages both claim -> PlaceError (422, identity is
    a conflict, never first-match-wins)."""
    reg = registry(name)
    group = reg["by_id"].get(str(place_id or "").strip())
    if group is None:
        raise PlaceNotFound(f"no such place: {place_id}")
    for c in reg["conflicts"]:
        if c["id"] == group["id"]:
            raise PlaceError(
                f"place_id '{group['id']}' is claimed by "
                f"{len(c['files'])} place pages ({', '.join(c['files'])}) — "
                "fix the duplicate before this page can render")

    ids = {group["id"]}
    if include_descendants:
        ids = set(_descendant_ids(reg, group["id"]))

    # one byte snapshot for the events list AND the locator revision the
    # page hands out: parsing one read and hashing another could pair an
    # old event index with a newer file's revision (DND-10)
    state_path = spine.campaign_dir(name) / "simulation" / "state.yaml"
    try:
        state_data = state_path.read_bytes()
    except OSError:
        state_data = None
    if state_data is None:
        state_rev, state = publication.ABSENT, {}
    else:
        state_rev = hashlib.sha256(state_data).hexdigest()
        try:
            state = yaml.safe_load(state_data.decode("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            first = str(e).splitlines()[0] if str(e) else type(e).__name__
            raise spine.CampaignFileError(
                f"state.yaml does not parse ({first}) — a hand-edit "
                "slipped? Fix or delete the file, or restore it with "
                "git checkout -- simulation/state.yaml")
        state = state if isinstance(state, dict) else {}

    maps = list(group["maps"])
    preferred = (group.get("lore") or {}).get("preferred_map") or ""

    def is_pref(m):
        # exact note path, exact artifact inside the attachment dir, or the
        # dir itself — a bare directory PREFIX must not match a sibling
        # ("maps/cities/ferry" must not claim "maps/cities/ferry-inn/...")
        if not preferred:
            return False
        return m["note_file"] == preferred or             m["dir"] == preferred.rstrip("/") or             preferred.startswith(m["dir"].rstrip("/") + "/")

    maps.sort(key=lambda m: (0 if is_pref(m) else 1, m["slug"]))
    for m in maps:
        m["preferred"] = is_pref(m)

    events, event_counts = _event_items(name, state, state_rev, ids, window,
                                        event_limit, event_offset)
    mentions, mention_counts = _mention_items(name, group,
                                              mention_limit, mention_offset)
    try:
        facts = _fact_items(name, ids)
        fact_error = None
    except Exception as e:  # noqa: BLE001 — one damaged source, not the page
        facts, fact_error = [], str(e)

    parent = reg["by_id"].get(group["parent_place_id"]) \
        if group["parent_place_id"] else None
    children = [g for g in reg["groups"]
                if g["parent_place_id"] == group["id"]]

    findings = list(reg["findings"])

    return {
        "place": {
            "id": group["id"], "name": group["name"], "kind": group["kind"],
            "aliases": group["aliases"],
            "parent_place_id": group["parent_place_id"],
            "explicit": group["explicit"], "provisional": group["provisional"],
            "association": group["association"],
            "lore": ({
                "file": group["lore"]["file"], "name": group["lore"]["name"],
                "aliases": group["lore"]["aliases"],
                "body": group["lore"].get("body") or "",
                "geography_ref": group["lore"].get("geography_ref"),
                "preferred_map": group["lore"].get("preferred_map") or "",
             } if group.get("lore") else None),
            "sim": ({
                "id": group["sim"]["id"], "name": group["sim"]["name"],
                "kind": group["sim"].get("kind"),
             } if group.get("sim") else None),
        },
        "parent": ({"id": parent["id"], "name": parent["name"]}
                   if parent else None),
        "children": [{"id": c["id"], "name": c["name"]} for c in children],
        "revision": _fingerprint(name),
        "maps": [{"slug": m["slug"], "scale": m["scale"],
                  "kind": m["kind"], "note_file": m["note_file"],
                  "dir": m["dir"], "place_ref": m["place_ref"],
                  "place_id": m["place_id"], "preferred": m["preferred"]}
                 for m in maps],
        "entities": _entity_items(name, ids),
        "factions": _faction_items(state, ids),
        "projects": _project_items(state, ids),
        "events": events,
        "knowledge": facts,
        "pending": _pending_items(name, ids),
        "forecasts": _forecast_items(name, ids),
        "mentions": mentions,
        "findings": findings,
        "counts": {"events": event_counts, "mentions": mention_counts},
        **({"section_errors": {"knowledge": fact_error}}
           if fact_error else {}),
    }


def list_places(name: str) -> dict:
    """The Places navigation entry: every explicit and provisional record,
    the suggested joins that would benefit from linking, conflict counts."""
    reg = registry(name)
    places = []
    suggested = []
    for g in reg["groups"]:
        places.append({
            "id": g["id"], "name": g["name"], "kind": g["kind"],
            "explicit": g["explicit"], "provisional": g["provisional"],
            "association": g["association"],
            "lore_file": (g.get("lore") or {}).get("file"),
            "sim_id": (g.get("sim") or {}).get("id"),
            "maps": len(g["maps"]),
            "parent_place_id": g["parent_place_id"],
            "conflict": any(c["id"] == g["id"] for c in reg["conflicts"]),
        })
        if g["association"] == "suggested":
            suggested.append({
                "id": g["id"], "name": g["name"],
                "lore_file": g["lore"]["file"],
                "sim_id": g["sim"]["id"], "sim_name": g["sim"]["name"]})
    return {
        "places": places,
        "suggested": suggested,
        "conflicts": reg["conflicts"],
        "findings": reg["findings"],
        "counts": {"places": len(places), "suggested": len(suggested),
                   "conflicts": len(reg["conflicts"])},
    }


# ----------------------------------------------------------------- mutations


def _locked_publish(name: str, writes: list[dict], operation: str,
                    base_revisions: dict) -> dict:
    """publish, with the revision conflict translated to the place error
    the routes map to 409. Caller holds the campaign lock."""
    try:
        return publication.publish(name, writes, operation=operation,
                                   base_revisions=base_revisions)
    except publication.RevisionConflict as e:
        raise PlaceConflict(str(e))


def _require_group(name: str, place_id: str) -> dict:
    reg = registry(name)
    group = reg["by_id"].get(str(place_id or "").strip())
    if group is None:
        raise PlaceNotFound(f"no such place: {place_id}")
    for c in reg["conflicts"]:
        if c["id"] == group["id"]:
            raise PlaceError(
                f"place_id '{group['id']}' is claimed by "
                f"{len(c['files'])} place pages — fix the duplicate first")
    return group


def _require_lore_page(group: dict) -> dict:
    page = group.get("lore")
    if page is None:
        raise PlaceError(
            f"place '{group['id']}' has no lore page yet — create "
            "lore/entities/<name>.md with kind: place first (the page owns "
            "identity; the simulation record is provisional)")
    return page


def link(name: str, place_id: str, *, page: str | None = None,
         expected_page_revision: str | None = None) -> dict:
    """Confirm an association by persisting `place_id` into a lore page's
    frontmatter (the one-time 'Link place records' action).

    `page` is the entity page stem for groups that do not own a page yet
    (a lore-only provisional record links itself; a sim-only place gets a
    new page ONLY if the caller creates one — this action links, it does
    not author lore). The group's suggested sim join becomes explicit
    because the page's id now IS the sim id. Renames later keep this id."""
    group = _require_group(name, place_id)
    minted = False
    if group.get("lore") is not None:
        target_file = group["lore"]["file"]
        stem = Path(target_file).stem
        if page and page != stem:
            raise PlaceError(
                f"place '{group['id']}' already has a lore page "
                f"({target_file}); refusing to also claim page '{page}'")
        minted = group["id"].startswith("page:")
    else:
        if not page:
            raise PlaceError(
                "this place has no lore page — pass the page stem to "
                "claim an existing kind: place page")
        target_file = f"lore/entities/{spine.entity_page_filename(page)}"
        minted = True
    path = spine.campaign_dir(name) / Path(*target_file.split("/"))
    if not path.is_file():
        raise PlaceNotFound(f"no such entity page: {target_file}")
    # one byte snapshot for BOTH the content and its baseline (see
    # _page_snapshot): hashing after the parse could pair the OLD text
    # with the NEW revision — publication would then accept the stale
    # write and destroy the edit
    raw, base_revision, newline = _page_snapshot(path, expected_page_revision)
    meta, body = _frontmatter_of(raw)
    meta = meta or {}
    existing = str(meta.get("place_id") or "").strip()
    if existing and existing != group["id"]:
        raise PlaceError(
            f"{target_file} already claims place_id '{existing}' — a page "
            "owns one identity; resolve the conflict by hand or fix the "
            "duplicate first")
    kind = str(meta.get("kind") or "").strip()
    if kind and kind != "place":
        # the registry only lets kind: place pages own identities: a
        # place_id on an NPC page would return success with an id the
        # place view can never resolve
        raise PlaceError(
            f"{target_file} is kind: {kind} — only kind: place pages can "
            "own a place identity; change the page's kind first")
    if minted:
        # a lore-only provisional record has no durable identity yet — the
        # link mints one (place-<uuid>) and it survives every later rename
        final_id = "place-" + uuid.uuid4().hex[:8]
    else:
        # adopt the simulation's id: one file write, no state rewrite, and
        # faction.place references keep resolving unchanged
        final_id = group["id"]
    changes = {"place_id": final_id}
    if not kind:
        changes["kind"] = "place"
    result = _locked_publish(
        name, [{"path": target_file,
                "text": _changed_page_text(raw, meta, body, changes,
                                           newline)}],
        operation=f"link place records ({target_file} -> {final_id})",
        base_revisions={target_file: base_revision})
    return {"linked": final_id, "minted": minted, "page": target_file,
            **result}


def patch(name: str, place_id: str, *, name_: str | None = None,
          parent_place_id: str | None = None, aliases: list | None = None,
          preferred_map: str | None = None,
          expected_page_revision: str | None = None) -> dict:
    """Rename / re-parent / re-alias / set the preferred map — all on the
    owning lore page's frontmatter. The place_id NEVER changes on rename
    (the old display name rides into aliases so human wiki-links keep
    working). Unrelated frontmatter keys and the body are preserved.

    Revision contract: the page is re-read under the campaign lock and
    merged (only the patched fields are touched), so an external Obsidian
    edit is never overwritten field-by-field; the revision check still
    guards the read->publish window. Passing expected_page_revision (the
    revision the DM's view rendered) makes an external edit a 409 instead
    of a merge."""
    group = _require_group(name, place_id)
    page = _require_lore_page(group)
    path = spine.campaign_dir(name) / Path(*page["file"].split("/"))
    # one byte snapshot for BOTH the content and its baseline (see
    # _page_snapshot): hashing after the parse could pair old text with a
    # newer revision
    raw, base_revision, newline = _page_snapshot(path, expected_page_revision)
    meta, body = _frontmatter_of(raw)
    meta = meta or {}
    changed = []   # the response's labels (what the DM asked for)
    written = []   # the keys the write actually touches (a rename also
    # folds the old name into aliases — the write set, not the label set,
    # decides surgery vs re-serialization)
    if name_ is not None:
        new_name = str(name_).strip()
        if not new_name:
            raise PlaceError("a place needs a name")
        if new_name != page["name"]:
            old = page["name"]
            meta["name"] = new_name
            # the passed alias list (if any) MERGES with the fold — a
            # rename+aliases call must not silently drop the old name.
            # _id_list folds a scalar hand-edit ("aliases: The Old Mill")
            # the same way the page loader does; list + raw scalar was a
            # TypeError that made a readable page unrenamable
            alias_list = [str(a).strip() for a in
                          ((aliases if aliases is not None else [])
                           + _id_list(meta.get("aliases")))
                          if str(a).strip()]
            if old not in alias_list:
                alias_list.append(old)  # old name keeps resolving
            meta["aliases"] = alias_list
            changed.append("name")
            written += ["name", "aliases"]
    if aliases is not None:
        passed = [str(a).strip() for a in aliases if str(a).strip()]
        # after a rename in the same call, meta["aliases"] already carries
        # the folded old name — keep anything folded that the caller kept
        folded = _id_list(meta.get("aliases")) if "name" in changed else []
        meta["aliases"] = list(dict.fromkeys(
            passed + [a for a in folded if a.strip()
                      and a.strip() not in passed]))
        changed.append("aliases")
        written.append("aliases")
    if parent_place_id is not None:
        parent = str(parent_place_id).strip()
        if parent:
            if parent.startswith("page:"):
                raise PlaceError(
                    f"parent_place_id '{parent}' is a provisional page "
                    "locator — link that place first (the Link action "
                    "mints a durable id), then re-parent")
            reg = registry(name)
            if parent not in reg["by_id"]:
                raise PlaceError(
                    f"parent_place_id '{parent}' matches no place — create "
                    "or link it first")
            if parent == group["id"]:
                raise PlaceError("a place cannot be its own parent")
            # cycle check: walk up from the proposed parent
            seen, cur = {group["id"], parent}, parent
            while cur:
                nxt = reg["by_id"].get(cur, {}).get("parent_place_id") or ""
                if not nxt:
                    break
                if nxt in seen:
                    raise PlaceError(
                        f"parent_place_id '{parent}' would create a parent "
                        "cycle")
                seen.add(nxt)
                cur = nxt
        meta["parent_place_id"] = parent or None
        changed.append("parent_place_id")
        written.append("parent_place_id")
    if preferred_map is not None:
        pref = str(preferred_map).strip()
        if pref:
            d = spine.campaign_dir(name)
            try:
                target = publication._safe_target(d, pref)
            except publication.PublicationError as e:
                # an unsafe path is the CALLER's input problem — a 422,
                # never the catch-all 500 the raw error mapped to
                raise PlaceError(str(e))
            rel = target.relative_to(d.resolve()).as_posix()
            if not rel.startswith("maps/") or not target.exists():
                raise PlaceError(
                    f"preferred_map must be an existing campaign-relative "
                    f"map artifact under maps/ — got '{pref}'")
            pref = rel
        meta["preferred_map"] = pref or None
        changed.append("preferred_map")
        written.append("preferred_map")
    if not changed:
        return {"patched": [], "state": "noop", "transaction": None}
    result = _locked_publish(
        name, [{"path": page["file"],
                "text": _changed_page_text(
                    raw, meta, body, {k: meta[k] for k in written},
                    newline)}],
        operation=f"patch place {group['id']} ({', '.join(changed)})",
        base_revisions={page["file"]: base_revision})
    return {"patched": changed, **result}


_ASSOCIATION_SOURCES = {
    # source_kind -> (relationships, meaning)
    "entity": ("resides_at", "present_at"),
    "event": ("occurred_at",),
    "fact": ("about",),
    "faction": ("interest",),
    "map": ("depicts",),
}


def associate(name: str, place_id: str, *, source_kind: str,
              source_id: str, relationship: str, add: bool = True,
              expected_revision: str | None = None) -> dict:
    """One typed association update to an ALLOWLISTED source record —
    never arbitrary file paths or YAML patches. source_kind/id:
      entity  page stem            -> resides_at / present_at (page frontmatter)
      event   state event locator  -> occurred_at (state event place_ids)
      fact    fact id              -> about (knowledge store place_ids)
      faction faction id           -> interest (state interest_place_ids)
      map     map slug, qualified when ambiguous ("cities/<slug>" or
              "sites/<slug>") -> depicts (companion note + params place_id)
    Unknown place references are refused at write time here (the read model
    reports them as findings; writes must not mint new ones). Provisional
    page-locator ids ("page:<stem>") are refused too — link the place first
    so references stay durable across renames.

    Revision contract: expected_revision (the revision the caller's view
    rendered) makes an external edit a 409. REQUIRED for event sources —
    the locator is a list INDEX, and a state changed since the view could
    silently point it at a different event. For the other sources it is
    optional: the record is re-read under the campaign lock and merged, and
    the current revision is captured as the publish baseline anyway, so a
    mid-flight external edit is still a 409, never a silent overwrite.
    Map sources: expected_revision gates the companion NOTE; params.yaml
    always baselines its own freshly-read hash (never the note's)."""
    group = _require_group(name, place_id)
    pid = group["id"]
    if pid.startswith("page:"):
        raise PlaceError(
            f"place '{pid}' is a provisional page locator without a "
            "durable id — link it first (the Link action mints one), then "
            "associate; writing page: references would break on rename")
    if source_kind not in _ASSOCIATION_SOURCES:
        raise PlaceError(
            f"source_kind must be one of: "
            f"{', '.join(sorted(_ASSOCIATION_SOURCES))}")
    if relationship not in _ASSOCIATION_SOURCES[source_kind]:
        raise PlaceError(
            f"relationship '{relationship}' is not valid for "
            f"source_kind '{source_kind}' (one of: "
            f"{', '.join(_ASSOCIATION_SOURCES[source_kind])})")
    source_id = str(source_id or "").strip()
    if not source_id:
        raise PlaceError("source_id is required")

    d = spine.campaign_dir(name)

    if source_kind == "entity":
        fn = spine.entity_page_filename(source_id)
        rel = f"lore/entities/{fn}"
        path = d / Path(*rel.split("/"))
        if not path.is_file():
            raise PlaceNotFound(f"no such entity page: {rel}")
        # one byte snapshot for content + baseline (see _page_snapshot)
        raw, base_revision, newline = _page_snapshot(path, expected_revision)
        meta, body = _frontmatter_of(raw)
        meta = meta or {}
        current = _id_list(meta.get(relationship))
        meta[relationship] = ([p for p in current if p != pid] + [pid]) \
            if add else [p for p in current if p != pid]
        result = _locked_publish(
            name, [{"path": rel,
                    "text": _changed_page_text(
                        raw, meta, body,
                        {relationship: meta[relationship]}, newline)}],
            operation=f"{relationship} <- {pid} ({fn})",
            base_revisions={rel: base_revision})
        return {"source": rel, "relationship": relationship,
                "place_id": pid, **result}

    if source_kind in ("event", "faction"):
        # both live in simulation/state.yaml — one file, one publish.
        # The baseline is captured BEFORE the parse: an external edit in
        # between then makes the parse NEWER than the baseline, which the
        # publish-time check refuses — never the silent-overwrite order
        # (parse old, hash new)
        rel = "simulation/state.yaml"
        if source_kind == "event" and not expected_revision:
            raise PlaceError(
                "locating an event needs expected_revision (the "
                "state_revision from the locator the place view returned) — "
                "the locator is a list index, and a state changed since "
                "your view could point it at a different event")
        path = d / Path(*rel.split("/"))
        base_revision = expected_revision or publication.file_revision(path)
        state = spine.load_state(name)
        if source_kind == "event":
            try:
                index = int(source_id)
            except ValueError:
                raise PlaceError(
                    "event source_id is the event's list index (the "
                    "locator the place view returns), e.g. '12'")
            events = state.get("events") or []
            if not 0 <= index < len(events) or \
                    not isinstance(events[index], dict):
                raise PlaceNotFound(f"no event at index {index}")
            ev = events[index]
            current = _id_list(ev.get("place_ids"))
            ev["place_ids"] = ([p for p in current if p != pid] + [pid]) \
                if add else [p for p in current if p != pid]
            record = f"event[{index}]"
        else:
            fac = next((f for f in state.get("factions") or []
                        if isinstance(f, dict)
                        and f.get("id") == source_id), None)
            if fac is None:
                raise PlaceNotFound(f"no such faction: {source_id}")
            current = _id_list(fac.get("interest_place_ids"))
            fac["interest_place_ids"] = \
                ([p for p in current if p != pid] + [pid]) if add \
                else [p for p in current if p != pid]
            record = f"faction:{source_id}"
        result = _locked_publish(
            name, [{"path": rel, "text": spine.dump_yaml(state)}],
            operation=f"{relationship} <- {pid} ({record})",
            base_revisions={rel: base_revision})
        return {"source": rel, "record": record,
                "relationship": relationship, "place_id": pid, **result}

    if source_kind == "fact":
        # baseline BEFORE the load (same reasoning as the state branch):
        # parse-then-hash could accept a write over a newer external edit
        rel = knowledge.KNOWLEDGE_FILE
        base_revision = expected_revision or publication.file_revision(
            d / Path(*rel.split("/")))
        doc = knowledge.load(name)
        fact = next((f for f in doc.get("facts", [])
                     if f.get("id") == source_id), None)
        if fact is None:
            raise PlaceNotFound(f"no such fact: {source_id}")
        current = _id_list(fact.get("place_ids"))
        fact["place_ids"] = ([p for p in current if p != pid] + [pid]) \
            if add else [p for p in current if p != pid]
        result = _locked_publish(
            name, [{"path": rel, "text": spine.dump_yaml(doc)}],
            operation=f"fact {source_id} about <- {pid}",
            base_revisions={rel: base_revision})
        return {"source": rel, "record_id": source_id,
                "relationship": relationship, "place_id": pid, **result}

    # source_kind == "map": companion note + params.yaml in ONE publish —
    # the generated SVG/PNG/GeoJSON are never touched. A bare slug may be
    # ambiguous (maps/cities/ruins AND maps/sites/ruins): the dir-qualified
    # form ("cities/<slug>" or "sites/<slug>", optionally "maps/...") picks
    # one exactly; an ambiguous bare slug is REFUSED, never first-match.
    wanted = source_id.strip().strip("/")
    wanted = wanted.removeprefix("maps/")
    matches = [a for a in _map_attachments(name)
               if a["slug"] == wanted or a["dir"] == f"maps/{wanted}"]
    if len(matches) > 1:
        raise PlaceError(
            f"map '{source_id}' is ambiguous: "
            f"{', '.join(a['dir'] for a in matches)} — qualify it as "
            "'cities/<slug>' or 'sites/<slug>'")
    if not matches:
        raise PlaceNotFound(f"no such map attachment: {source_id}")
    att = matches[0]
    if not add and att["place_id"] and att["place_id"] != pid:
        # detaching from the wrong place would silently rewrite another
        # page's relationship
        raise PlaceError(
            f"map {att['dir']} is attached to place '{att['place_id']}', "
            f"not '{pid}' — detach it from its own place page")
    writes, base = [], {}
    note_path = d / Path(*att["note_file"].split("/"))
    if note_path.is_file():
        raw, base[att["note_file"]], newline = _page_snapshot(
            note_path, expected_revision)
        meta, body = _frontmatter_of(raw)
        meta = meta or {}
    else:
        # a brand-new note page has no bytes to preserve — _page_text
        # creates its frontmatter block
        raw, meta, body, newline = "", {}, "", "\n"
    meta["place_id"] = pid if add else None
    writes.append({"path": att["note_file"],
                   "text": _changed_page_text(
                       raw, meta, body, {"place_id": meta["place_id"]},
                       newline)})
    params_rel = f"{att['dir']}/params.yaml"
    params_path = d / Path(*params_rel.split("/"))
    if params_path.is_file():
        params_data = params_path.read_bytes()
        # params.yaml's baseline is its OWN hash — expected_revision is the
        # companion NOTE's revision and can never equal params' hash, so
        # applying it here made every caller-supplied revision a false 409
        # (ticket 22 P3-31). An absent params.yaml writes nothing, so it
        # carries no baseline at all.
        base[params_rel] = hashlib.sha256(params_data).hexdigest()
        try:
            params = yaml.safe_load(params_data.decode("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError):
            params = None
        if isinstance(params, dict):
            params["place_id"] = pid if add else None
            params = {k: v for k, v in params.items() if v is not None}
            writes.append({"path": params_rel,
                           "text": spine.dump_yaml(params)})
    result = _locked_publish(
        name, writes,
        operation=f"map {source_id} depicts <- {pid}",
        base_revisions=base)
    return {"source": att["note_file"], "relationship": relationship,
            "place_id": pid, **result}


# ----------------------------------------------------------------- markdown


def render_markdown(name: str, view: dict) -> str:
    """The DM read model as durable Markdown. Identifies itself as DM
    material; private / pending / hypothetical sections carry their labels.
    This is NOT a player handout — briefing.handout_markdown is the only
    deliberate sharing path."""
    p = view["place"]
    lines = [f"# Place — {p['name']} (DM material — not a player handout)",
             ""]
    bits = [f"id: `{p['id']}`", f"kind: {p['kind']}"]
    if p.get("association"):
        bits.append(f"simulation join: {p['association']}")
    if p.get("provisional"):
        bits.append("provisional (no explicit place_id yet)")
    lines.append("; ".join(bits))
    if p.get("aliases"):
        lines.append(f"Aliases: {', '.join(p['aliases'])}")
    if view.get("parent"):
        lines.append(f"Parent: {view['parent']['name']} "
                     f"(`{view['parent']['id']}`)")
    if view.get("children"):
        lines.append("Children: " + ", ".join(
            f"{c['name']} (`{c['id']}`)" for c in view["children"]))
    if p.get("lore"):
        lines.append(f"Lore page: `{p['lore']['file']}`")
    lines.append("")

    if view["maps"]:
        lines.append("## Maps")
        for m in view["maps"]:
            pref = " (preferred)" if m.get("preferred") else ""
            lines.append(f"- {m['scale']}{pref}: `{m['dir']}/`")
        lines.append("")
    if view["factions"]:
        lines.append("## Factions")
        for f in view["factions"]:
            lines.append(f"- {f['name'] or f['id']} — {f['relationship']}")
        lines.append("")
    if view["entities"]:
        lines.append("## People & organizations")
        for e in view["entities"]:
            lines.append(f"- {e['name']} — {e['relationship']} "
                         f"(`{e['file']}`)")
        lines.append("")
    if view["projects"]:
        lines.append("## Projects")
        for pr in view["projects"]:
            lines.append(f"- {pr['title'] or pr['id']} "
                         f"({pr['faction_id']}): {pr['progress']}/"
                         f"{pr['target']} — {pr['status']}")
        lines.append("")
    if view["events"] or view["counts"]["events"]["total"]:
        c = view["counts"]["events"]
        lines.append("## Recent events (canon — explicitly located here)")
        for ev in view["events"]:
            lines.append(f"- week {ev.get('week')} [{ev.get('actor')}]: "
                         f"{ev.get('text')}")
        if c["more"]:
            lines.append(f"_…and {c['more']} more (raise the limit)._")
        if not view["events"]:
            lines.append("_No located events in this window._")
        lines.append("")
    if view["knowledge"]:
        lines.append("## Facts (DM view — visibility labeled)")
        for f in view["knowledge"]:
            vis = f["visibility"]
            tag = "PRIVATE (dm_only)" if vis == "dm_only" else \
                  f"visible to: {vis}" if isinstance(vis, list) else \
                  "public in-world"
            if f.get("disclosed"):
                tag += "; DISCLOSED to party"
            lines.append(f"- {f['text']} _({tag})_")
        lines.append("")
    if view["pending"]:
        lines.append("## Pending (PROPOSED — NOT CANON)")
        for item in view["pending"]:
            lines.append(f"- {item['text'] or item.get('title')} "
                         f"(proposal {item['proposal_id']}, "
                         f"{item['proposal_kind']}, "
                         f"{item['proposal_status']})")
        lines.append("")
    if view["forecasts"]:
        lines.append("## Forecasts (HYPOTHETICAL — never canon)")
        for item in view["forecasts"]:
            lines.append(f"- week {item.get('week')} [{item.get('actor')}]: "
                         f"{item.get('text')} (forecast "
                         f"{item['forecast_id']})")
        lines.append("")
    if view["mentions"]:
        c = view["counts"]["mentions"]
        lines.append("## Mentions (wiki-links — context, not location)")
        for m in view["mentions"]:
            lines.append(f"- `{m['source']['file']}:{m['source']['line']}` "
                         f"— {m['excerpt']}")
        if c["more"]:
            lines.append(f"_…and {c['more']} more._")
        lines.append("")
    if view["findings"]:
        lines.append("## Findings (attention needed)")
        for f in view["findings"]:
            lines.append(f"- [{f['level']}] {f['rule']}: {f['message']}")
        lines.append("")
    if not any((view["maps"], view["factions"], view["entities"],
                view["projects"], view["events"], view["knowledge"],
                view["pending"], view["forecasts"], view["mentions"])):
        lines.append("_Nothing recorded here yet — empty sections list the "
                     "next action in the Places tab._")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- CLI


def _cli(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="studio/places.py",
        description="Place-centered campaign view (DM read model)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("campaign")
        sp.add_argument("place_id")

    show = sub.add_parser("show", help="the consolidated DM view")
    add_common(show)
    show.add_argument("--json", action="store_true")
    show.add_argument("--markdown", action="store_true")
    show.add_argument("--window", choices=("recent", "all"), default="recent")
    show.add_argument("--descendants", action="store_true")
    show.add_argument("--limit", type=int, default=DEFAULT_LIMIT)

    lst = sub.add_parser("list", help="every place record + suggested joins")
    lst.add_argument("campaign")
    lst.add_argument("--json", action="store_true")

    link_p = sub.add_parser("link", help="persist a place_id onto a lore page")
    add_common(link_p)
    link_p.add_argument("--page", required=True,
                        help="entity page stem to claim for this place")

    assoc = sub.add_parser("associate",
                           help="one typed association update")
    add_common(assoc)
    assoc.add_argument("--source", required=True,
                       choices=sorted(_ASSOCIATION_SOURCES))
    assoc.add_argument("--id", required=True, dest="source_id",
                       help="record id (for maps: cities/<slug> or "
                            "sites/<slug> when the bare slug is ambiguous)")
    assoc.add_argument("--relationship", required=True)
    assoc.add_argument("--remove", action="store_true")

    args = ap.parse_args(argv)
    with spine.campaign_lock(args.campaign):
        if args.cmd == "show":
            view = place_view(args.campaign, args.place_id,
                              window=args.window,
                              include_descendants=args.descendants,
                              event_limit=args.limit)
            if args.markdown or not args.json:
                print(render_markdown(args.campaign, view))
            else:
                print(json.dumps(view, indent=2, ensure_ascii=False))
        elif args.cmd == "list":
            doc = list_places(args.campaign)
            if args.json:
                print(json.dumps(doc, indent=2, ensure_ascii=False))
            else:
                for p in doc["places"]:
                    tag = "explicit" if p["explicit"] else \
                          ("conflict" if p["conflict"] else "provisional")
                    print(f"{p['id']:40} {p['name']:30} {tag}")
                for s in doc["suggested"]:
                    print(f"suggested join: {s['lore_file']} <-> sim "
                          f"'{s['sim_id']}' (run: link {args.campaign} "
                          f"{s['id']} --page <stem>)")
        elif args.cmd == "link":
            out = link(args.campaign, args.place_id, page=args.page)
            print(f"linked: {out.get('page')} -> {out.get('linked')} "
                  f"({out.get('state')})")
        elif args.cmd == "associate":
            out = associate(args.campaign, args.place_id,
                            source_kind=args.source,
                            source_id=args.source_id,
                            relationship=args.relationship,
                            add=not args.remove)
            print(f"associated: {out.get('source')} "
                  f"{out.get('relationship')} {out.get('place_id')} "
                  f"({out.get('state')})")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
