"""Import an Azgaar FMG export into a geography.yaml for the atlas.

FMG sources accepted:
  - .map save file: pipe-separated header line, then a JSON blob
  - "full" JSON export: {info, settings, mapCoordinates, pack, grid, nameBases}

Maps are derived artifacts: the raw FMG file stays untouched in
worlds/raw/ and this script derives a terse geography.yaml beside the
canonical state. Re-run any time; never hand-edit the output. meta carries
no timestamps at all (seed + fmg_version are the provenance), so
re-importing an unchanged file is byte-stable — a wall-clock or mtime stamp
permanently dirtied tracked derivatives on every regeneration.

Provenance caveat: every tracked candidate's raw dump is expected in
worlds/raw/ so `--all` can regenerate it — EXCEPT the seed-913 world
(world-913-913.yaml), whose raw export lives in the campaigns repo as
campaigns/sable-ford/world/source.json (the campaign imported it before
the raw/ convention was settled); the seed-7942/e2e9 candidates (raw
dumps never retained); and the rejected atlas candidates 01/02/04/05
(Pitbar, Seia, Lombreux, Bouieland — .map saves pruned 2026-10-04 per
the map-pipeline.md rule, seeds recorded in worlds/candidates/index.md).
worlds/raw/ keeps only the chosen save (candidate-03.map, Bonia) and the
pinned candidate-5-full.json test fixture.

Usage:
  python src/import_fmg.py <path-to-.map-or-.json> [more paths...]
  python src/import_fmg.py --all          # everything in worlds/raw/
"""

import hashlib
import json
import os
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Derived geography.yamls land in a SCRATCH drop dir under the campaign-data
# root ($CAMPAIGN_DATA_ROOT when set — the studio sets it on every subprocess
# it launches — else the repo's campaign-data/): nothing a run produces can
# land in tracked space by accident (ticket 35). Curated candidates stay
# TRACKED in worlds/candidates/; promoting a drop is an ordinary copy there
# + git add. worlds/raw/ keeps the tracked genesis records and stays put.
DATA_ROOT = pathlib.Path(
    os.environ.get("CAMPAIGN_DATA_ROOT") or (ROOT.parent / "campaign-data"))
RAW_DIR = ROOT / "worlds" / "raw"
OUT_DIR = DATA_ROOT / "scratch" / "map-candidates"

# Every signature import_fmg matches on, and what a miss costs: a drifted
# file imports with that section EMPTY — silently wrong. load_fmg reports
# the sections it found so main() can warn loudly instead.
_SIGNATURES = {
    "features": '"type":"ocean"',
    "cultures": '"base":',
    "states": '"salesTax"',
    "burgs": '"port":',
    "provinces": '"burg":',
    "religions": '"No religion"',
    "rivers": '"discharge":',
}


def load_fmg(path):
    """Return (info, settings, pack, found_sections) from either supported
    format. found_sections is the set of signature keys matched — .map files
    only; full JSON exports carry every section by construction.

    .map files are line-oriented: header | settings JSON | nameBases | an
    embedded SVG snapshot | positional data sections. We extract by content
    signature, not position, so version drift in ordering is survivable.
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    if text.startswith("{"):
        data = json.loads(text)
        info = dict(data["info"])
        settings = data["settings"]
        pack = data["pack"]
        # FMG lists open with a literal 0 placeholder at index 0
        pack = {
            k: [el for el in v if isinstance(el, dict)]
            if isinstance(v, list) else v
            for k, v in pack.items()
        }
        return info, settings, pack, set(_SIGNATURES)

    # CR-tolerant: a dump round-tripped through a Windows editor carries \r\n
    lines = re.split("\r?\n", text)
    header = lines[0].split("|")

    def section_json(signature):
        """Find the data line carrying `signature` and parse it. Data
        sections are arrays; the settings line also contains quoted keys
        (e.g. "port":false in burg-group presets), so require a list and
        skip the settings line."""
        for ln in lines[2:]:
            if signature in ln:
                try:
                    parsed = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, list):
                    return parsed
        return None

    settings = json.loads(lines[1])
    lore = settings.get("lore", {})
    info = {
        "version": header[0],
        "mapName": lore.get("name", "unnamed"),
        "seed": settings.get("seed") or (header[3] if len(header) > 3 else None),
        "width": settings.get("graph", {}).get("width"),
        "height": settings.get("graph", {}).get("height"),
        "mapId": header[6] if len(header) > 6 else None,
    }

    def objs(section):
        if section is None:
            return []
        return [el for el in section if isinstance(el, dict)]

    pack, found = {}, set()
    for key, signature in _SIGNATURES.items():
        section = section_json(signature)
        pack[key] = objs(section)
        if section is not None:
            found.add(key)
    return info, settings, pack, found


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "unnamed"


# Rivers: lore wants names + which settlements they touch, not geometry.
# Each river lists its most POPULOUS burgs within RIVER_NEAR map units of
# its course (cell centers) — alphabetical order silently dropped capitals
# (found 2026-09-25: 4 of 40 rivers on seed 913) — capped at RIVER_LIMIT
# by discharge so the file stays terse (the summary counts them all).
# River/burg name collisions ("the river Chang") are accepted as-is.
RIVER_LIMIT = 40
RIVER_NEAR = 5.0  # map units on the FMG grid (maps are typically 1280x720)
RIVER_SETTLEMENTS_CAP = 6
NEIGHBORS_CAP = 6  # declared in output as neighbors_note when it bites


def _cell_centers(pack):
    """[ (x, y) centroid per cell id ] from either export shape, or None
    when the export carries no usable graph arrays. Columnar headless
    dumps keep cells.v / vertices.p as parallel arrays (position == id);
    browser full JSON exports expand both into per-object lists — the same
    two shapes _cell_biome supports. (Found 2026-10-02: only the columnar
    shape was recognized, so browser exports grounded every river with
    zero settlements while the summary claimed missing graph arrays.)"""
    cells, vertices = pack.get("cells"), pack.get("vertices")
    if isinstance(cells, dict) and isinstance(vertices, dict):
        cells_v, verts_p = cells.get("v"), vertices.get("p")
        if not (isinstance(cells_v, list) and isinstance(verts_p, list)
                and cells_v and verts_p):
            return None
        vlists = enumerate(cells_v)
        pts = dict(enumerate(verts_p))
    elif isinstance(cells, list) and isinstance(vertices, list):
        vlists = [(c.get("i"), c.get("v")) for c in cells
                  if isinstance(c, dict)]
        pts = {v.get("i"): v.get("p") for v in vertices
               if isinstance(v, dict)}
    else:
        return None
    centers: dict[int, tuple] = {}
    for cid, vlist in vlists:
        if not isinstance(cid, int) or not isinstance(vlist, list):
            continue
        xy = [pts[j] for j in vlist if isinstance(j, int) and j in pts]
        if xy:
            centers[cid] = (sum(p[0] for p in xy) / len(xy),
                            sum(p[1] for p in xy) / len(xy))
    if not centers:
        return None
    return [centers.get(i) for i in range(max(centers) + 1)]


def _has_graph_arrays(pack):
    """True when the export carries the graph arrays the settlement
    grounding needs, in either shape (see _cell_centers). Full JSON
    exports do — columnar or per-object lists; .map-section imports
    never."""
    return _cell_centers(pack) is not None


def _river_settlements(pack, burgs, rivers):
    """{river_index: [burg dicts within RIVER_NEAR of the course]}. Needs
    the graph arrays (see _has_graph_arrays); otherwise {}."""
    centers = _cell_centers(pack)
    if centers is None:
        return {}
    near_limit = RIVER_NEAR * RIVER_NEAR
    out: dict[int, list] = {}
    for b in burgs:
        bx, by = b.get("x", 0), b.get("y", 0)
        best = None
        for r in rivers:
            ri = r.get("i")
            for ci in r.get("cells", []):
                c = centers[ci] if (isinstance(ci, int)
                                    and not isinstance(ci, bool)
                                    and 0 <= ci < len(centers)) else None
                if c is None:
                    continue
                d2 = (c[0] - bx) ** 2 + (c[1] - by) ** 2
                if d2 <= near_limit and (best is None or d2 < best[0]):
                    best = (d2, ri)
        if best is not None:
            out.setdefault(best[1], []).append(b)
    return out


def _map_options(settings):
    """The FMG map-options object from any export shape. Browser full JSON
    exports nest it under settings.options.map; a .map file's settings line
    and the headless dump carry the same fields at the top level. Returns
    {} when neither shape is present (very old dumps)."""
    if not isinstance(settings, dict):
        return {}
    opts = settings.get("options")
    if isinstance(opts, dict) and isinstance(opts.get("map"), dict):
        return opts["map"]
    return settings


def _cell_biome(pack):
    """cell id -> biome id, or None when the export carries no per-cell
    data. Two serializations exist: the headless dump keeps columnar arrays
    (pack.cells.biome[i]); browser full JSON exports expand cells into
    per-cell dicts."""
    cells = pack.get("cells")
    if isinstance(cells, dict):
        biome = cells.get("biome")
        if not isinstance(biome, list):
            return None
        return lambda i: (biome[i] if isinstance(i, int)
                          and 0 <= i < len(biome) else None)
    if isinstance(cells, list):
        by_id = {c.get("i"): c.get("biome") for c in cells
                 if isinstance(c, dict)}
        return lambda i: by_id.get(i) if isinstance(i, int) else None
    return None


def _dominant_state_biomes(pack, biome_names):
    """{state_id: most common biome name} by cell count — the biome that
    dominates where the nation lives. Marine (biome 0) is excluded: nations
    are land. {} when the export lacks per-cell data or biome names."""
    if not biome_names:
        return {}
    cells = pack.get("cells")
    if isinstance(cells, dict):
        state, biome = cells.get("state"), cells.get("biome")
        if not (isinstance(state, list) and isinstance(biome, list)):
            return {}
        pairs = list(zip(state, biome))
    elif isinstance(cells, list):
        pairs = [(c.get("state"), c.get("biome")) for c in cells
                 if isinstance(c, dict)]
    else:
        return {}
    counts: dict[int, dict[int, int]] = {}
    for sid, bid in pairs:
        if not isinstance(sid, int) or sid <= 0:
            continue  # 0 is no state
        if not isinstance(bid, int) or bid <= 0:
            continue  # 0 is Marine
        counts.setdefault(sid, {})
        counts[sid][bid] = counts[sid].get(bid, 0) + 1
    out = {}
    for sid, tally in counts.items():
        # most cells wins; ties break to the lower biome id (stable order)
        best = max(tally.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        out[sid] = biome_names.get(best)
    return out


def _latitude_of(info, map_opts):
    """y coordinate -> latitude [90; -90], FMG's own formula (y down the
    map between latN and latN - latT), or None when the dump lacks the
    graph height or the coordinates block."""
    height = info.get("height") if isinstance(info, dict) else None
    coords = (map_opts.get("geography") or {}).get("coordinates") or {}
    lat_n, lat_t = coords.get("latN", 90), coords.get("latT", 180)
    if not isinstance(height, (int, float)) or not height:
        return None
    if not isinstance(lat_n, (int, float)) or not isinstance(lat_t, (int, float)):
        return None

    def latitude(y):
        try:
            y = float(y)
        except (TypeError, ValueError):
            return None
        return round(lat_n - (y / height) * lat_t, 1)

    return latitude


def summarize(info, pack, settings=None):
    states = [s for s in pack.get("states", []) if s.get("i", 0) != 0]
    burgs = [b for b in pack.get("burgs", []) if b.get("i", 0) != 0 and b.get("name")]
    provinces = [p for p in pack.get("provinces", []) if p.get("i", 0) != 0]
    cultures = [c for c in pack.get("cultures", []) if c.get("i", 0) != 0]
    religions = [r for r in pack.get("religions", []) if r.get("i", 0) != 0]
    features = [
        f for f in pack.get("features", [])
        if f.get("type") in ("island", "lake") and f.get("cells", 0) > 50
    ]

    state_by_id = {s["i"]: s for s in states}
    burgs_by_state = {}
    for b in burgs:
        burgs_by_state.setdefault(b.get("state"), []).append(b)
    culture_by_id = {c["i"]: c["name"] for c in cultures
                     if isinstance(c.get("i"), int) and c.get("name")}

    def burg_name(i):
        return next((b["name"] for b in burgs if b.get("i") == i), None)

    # climate context degrades to absent, never wrong: no options (old
    # headless dumps) or no per-cell data (.map-section imports) means no
    # climate fields
    map_opts = _map_options(settings)
    biome_names = {b["i"]: b["name"] for b in pack.get("biomes", [])
                   if isinstance(b, dict) and isinstance(b.get("i"), int)}
    cell_biome = _cell_biome(pack)
    dominant_biome = _dominant_state_biomes(pack, biome_names)
    latitude = _latitude_of(info, map_opts)

    climate_meta = {}
    temps = (map_opts.get("climate") or {}).get("temperature") or {}
    if isinstance(temps, dict):
        for key, src in (("equator_c", "equator"),
                         ("north_pole_c", "northPole"),
                         ("south_pole_c", "southPole")):
            v = temps.get(src)
            if isinstance(v, (int, float)):
                climate_meta[key] = v
    culture_set = map_opts.get("cultures", {}).get("set")
    culture_set = culture_set if isinstance(culture_set, str) else None

    nations = []
    for s in sorted(states, key=lambda s: -(s.get("cells", 0))):
        own = burgs_by_state.get(s["i"], [])
        entry = {
            "id": f"{slugify(s['name'])}-{s['i']}",
            "name": s["name"],
            "kind": s.get("type", "Generic"),
            "capital": burg_name(s.get("capital")),
            "culture": culture_by_id.get(s.get("culture")),
        }
        # dominant biome + capital latitude
        biome = dominant_biome.get(s["i"])
        if biome:
            entry["biome"] = biome
        capital = next((b for b in burgs if b.get("i") == s.get("capital")), None)
        lat = latitude(capital.get("y")) if capital and latitude else None
        if lat is not None:
            entry["latitude"] = lat
        neighbor_names = [
            state_by_id[n]["name"]
            for n in s.get("neighbors", []) if n in state_by_id
        ]
        entry.update({
            "form_of_government": s.get("formName"),
            "burgs": len(own),
            "provinces": sum(
                1 for p in provinces if p.get("state") == s["i"]
            ),
            # FMG exports no state-level rural population; this is the sum
            # of urban burg populations (thousands of inhabitants)
            "urban_population_thousands": round(
                sum(b.get("population", 0) for b in own), 1
            ),
            "neighbors": neighbor_names[:NEIGHBORS_CAP],
            "color": s.get("color"),
        })
        # the cap is declared when it bites, never silent (house rule; the
        # same pattern as the river settlements_note below)
        if len(neighbor_names) > NEIGHBORS_CAP:
            entry["neighbors_note"] = (
                f"top {NEIGHBORS_CAP} of {len(neighbor_names)} neighbors")
        nations.append(entry)

    settlements = sorted(burgs, key=lambda b: -(b.get("population", 0)))[:15]
    major_settlements = []
    for b in settlements:
        entry = {
            "name": b["name"],
            # the dump's burg index (`i`) — gen-city resolves `--burg` by
            # name OR index, and generated worlds can hold two burgs with
            # the same name in different nations, so the Maps tab's picker
            # submits this, not the bare name (ticket 41)
            "burg_id": b.get("i"),
            "nation": state_by_id.get(b.get("state"), {}).get("name"),
            "population_thousands": round(b.get("population", 0), 1),
            "capital": bool(b.get("capital")),
            "port": bool(b.get("port")),
            "kind": b.get("type", "Town"),
        }
        biome = biome_names.get(cell_biome(b.get("cell"))) \
            if cell_biome else None
        if biome:
            entry["biome"] = biome
        lat = latitude(b.get("y")) if latitude else None
        if lat is not None:
            entry["latitude"] = lat
        entry["x"] = round(b.get("x", 0), 1)
        entry["y"] = round(b.get("y", 0), 1)
        major_settlements.append(entry)

    rivers_all = [r for r in pack.get("rivers", [])
                  if isinstance(r, dict) and isinstance(r.get("i"), int)]
    on_river = _river_settlements(pack, burgs, rivers_all)
    rivers = []
    for r in sorted(rivers_all, key=lambda r: -r.get("discharge", 0))[
            :RIVER_LIMIT]:
        nearby = on_river.get(r["i"], [])
        # most POPULOUS first (deterministic name tiebreak), deduped; the
        # cap is declared when it bites, never silently
        ranked, seen = [], set()
        for b in sorted(nearby, key=lambda b: (-b.get("population", 0),
                                               b["name"])):
            if b["name"] not in seen:
                seen.add(b["name"])
                ranked.append(b)
        kept = ranked[:RIVER_SETTLEMENTS_CAP]
        entry = {
            "name": r.get("name") or f"river-{r['i']}",
            "discharge": round(r.get("discharge", 0), 1),
            "length": round(r.get("length", 0), 1),
            "settlements": [b["name"] for b in kept],
        }
        if len(ranked) > len(kept):
            entry["settlements_note"] = (
                f"top {len(kept)} of {len(ranked)} nearby by population")
        rivers.append(entry)

    meta = {
        "source": "Azgaar Fantasy Map Generator",
        "map_name": info.get("mapName"),
        "seed": str(info.get("seed")),
        "fmg_version": info.get("version"),
    }
    if culture_set:
        meta["culture_set"] = culture_set
    if climate_meta:
        meta["climate"] = climate_meta
    return {
        "meta": meta,
        "summary": {
            "nations": len(nations),
            "provinces": len(provinces),
            "settlements": len(burgs),
            "cultures": len(cultures),
            "religions": len(religions),
            "major_landmasses": len(features),
            "rivers": len(rivers_all),
            # distinguish "no settlements computed" (.map section import,
            # no graph arrays in either shape) from "rivers with truly
            # none nearby" — the reason names what was actually missing
            "river_settlements": ("nearby settlements per river, most "
                                  "populous first"
                                  if _has_graph_arrays(pack) else
                                  "not computed — no usable cells/vertices "
                                  "graph arrays (.map imports carry none)"),
        },
        "nations": nations,
        "cultures": [{
            "name": c["name"],
            "kind": c.get("type"),
            "expansionism": c.get("expansionism"),
        } for c in cultures],
        "major_settlements": major_settlements,
        "rivers": rivers,
    }


def import_file(path):
    path = pathlib.Path(path)
    info, settings, pack, found = load_fmg(path)
    # full JSON exports report every section found, so `missing` is .map-only
    missing = sorted(set(_SIGNATURES) - found)
    doc = summarize(info, pack, settings)
    # byte-stable provenance: seed + version only (see module docstring; an
    # mtime permanently dirtied every regeneration — found 2026-09-23)
    seed = info.get("seed")
    suffix = str(seed)[-4:] if seed not in (None, "") else "noseed"
    slug = f"{slugify(info.get('mapName') or 'unnamed')}-{suffix}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{slug}.yaml"
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
    if out.exists() and out.read_text(encoding="utf-8") != text:
        # same name and last-four seed digits but genuinely different
        # content: widen the suffix with a full-seed hash instead of
        # silently overwriting a different world's candidate
        widen = hashlib.sha256(
            str(seed).encode("utf-8")).hexdigest()[:6]
        slug = f"{slug}-{widen}"
        out = OUT_DIR / f"{slug}.yaml"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    return out, doc, missing


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    paths = (
        sorted(p for p in RAW_DIR.iterdir()
               if p.is_file() and p.suffix in (".map", ".json")
               and (p.stem.startswith("candidate-")
                    or p.stem.startswith("headless-")))
        if args == ["--all"]
        else [pathlib.Path(a) for a in args]
    )
    failures = 0
    for p in paths:
        try:
            out, doc, missing = import_file(p)
        except Exception as e:  # noqa: BLE001 — one bad file must not abort the batch
            print(f"{p.name}: IMPORT FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            failures += 1
            continue
        s = doc["summary"]
        print(
            f"{p.name} -> {out.name}: '{doc['meta']['map_name']}' "
            f"(seed {doc['meta']['seed']}): {s['nations']} nations, "
            f"{s['settlements']} settlements, {s['cultures']} cultures"
        )
        if missing:
            # a signature miss means the FMG version drifted and that section
            # imported EMPTY — say so instead of shipping silently wrong data
            print(f"  WARNING: sections not found (imported empty): "
                  f"{', '.join(missing)} — the FMG format may have drifted",
                  file=sys.stderr)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
