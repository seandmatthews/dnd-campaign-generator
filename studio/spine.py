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
import re
import threading
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
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


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
    d = campaigns_root() / name
    if d.exists():
        raise CampaignExists(f"campaign already exists: {name}")
    title = (title or "").strip() or name
    (d / "world").mkdir(parents=True)
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
    with open(d / "campaign.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, sort_keys=False, allow_unicode=True)
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
        meta = _safe_load(d / "campaign.yaml") or {}
        out.append({
            "name": meta.get("name") or d.name,
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


def load_entities(name: str) -> dict:
    doc = _safe_load(campaign_dir(name) / "lore" / "entities.yaml")
    return doc if isinstance(doc, dict) else {}


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
    path.write_text(text, encoding="utf-8")
    return path


def write_entities(name: str, text: str) -> Path:
    require_campaign(name)
    path = campaign_dir(name) / "lore" / "entities.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _style_path(name: str, filename: str, must_exist: bool = False) -> Path:
    if not _STYLE_RE.match(filename or ""):
        raise CampaignError(f"invalid style filename: {filename!r}")
    path = campaign_dir(name) / "lore" / "style" / filename
    if must_exist and not path.is_file():
        raise CampaignNotFound(f"no such style file: {filename}")
    return path


def load_state(name: str) -> dict:
    doc = _safe_load(campaign_dir(name) / "simulation" / "state.yaml")
    return doc if isinstance(doc, dict) else {}


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
        first = p.read_text(encoding="utf-8").splitlines()[:1]
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
            c["markdown"] = path.read_text(encoding="utf-8")
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
