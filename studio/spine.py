"""Campaign spine IO — discovery and typed loaders over campaigns/<name>/.

One campaign is one directory of files (design §1); there is no database.
Everything here is read-mostly: loaders return plain dicts/lists parsed with
yaml.safe_load, missing optional files degrade to empty defaults, and an
unknown campaign raises CampaignNotFound (mapped to 404 by the server).

The campaigns root is a module-level constant so tests can point it at a
fixture tree (monkeypatch spine.CAMPAIGNS_ROOT); code reads it at call time
via campaigns_root(), never at import time.
"""

import re
import datetime
import os
import threading
import time
from pathlib import Path

import yaml

STUDIO_DIR = Path(__file__).resolve().parent
CAMPAIGNS_ROOT = STUDIO_DIR.parent / "campaigns"

# URL path segments must be plain names — no separators, no leading dots.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# New campaigns get a stricter bar: the name IS the directory name and the
# URL segment, and it should stay a lowercase slug like its siblings —
# no leading/trailing hyphens, no doubles, 1-40 chars.
_NEW_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")
# Windows reserves device names as directories in any drive path — mkdir
# would raise raw OSError, so they are refused at validation time.
_RESERVED_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
# lore/style files are constrained the same way (API.md, PUT style/{file})
_STYLE_RE = _NAME_RE
_CHRONICLE_RE = re.compile(r"^(\d{4})-(.+)\.md$")
_TITLE_RE = re.compile(r"^#\s*Week\s+(\d+)\s*[—–-]\s*(.+?)\s*$")


class CampaignError(Exception):
    """Raised for spine-level problems that map to 4xx responses."""


class CampaignNotFound(CampaignError):
    """Unknown campaign name — the server maps this to 404."""


class CampaignExists(CampaignError):
    """Campaign directory already exists — the server maps this to 409."""


class CampaignFileError(CampaignError):
    """A campaign file exists but does not parse (a hand-edit slipped).
    Carries the file name; the server maps this to a readable 422, never
    an opaque 500."""


# ------------------------------------------------------- per-campaign locks

# One mutation at a time per campaign: long jobs (week, world/city/site
# generation) hold the lock for their whole run, review approve/delete take
# it blocking for the length of their file rewrite. The registry is
# in-process (the server is the only writer; canon lives on disk, D5).
_CAMPAIGN_LOCKS: dict = {}
_CAMPAIGN_LOCKS_GUARD = threading.Lock()


def campaign_lock(name: str) -> threading.Lock:
    """The mutation lock for one campaign (created on first use).

    A threading.Lock IS a context manager, so `with spine.campaign_lock(n):`
    is the blocking form — used by review approve/delete (short file
    rewrites; they wait for a running job to finish). Long jobs acquire at
    submit time with `acquire(blocking=False)` and release in the job's
    finally (routers/common.submit_campaign_job), answering 409 while held.
    NEVER nest acquisitions of the same campaign's lock — a Lock is not
    reentrant, and a blocking re-acquire deadlocks."""
    with _CAMPAIGN_LOCKS_GUARD:
        return _CAMPAIGN_LOCKS.setdefault(name, threading.Lock())


def campaigns_root() -> Path:
    """The live campaigns root (reads the module global so tests can patch)."""
    return CAMPAIGNS_ROOT


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _safe_load(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except OSError:
        # a concurrent writer holds the file (Windows shares) or it vanished;
        # loaders degrade to "empty" rather than 500ing a read route
        return None
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        first = str(e).splitlines()[0] if str(e) else type(e).__name__
        raise CampaignFileError(
            f"{path.name} does not parse ({first}) — a hand-edit slipped? "
            f"Fix or delete the file, or restore it with git checkout -- {path}")


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 3) -> None:
    """os.replace, but tolerant of a Windows sharing violation: a reader
    holding the destination open without FILE_SHARE_DELETE makes the replace
    raise PermissionError for an instant. Three short tries covers it; a
    genuine failure still raises."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def write_text_atomic(path: Path, text: str) -> Path:
    """Write via tmp + os.replace so a crash or concurrent reader never sees
    a half-written spine file. Same volume, so the replace is atomic."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace_with_retry(tmp, path)
    return path


def valid_name(name: str) -> bool:
    return bool(name) and bool(_NAME_RE.match(name))


def campaign_dir(name: str) -> Path:
    if not valid_name(name):
        raise CampaignNotFound(f"invalid campaign name: {name!r}")
    return campaigns_root() / name


def require_campaign(name: str) -> Path:
    d = campaign_dir(name)
    if not (d / "campaign.yaml").is_file():
        raise CampaignNotFound(f"unknown campaign: {name}")
    return d


# ----------------------------------------------------------------- creation

_CREATE_ENGINE_POINTERS = {
    "mapping": "../dnd-mapping",
    "simulator": "../agentic-simulator",
    "verification": "../verification",
}


def create_campaign(name: str, title: str = "") -> dict:
    """A brand-new empty campaign: directory, campaign.yaml, section dirs.

    The name becomes the directory name and URL segment, so it is stricter
    than existing-campaign lookup: a lowercase slug, 1-40 chars. This is the
    one spine writer that starts from nothing — everything else fills in
    sections of an existing campaign, and the first feed is POST /world's
    seed (generation is fed or refused; there is nothing to refuse yet)."""
    if not _NEW_NAME_RE.match(name or ""):
        raise CampaignError(
            "campaign name must be a lowercase slug — a-z, 0-9, hyphens, "
            "1-40 chars, starting with a letter or digit (e.g. ember-coast)")
    if name in _RESERVED_DEVICE_NAMES:
        raise CampaignError(f"campaign name {name!r} is reserved on Windows")
    d = campaigns_root() / name
    if d.exists():
        raise CampaignExists(f"campaign already exists: {name}")
    title = (title or "").strip() or name
    try:
        (d / "world").mkdir(parents=True)
    except FileExistsError:
        # two concurrent creates: the exists() check above raced — map to the
        # same typed error instead of a raw OSError 500
        raise CampaignExists(f"campaign already exists: {name}")
    (d / "lore" / "style").mkdir(parents=True)
    (d / "simulation" / "chronicle").mkdir(parents=True)
    (d / "maps" / "cities").mkdir(parents=True)
    (d / "maps" / "sites").mkdir(parents=True)
    meta = {
        "name": name,
        "title": title,
        "created": datetime.date.today().isoformat(),
        "engines": dict(_CREATE_ENGINE_POINTERS),
    }
    write_text_atomic(d / "campaign.yaml",
                      yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    return {"name": name, "title": title, "created": meta["created"],
            "has": _has_sections(d)}


# ---------------------------------------------------------------- discovery


def list_campaigns() -> list:
    root = campaigns_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "campaign.yaml").is_file():
            continue
        # A folder whose name fails _NAME_RE can never be addressed by the
        # API (campaign_dir refuses it), so listing it would hand the UI an
        # option every route 404s on — skip it; the doctor names the skips.
        if not valid_name(d.name):
            continue
        try:
            meta = _safe_load(d / "campaign.yaml") or {}
        except CampaignFileError:
            # one corrupt campaign.yaml must not 4xx the whole listing —
            # degrade to folder-name identity; the file routes still 422
            # with the file name when that campaign is opened
            meta = {}
        # identity must stay addressable even if campaign.yaml was hand-edited
        listed_name = str(meta.get("name") or "")
        if not valid_name(listed_name):
            listed_name = d.name
        out.append({
            "name": listed_name,
            "title": meta.get("title") or d.name,
            "created": str(meta.get("created") or ""),
            "has": _has_sections(d),
        })
    return out


def _has_sections(d: Path) -> dict:
    return {
        "world": (d / "world" / "geography.yaml").is_file()
                 or world_source(d.name) is not None,
        "lore": (d / "lore" / "timeline.yaml").is_file(),
        "simulation": (d / "simulation" / "state.yaml").is_file(),
        "maps": bool(_map_slugs(d, "cities") or _map_slugs(d, "sites")),
    }


def skipped_campaign_folders() -> list:
    """Folders under campaigns/ that look like campaigns (campaign.yaml
    inside) but whose names cannot be addressed — the doctor reports them
    so a rename is the obvious fix instead of a silent absence."""
    root = campaigns_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "campaign.yaml").is_file()
        and not valid_name(d.name)
    )


# ------------------------------------------------------------------ loaders


def load_campaign_yaml(name: str) -> dict:
    return _safe_load(require_campaign(name) / "campaign.yaml") or {}


def load_geography(name: str) -> dict:
    geo = _safe_load(campaign_dir(name) / "world" / "geography.yaml")
    return geo or {}


def world_source(name: str):
    """The FMG dump under world/ (source.map / source.json / any source.*)."""
    world = campaign_dir(name) / "world"
    if not world.is_dir():
        return None
    for candidate in sorted(world.glob("source.*")):
        return candidate
    return None


def load_timeline(name: str) -> list:
    doc = _safe_load(campaign_dir(name) / "lore" / "timeline.yaml")
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        return doc.get("entries") or []
    return []


def load_entities(name: str) -> list:
    """The campaign's entities as a canonical list of dicts
    (id?/kind/name/aliases/note, plus `file` for page-backed entities).

    Entity pages are canonical (obsidian-integration.md §1): one markdown
    file per entity in lore/entities/. A legacy lore/entities.yaml that has
    not been migrated yet still reads (boot migrates it away), so a campaign
    zipped off an old install degrades gracefully instead of vanishing."""
    pages = entity_pages(name)
    if pages:
        return [{k: p[k] for k in p if k != "body"} for p in pages]
    return _legacy_entities(name)


# ------------------------------------------------------- entity pages (vault)

# Frontmatter fence: --- at the very top, closing --- on its own line.
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
# A page filename is a display name (spaces, dots fine) with the
# filesystem's trouble characters already excluded; .md rides in the caller.
_PAGE_STEM_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,80}$")


def valid_page_name(page: str) -> bool:
    """A lore/entities page stem as it may appear in a URL path segment —
    a display name, but no separators, control chars, leading dot, or
    traversal."""
    return bool(page) and ".." not in page and bool(_PAGE_STEM_RE.match(page))


def entity_page_filename(name: str) -> str:
    """The page filename for an entity name — display names, per the vault
    design's recommendation (typing [[House Fer autocompletes in Obsidian;
    nothing here becomes a shell path). Filesystem-hostile characters fold
    to spaces; a hostile stem (reserved device name, trailing dot/space)
    raises CampaignError — mint a different name instead."""
    stem = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", name or "")
    stem = re.sub(r"\s+", " ", stem).strip()
    if (not _PAGE_STEM_RE.match(stem) or stem.startswith(".")
            or windows_hostile_stem(stem)):
        raise CampaignError(f"cannot make an entity page filename from {name!r}")
    return stem + ".md"


def parse_entity_page(path: Path) -> dict:
    """One lore/entities/*.md page -> the entity dict (id?/kind/name/
    aliases/note/body/file).

    Frontmatter carries today's exact fields; the body's first paragraph is
    the note (what entities.yaml used to carry), the rest is free lore —
    sections, wiki-links. A page with no frontmatter degrades to its
    filename stem (a hand-created file is a page, not an error); frontmatter
    that does not parse is a hand-edit slip -> CampaignFileError (readable
    422, never an opaque 500)."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise CampaignFileError(
            f"{path.name} is not UTF-8 — fix the encoding, or restore it "
            f"with git checkout -- lore/entities/{path.name}")
    m = _FRONTMATTER_RE.match(text)
    body = text[m.end():].lstrip("\n") if m else text
    meta = {}
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            first = str(e).splitlines()[0] if str(e) else type(e).__name__
            raise CampaignFileError(
                f"{path.name} does not parse ({first}) — a hand-edit slipped? "
                f"Fix the frontmatter, or restore it with "
                f"git checkout -- lore/entities/{path.name}")
        if not isinstance(meta, dict):
            raise CampaignFileError(
                f"{path.name}: frontmatter must be a mapping of fields")
    aliases = meta.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    return {
        "id": str(meta.get("id") or "").strip(),
        "kind": str(meta.get("kind") or "").strip(),
        "name": str(meta.get("name") or "").strip() or path.stem,
        "aliases": [str(a) for a in aliases],
        "note": _first_paragraph(body),
        "body": body.strip(),
        "file": f"lore/entities/{path.name}",
    }


def _first_paragraph(body: str) -> str:
    """The note: the first paragraph, skipping leading headings."""
    lines, para, started = body.splitlines(), [], False
    for line in lines:
        t = line.strip()
        if not t:
            if started:
                break
            continue
        if t.startswith("#"):
            if started:
                break
            continue  # a leading heading is a title, not the note
        started = True
        para.append(t)
    return " ".join(para).strip()


def entity_pages(name: str) -> list:
    """Every entity page, sorted by filename. Degrades to [] when the
    campaign has none; a page that does not parse raises CampaignFileError
    (the caller maps it to a readable 422)."""
    d = campaign_dir(name) / "lore" / "entities"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        try:
            out.append(parse_entity_page(p))
        except FileNotFoundError:
            continue  # unlinked mid-glob (migration/race) — never a 500
    return out


def write_entity_page(name: str, page_name: str, frontmatter: dict,
                      body: str) -> Path:
    """Create (never overwrite) one entity page. Create-only is the point:
    a re-run migration or a re-stub must never clobber hand edits."""
    require_campaign(name)
    filename = entity_page_filename(page_name)
    path = campaign_dir(name) / "lore" / "entities" / filename
    if path.exists():
        raise CampaignExists(f"entity page already exists: {filename}")
    meta = {k: v for k, v in frontmatter.items()
            if v not in (None, "", [], {})}
    text = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True,
                          default_flow_style=False).strip()
    page = f"---\n{text}\n---\n\n{body.strip()}\n" if text else f"{body.strip()}\n"
    return write_text_atomic(path, page)


def _legacy_entities(name: str) -> list:
    """Normalize every entities.yaml shape seen in the wild into the
    canonical list: {entities: [...]}, a bare list, or groups keyed by kind
    (the fixture's people/factions/places — the key IS the kind; a singular
    `alias:` field rides along as a one-item aliases list)."""
    doc = _safe_load(campaign_dir(name) / "lore" / "entities.yaml")
    if isinstance(doc, dict):
        entities = doc.get("entities")
        if isinstance(entities, list):
            return [_legacy_entity(e) for e in entities
                    if isinstance(e, dict)]
        out = []  # grouped shape: {<kind>: [{name, note, alias?...}]}
        for kind, items in doc.items():
            if isinstance(items, list):
                out.extend(_legacy_entity(e, kind=str(kind))
                           for e in items if isinstance(e, dict))
        return out
    if isinstance(doc, list):
        return [_legacy_entity(e) for e in doc if isinstance(e, dict)]
    return []


def _legacy_entity(e: dict, kind: str = "") -> dict:
    aliases = e.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if e.get("alias") and e["alias"] not in aliases:
        aliases = [str(e["alias"])] + list(aliases)
    return {
        "id": str(e.get("id") or "").strip(),
        "kind": str(e.get("kind") or kind or "").strip(),
        "name": str(e.get("name") or "").strip(),
        "aliases": [str(a) for a in aliases],
        "note": str(e.get("note") or "").strip(),
    }


def migrate_entities_yaml_to_pages(name: str):
    """One-time entities.yaml -> lore/entities/*.md (vault §1, stage 1).

    Migrate-and-delete, on the same pass: pages are written for entities
    whose page does not exist yet (a partial earlier migration never
    clobbers hand edits — filename collisions get a ' (2)' suffix), then
    entities.yaml is deleted. Returns the number of pages written, 0 when
    the yaml existed but was empty, None when there was nothing to migrate.
    The caller holds the campaign lock and takes the canon snapshot."""
    require_campaign(name)
    yaml_path = campaign_dir(name) / "lore" / "entities.yaml"
    if not yaml_path.is_file():
        return None
    entities = _legacy_entities(name)
    pages_dir = campaign_dir(name) / "lore" / "entities"
    # casefold: Windows filenames are case-insensitive, so "The Reliquary"
    # and "the Reliquary" are the same file — the second needs the suffix
    used = {p.name.casefold()
            for p in pages_dir.glob("*.md")} if pages_dir.is_dir() else set()
    written = 0
    for e in entities:
        if not e["name"]:
            continue
        try:
            filename = entity_page_filename(e["name"])
        except CampaignError:
            filename = slugify(e["name"]) + ".md"  # hostile stem: slug it
        while filename.casefold() in used:
            filename = f"{filename[:-3]} (2).md"
        used.add(filename.casefold())
        meta = {"id": e["id"], "kind": e["kind"], "name": e["name"],
                "aliases": e["aliases"]}
        write_entity_page(name, filename[:-3], meta, e["note"])
        written += 1
    yaml_path.unlink()
    return written


def style_files(name: str) -> list:
    d = campaign_dir(name) / "lore" / "style"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


def read_style(name: str, filename: str) -> str:
    path = _style_path(name, filename)
    return path.read_text(encoding="utf-8")


def write_style(name: str, filename: str, text: str) -> Path:
    path = _style_path(name, filename, must_exist=True)
    return write_text_atomic(path, text)


def create_style(name: str, filename: str, text: str) -> Path:
    """Create (never overwrite) a lore/style file — the staging path for the
    seed lists the fed-or-refused guardrail demands (name helper)."""
    path = _style_path(name, filename)
    if path.exists():
        raise CampaignExists(f"style file already exists: {filename}")
    return write_text_atomic(path, text)


def windows_hostile_stem(stem: str) -> str | None:
    """Why a would-be directory/file stem cannot exist on Windows, or None
    when it is fine: reserved device names, trailing dot/space (Windows
    silently strips them, so the path written is not the path asked for).
    Callers turn this into a readable 422 instead of a raw OSError."""
    if stem.lower() in _RESERVED_DEVICE_NAMES:
        return f"'{stem}' is a reserved Windows device name"
    if stem != stem.rstrip(" ."):
        return f"'{stem}' ends with a dot or space — Windows strips it"
    return None


def _style_path(name: str, filename: str, must_exist: bool = False) -> Path:
    if not _STYLE_RE.match(filename or ""):
        raise CampaignError(f"invalid style filename: {filename!r}")
    hostile = windows_hostile_stem(Path(filename).stem)
    if hostile:
        raise CampaignError(f"invalid style filename: {hostile}")
    path = campaign_dir(name) / "lore" / "style" / filename
    if must_exist and not path.is_file():
        raise CampaignNotFound(f"no such style file: {filename}")
    return path


def load_state(name: str) -> dict:
    doc = _safe_load(campaign_dir(name) / "simulation" / "state.yaml")
    return doc if isinstance(doc, dict) else {}


def write_state(name: str, doc: dict) -> Path:
    """Write simulation/state.yaml from a dict (the create-a-simulation
    flow). The caller validates content; this is the typed writer."""
    require_campaign(name)
    path = campaign_dir(name) / "simulation" / "state.yaml"
    return write_text_atomic(
        path, yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))


def chronicles(name: str) -> list:
    """[{"week","title","file"}] parsed from simulation/chronicle/*.md."""
    d = campaign_dir(name) / "simulation" / "chronicle"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        m = _CHRONICLE_RE.match(p.name)
        if not m:
            continue
        week = int(m.group(1))
        title = m.group(2).replace("-", " ")
        # one cp1252-resaved file must degrade, not 500 the Simulation tab
        try:
            first = p.read_text(encoding="utf-8").splitlines()[:1]
        except UnicodeDecodeError:
            out.append({"week": week, "title": "(unreadable — not UTF-8)",
                        "file": f"simulation/chronicle/{p.name}"})
            continue
        if first:
            t = _TITLE_RE.match(first[0])
            if t:
                title = t.group(2)
        out.append({"week": week, "title": title,
                    "file": f"simulation/chronicle/{p.name}"})
    return sorted(out, key=lambda c: c["week"])


def read_chronicle(name: str, week: int):
    for c in chronicles(name):
        if c["week"] == week:
            path = campaign_dir(name) / c["file"]
            c = dict(c)
            try:
                c["markdown"] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # same contract as chronicles(): latin-1 never fails, and
                # replacement chars beat an opaque 500 on a resaved file
                c["markdown"] = path.read_text(encoding="latin-1")
            return c
    return None


def count_pending(name: str) -> int:
    """Pending review files: lore/pending-*.yaml + simulation/pending-week-*.yaml."""
    n = 0
    for pattern in ("lore/pending-*.yaml", "simulation/pending-week-*.yaml"):
        n += len(list(campaign_dir(name).glob(pattern)))
    return n


def _map_slugs(d: Path, kind: str) -> list:
    base = d / "maps" / kind
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def map_slugs(name: str, kind: str) -> list:
    return _map_slugs(campaign_dir(name), kind)


# ------------------------------------------------------------------ summary


def summary(name: str) -> dict:
    """GET /api/campaign/{name} — the one-call overview (API.md shape)."""
    d = require_campaign(name)
    meta = load_campaign_yaml(name)
    geo = load_geography(name)

    nations = geo.get("nations") or []
    burgs = [{
        "name": b.get("name"),
        "nation": b.get("nation"),
        "population": b.get("population_thousands", b.get("population")),
        "x": b.get("x"),
        "y": b.get("y"),
        "port": bool(b.get("port")),
    } for b in (geo.get("major_settlements") or [])]

    state = load_state(name)
    sim_meta = state.get("meta") or {}
    clock = sim_meta.get("clock") or {}
    factions = state.get("factions") or []

    return {
        "name": meta.get("name") or name,
        "title": meta.get("title") or name,
        "world": {
            "nations": len(nations),
            "burgs": burgs,
        },
        "lore": {
            "timeline_entries": len(load_timeline(name)),
            "entities": len(load_entities(name)),
            "style_files": style_files(name),
        },
        "simulation": {
            "clock": {"week": clock.get("week"), "season": clock.get("season")},
            "factions": [{
                "id": f.get("id"),
                "name": f.get("name"),
                "kind": f.get("kind"),
                "resources": f.get("resources") or {},
            } for f in factions],
            "rumors": len(state.get("rumors") or []),
            "pending": count_pending(name),
        },
        "maps": {
            "cities": _map_slugs(d, "cities"),
            "sites": _map_slugs(d, "sites"),
        },
    }
