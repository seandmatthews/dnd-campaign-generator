"""Maps tab API: reads, city generation, world generation, world-map
rendering, battlemap site generation.

World generation (M1): POST /world wraps the documented headless-FMG
incantation exactly (docs/map-pipeline.md Tier 1) — a vitest run from the
FMG repo root with FMG_SEED/FMG_OUT env — then copies the raw dump into
the campaign (D4) and runs `src/import_fmg.py` per its real CLI
(cwd=dnd-mapping; it writes worlds/candidates — its documented behavior),
copying the produced candidate yaml in as world/geography.yaml. FMG
headless exports no SVG, so the hero is rendered from the dump itself by
studio/worldmap.py (read-only; see that module for why regenerating is not
an option).

City generation: `node scripts/gen-city.mjs --world <campaign
world/source.*> --burg "<name>" --out <tmp>` with cwd=dnd-mapping (D3:
list-form args — the parent path contains `&`), then copy outputs into the
campaign's maps/cities/<slug>/.

Site generation: the full documented battlemap chain — gen-<kind> → render
→ render-scene-png.mjs (sharp) → wrap — run in the engine home
(worlds/sites/<name> is battlemap.py's documented output location), then
the artifact set is copied into maps/sites/<slug>/ (D4).

Generation jobs are campaign MUTATIONS: each takes the per-campaign lock at
submit time (routers/common.submit_campaign_job — 409 while another
mutating job runs) and commits a canon snapshot on success
("campaign/<name>: generate world / map city / map site ...").
"""

import re
import shutil
import tempfile
import threading
import zlib
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import canon
import engines
import links
import spine
import worldmap
from routers.common import require_campaign, submit_campaign_job

router = APIRouter()

# dnd-mapping's engine home is SHARED across campaigns (worlds/sites/<name>,
# worlds/candidates), unlike the simulator it has no ENGINE_LOCK — the
# per-campaign locks cannot see each other. This process-wide lock serializes
# the staging sections: two campaigns generating same-named sites would
# otherwise interleave artifacts (and wrap's *.uvtt glob could pick the other
# run's file), and a concurrent import would blur the candidates diff.
MAPPING_LOCK = threading.Lock()

_SEED_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SITE_KINDS = ("dungeon", "cave", "camp")
# characters a scene directory (engine home, Windows) cannot carry
_BAD_SITE_NAME = re.compile(r'[<>:"/\\|?*]')


class CityBody(BaseModel):
    burg: str
    place: str | None = None


class SiteBody(BaseModel):
    kind: str
    name: str
    seed: str | None = None
    place: str | None = None


class WorldBody(BaseModel):
    seed: str
    force: bool = False


def _place_link(raw: str | None) -> str | None:
    """The attach-to-place form value -> a wiki-link target, or None when
    empty. Stripped; anything else is the DM's display name verbatim."""
    place = (raw or "").strip()
    return place or None


def _stub_place_page(name: str, place: str, log) -> None:
    """Create the place's entity page as a stub when nothing resolves it —
    mechanical metadata (the link target must exist in Obsidian), not
    generated prose. Create-only; whatever exists is never clobbered."""
    try:
        known = {links.normalize_key(p["name"]) for p in spine.entity_pages(name)}
    except spine.CampaignFileError:
        return
    if links.normalize_key(place) in known:
        return
    try:
        spine.write_entity_page(
            name, place,
            {"kind": "place", "name": place},
            "(Stub page — created when this place was mapped. Add lore "
            "here, or in Obsidian.)")
        log(f"stubbed place page: lore/entities/{place}.md")
    except (spine.CampaignError, OSError) as e:
        log(f"place stub skipped: {e}")


def _companion_note(dest: Path, slug: str, front: dict, body: str) -> Path:
    """The map's companion note (vault §4): generated with the artifact —
    embed by vault-unique basename, frontmatter links, params summary.
    Regenerated on re-map, like params.yaml; hand edits do not survive."""
    meta = yaml.safe_dump(
        {k: v for k, v in front.items() if v not in (None, "", [], {})},
        sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
    text = (f"---\n{meta}\n---\n\n{body.strip()}\n" if meta
            else f"{body.strip()}\n")
    path = dest / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def ensure_site_slugs(name: str):
    """One-time mechanical rename (vault §4, load-bearing): site directories
    used to carry identical basenames (scene.png, scene.svg, scene.json) —
    Obsidian resolves embeds by filename, so every artifact becomes
    <slug>.<ext> (cities already work this way). Idempotent, non-blocking
    (skipped while a job holds the lock), never raises; snapshots a real
    rename. params.yaml keeps its name — nobody embeds it."""
    try:
        d = spine.require_campaign(name)
    except spine.CampaignError:
        return None
    sites = d / "maps" / "sites"
    if not sites.is_dir():
        return None
    renames = []
    for site in sorted(sites.iterdir()):
        if not site.is_dir():
            continue
        for ext in ("png", "svg", "json"):
            old, new = site / f"scene.{ext}", site / f"{site.name}.{ext}"
            if old.is_file() and not new.exists():
                renames.append((old, new))
    if not renames:
        return None
    lock = spine.campaign_lock(name)
    if not lock.acquire(blocking=False):
        return None
    done = 0
    try:
        for old, new in renames:
            try:
                old.rename(new)
                done += 1
            except OSError:
                pass  # vanished mid-list (concurrent rename) — never a 500
    finally:
        lock.release()
    if done:
        canon.snapshot(name, f"campaign/{name}: slug site artifact basenames "
                             f"({done} file(s) renamed for vault-unique embeds)")
    return done


@router.get("/api/campaign/{name}/maps")
def get_maps(name: str):
    d = require_campaign(name)
    ensure_site_slugs(name)  # lazy legacy scene.* -> <slug>.*, no-op when done
    return {
        "cities": [_entry(d, "cities", slug) for slug in spine.map_slugs(name, "cities")],
        "sites": [_entry(d, "sites", slug) for slug in spine.map_slugs(name, "sites")],
    }


def _entry(d: Path, kind: str, slug: str) -> dict:
    base = d / "maps" / kind / slug
    svg = _first(base, "*.svg")
    geojson = _first(base, "*.geojson")
    params = _first(base, "params.yaml")
    label = slug
    if params is not None:
        try:
            doc = yaml.safe_load(params.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            doc = {}  # a corrupt hand-edited params.yaml must not 500 the list
        if isinstance(doc, dict):
            label = doc.get("name") or doc.get("label") or slug
    return {
        "slug": slug,
        "label": label,
        "svg": _file_url(svg),
        "params": _file_url(params),
        "geojson": _file_url(geojson),
    }


def _first(base: Path, pattern: str):
    if not base.is_dir():
        return None
    for p in sorted(base.glob(pattern)):
        return p
    return None


def _file_url(path):
    if path is None:
        return None
    return f"/campaign-file/{path.relative_to(spine.campaigns_root()).as_posix()}"


@router.post("/api/campaign/{name}/maps/cities", status_code=202)
def post_city(name: str, body: CityBody):
    d = require_campaign(name)
    burg = (body.burg or "").strip()
    if not burg:
        raise HTTPException(status_code=422, detail="burg must be a non-empty name")
    if not spine.slugify(burg):
        raise HTTPException(
            status_code=422,
            detail=f"burg {burg!r} has no usable slug — name it with letters or digits")
    source = spine.world_source(name)
    if source is None:
        raise HTTPException(
            status_code=422,
            detail="campaign has no world/source.* — generate a world first "
                   "(Maps tab); a city map is generated from the world dump "
                   "or not at all (fed-or-refused)")

    place = _place_link(body.place) or burg  # cities default to their burg
    try:
        spine.entity_page_filename(place)  # nameable before the job runs
    except spine.CampaignError as e:
        raise HTTPException(status_code=422, detail=str(e))

    def job(log):
        out = Path(tempfile.mkdtemp(prefix="studio-city-"))
        try:
            log(f"generating {burg} from {source.name} via gen-city.mjs")
            rc, lines = engines.run_node(
                engines.DND_MAPPING, "scripts/gen-city.mjs",
                "--world", str(source), "--burg", burg, "--out", str(out),
                on_line=log, timeout=600)
            if rc != 0:
                raise RuntimeError("gen-city.mjs failed (rc=%s): %s"
                                   % (rc, " | ".join(lines[-8:])))
            outputs = [p for p in sorted(out.iterdir()) if p.is_file()]
            if not outputs:
                raise RuntimeError("gen-city.mjs produced no output files")
            svg = next((p for p in outputs if p.suffix == ".svg"), None)
            if svg is None:
                raise RuntimeError("no .svg in gen-city output")
            # the slug is the GENERATED FILE's stem, never the raw form
            # value: `--burg 12` writes natabashe.svg, and a directory or
            # ![[embed]] named after "12" would reference nothing
            slug = spine.slugify(svg.stem)
            dest = d / "maps" / "cities" / slug
            for p in outputs:
                engines.copy_file(p, dest / p.name)
                log(f"copied {p.name} -> maps/cities/{slug}/")
            _stub_place_page(name, place, log)
            note = _companion_note(
                dest, slug,
                {"kind": "city map", "place": f"[[{place}]]", "burg": burg},
                f"![[{slug}.svg]]\n\nCity map of {place}, generated from the "
                f"world dump ({source.name}) via settlemaker.")
            log(f"companion note: maps/cities/{slug}/{note.name}")
            canon.snapshot(name, f"campaign/{name}: map city {slug}")
            return {"slug": slug,
                    "svg": f"/campaign-file/{name}/maps/cities/{slug}/{svg.name}"}
        finally:
            shutil.rmtree(out, ignore_errors=True)

    return submit_campaign_job("map-city", name, job)


# --------------------------------------------------------------- world gen


@router.post("/api/campaign/{name}/world", status_code=202)
def post_world(name: str, body: WorldBody):
    """seed → headless FMG → import → copy into the workspace (M1)."""
    d = require_campaign(name)
    seed = (body.seed or "").strip()
    if not _SEED_RE.match(seed):
        raise HTTPException(
            status_code=422,
            detail="seed must be 1-64 characters of [A-Za-z0-9_-] "
                   "(it becomes FMG_SEED and a filename)")
    if (d / "world" / "geography.yaml").is_file() and not body.force:
        raise HTTPException(
            status_code=409,
            detail="world/geography.yaml already exists — pass force:true to "
                   "regenerate (this replaces world/source.* and "
                   "world/geography.yaml)")

    def job(log):
        tmp = Path(tempfile.mkdtemp(prefix="studio-world-"))
        try:
            fmg = engines.fmg_dir()
            log(f"headless FMG seed {seed} (FMG_SEED={seed} vitest run)")
            rc, lines = engines.run_node(
                fmg, "node_modules/vitest/vitest.mjs", "run",
                "src/generators/headless-export.test.ts",
                timeout=900, on_line=log,
                env={"FMG_SEED": seed, "FMG_OUT": str(tmp)})
            if rc != 0:
                raise RuntimeError("headless FMG failed (rc=%s): %s"
                                   % (rc, " | ".join(lines[-8:])))
            dump = tmp / f"headless-{seed}.json"
            if not dump.is_file():
                raise RuntimeError(
                    f"FMG ran but wrote no {dump.name} — check FMG_OUT handling")

            world = d / "world"
            world.mkdir(parents=True, exist_ok=True)
            source = world / "source.json"
            engines.copy_file(dump, source)
            log("genesis record copied -> world/source.json")

            candidates = engines.DND_MAPPING / "worlds" / "candidates"
            with MAPPING_LOCK:  # the candidates dir is the shared engine home
                before = ({p.name for p in candidates.glob("*.yaml")}
                          if candidates.is_dir() else set())
                log("importing geography via src/import_fmg.py")
                rc, lines = engines.run_py(
                    engines.DND_MAPPING, "src/import_fmg.py", str(dump),
                    timeout=300, on_line=log)
                if rc != 0:
                    raise RuntimeError("import_fmg.py failed (rc=%s): %s"
                                       % (rc, " | ".join(lines[-8:])))
                produced = None
                for ln in lines:  # the summary line: "<dump> -> <out>.yaml: ..."
                    m = re.search(r"-> (\S+\.yaml):", ln)
                    if m:
                        produced = m.group(1)
                        break
                if produced is None:  # fall back to a candidates-dir diff
                    new = ({p.name for p in candidates.glob("*.yaml")} - before) \
                        if candidates.is_dir() else set()
                    produced = sorted(new)[0] if new else None
                cand = candidates / produced if produced else None
                if not cand or not cand.is_file():
                    raise RuntimeError(
                        "import_fmg.py wrote no candidate yaml — cannot stage "
                        "world/geography.yaml")
                engines.copy_file(cand, world / "geography.yaml")
            log(f"candidate {cand.name} copied -> world/geography.yaml")

            geo = yaml.safe_load(
                (world / "geography.yaml").read_text(encoding="utf-8")) or {}
            summary = geo.get("summary") or {}

            # hero: FMG headless exports no svg; render from the dump
            # (studio/worldmap.py — pure read, no regeneration)
            map_url = None
            try:
                info = worldmap.render_world_map(source, world / "map.svg")
                map_url = f"/campaign-file/{name}/world/map.svg"
                log(f"rendered world/map.svg ({info.get('mode')})")
            except Exception as e:  # best effort — the world exists regardless
                log(f"world/map.svg skipped: {e}")

            canon.snapshot(name, f"campaign/{name}: generate world (seed {seed})")
            return {"nations": int(summary.get("nations") or 0),
                    "settlements": int(summary.get("settlements") or 0),
                    "map": map_url}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return submit_campaign_job("world-gen", name, job)


@router.post("/api/campaign/{name}/maps/world-map", status_code=202)
def post_world_map(name: str):
    """Render world/map.svg from the EXISTING world/source.* — read-only;
    geography is never regenerated (studio/worldmap.py docstring explains
    why re-running the generator is not an option)."""
    d = require_campaign(name)
    source = spine.world_source(name)
    if source is None:
        raise HTTPException(
            status_code=422,
            detail="campaign has no world/source.* — generate a world first "
                   "(Maps tab); the world map renders from the genesis "
                   "record or not at all (fed-or-refused)")

    def job(log):
        out = d / "world" / "map.svg"
        log(f"rendering world/map.svg from {source.name} "
            "(read-only: geography is not regenerated)")
        info = worldmap.render_world_map(source, out)
        log(f"mode: {info['mode']}")
        return {"svg": f"/campaign-file/{name}/world/map.svg",
                "mode": info["mode"]}

    # world-map writes world/map.svg, which a force world-gen also replaces —
    # it takes the same per-campaign mutation lock as every other writer
    return submit_campaign_job("world-map", name, job)


# ---------------------------------------------------------- battlemap sites


@router.post("/api/campaign/{name}/maps/sites", status_code=202)
def post_site(name: str, body: SiteBody):
    """battlemap.py gen-<kind> → render → rasterize (sharp) → wrap UVTT."""
    d = require_campaign(name)
    kind = (body.kind or "").strip().lower()
    if kind not in _SITE_KINDS:
        raise HTTPException(
            status_code=422,
            detail="kind must be one of dungeon|cave|camp")
    site_name = (body.name or "").strip()
    if not site_name:
        raise HTTPException(status_code=422,
                            detail="name must be a non-empty site name")
    if _BAD_SITE_NAME.search(site_name):
        raise HTTPException(
            status_code=422,
            detail='name must not contain any of <>:"/\\|?*')
    hostile = spine.windows_hostile_stem(site_name)
    if hostile:
        raise HTTPException(status_code=422, detail=hostile)
    if not spine.slugify(site_name):
        raise HTTPException(
            status_code=422,
            detail=f"site {site_name!r} has no usable slug — name it with letters or digits")
    seed = (body.seed or "").strip() or str(
        zlib.crc32(spine.slugify(site_name).encode("utf-8")) % 10_000_000)
    slug = spine.slugify(site_name)
    place = _place_link(body.place)
    if place:
        try:
            spine.entity_page_filename(place)  # nameable before the job runs
        except spine.CampaignError as e:
            raise HTTPException(status_code=422, detail=str(e))

    def job(log):
        rel = f"worlds/sites/{site_name}"
        root = engines.DND_MAPPING
        # worlds/sites/<name> is the shared engine home: two campaigns here
        # at once interleave artifacts (wrap globs *.uvtt), so the whole
        # staging chain holds MAPPING_LOCK (minutes of subprocess — fine,
        # site generation is rare and serial for a single DM anyway)
        with MAPPING_LOCK:

            def stage(label, call, what):
                log(label)
                rc, lines = call()
                if rc != 0:
                    raise RuntimeError(f"{what} failed (rc={rc}): "
                                       + " | ".join(lines[-8:]))
                return lines

            stage(f"battlemap.py gen-{kind} --seed {seed} --name \"{site_name}\"",
                  lambda: engines.run_py(root, "src/battlemap.py", f"gen-{kind}",
                                         "--seed", seed, "--name", site_name,
                                         timeout=300, on_line=log),
                  f"battlemap.py gen-{kind}")
            stage(f"battlemap.py render --dir {rel}",
                  lambda: engines.run_py(root, "src/battlemap.py", "render",
                                         "--dir", rel, timeout=300, on_line=log),
                  "battlemap.py render")
            log(f"render-scene-png.mjs {rel} (sharp)")
            rc, png_lines = engines.run_node(
                root, "scripts/render-scene-png.mjs", rel, timeout=300, on_line=log)
            if rc != 0:
                joined_png = " ".join(png_lines)
                if "sharp" in joined_png or "Cannot find module" in joined_png:
                    raise RuntimeError(
                        "scene rasterization failed — sharp is unavailable "
                        "(the tool tree is broken or half-installed). "
                        "Documented repair: run `npm install "
                        "--no-audit --no-fund` in dnd-mapping/tools/"
                        "settlemaker (or python studio/doctor.py --fix), "
                        "then try again")
                raise RuntimeError(f"render-scene-png.mjs failed (rc={rc}): "
                                   + " | ".join(png_lines[-8:]))
            stage(f"battlemap.py wrap --dir {rel}",
                  lambda: engines.run_py(root, "src/battlemap.py", "wrap",
                                         "--dir", rel, timeout=300, on_line=log),
                  "battlemap.py wrap")

            engine_dir = root / "worlds" / "sites" / site_name
            scene = engine_dir / "scene.json"
            svg = engine_dir / "scene.svg"
            png = engine_dir / "scene.png"
            uvtt = next(iter(sorted(engine_dir.glob("*.uvtt"))), None)
            missing = [p.name for p in (scene, svg, png) if not p.is_file()]
            if uvtt is None or not uvtt.is_file():
                missing.append("<slug>.uvtt")
            if missing:
                raise RuntimeError(
                    "scene pipeline did not produce: " + ", ".join(missing))

            dest = d / "maps" / "sites" / slug
            # vault-unique basenames (§4): <slug>.png/.svg/.json — embeds
            # resolve by filename, and every site used to collide on scene.*
            # (the UVTT already ships as <slug>.uvtt)
            for p in (scene, svg, png):
                engines.copy_file(p, dest / f"{slug}{p.suffix}")
                log(f"copied {p.name} -> maps/sites/{slug}/")
            engines.copy_file(uvtt, dest / uvtt.name)
            log(f"copied {uvtt.name} -> maps/sites/{slug}/")
        # provenance beside the artifact set, as cities do
        (dest / "params.yaml").write_text(yaml.safe_dump(
            {"name": site_name, "kind": kind, "seed": seed,
             "generator": f"battlemap.py gen-{kind} + render + rasterize + wrap",
             "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
            sort_keys=False, allow_unicode=True), encoding="utf-8")

        note = _companion_note(
            dest, slug,
            {"kind": f"{kind} battlemap", "place": f"[[{place}]]" if place else None,
             "name": site_name, "seed": seed},
            f"![[{slug}.png]]\n\n{site_name} — a {kind} scene (seed {seed}). "
            f"The {uvtt.name} beside this note drops straight into Foundry "
            "(walls, doors and lights live).")
        log(f"companion note: maps/sites/{slug}/{note.name}")

        base = f"/campaign-file/{name}/maps/sites/{slug}/"
        canon.snapshot(name, f"campaign/{name}: map site {slug}")
        return {"slug": slug, "label": site_name,
                "svg": base + f"{slug}.svg", "png": base + f"{slug}.png",
                "uvtt": base + uvtt.name, "scene": base + f"{slug}.json"}

    return submit_campaign_job("map-site", name, job)
