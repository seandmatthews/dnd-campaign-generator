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
the raw/ convention was settled).

Usage:
  python src/import_fmg.py <path-to-.map-or-.json> [more paths...]
  python src/import_fmg.py --all          # everything in worlds/raw/
"""

import hashlib
import json
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "worlds" / "raw"
OUT_DIR = ROOT / "worlds" / "candidates"

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


# Rivers: FMG names every river and gives its course as a cell-id polyline.
# Lore grounding wants names and which settlements they touch, not geometry:
# each river lists the most POPULOUS burgs within RIVER_NEAR map units of
# its course (cell centers), capped — alphabetical order would silently
# drop capitals for alphabet soup (found 2026-09-25: 4 of 40 rivers on seed
# 913 lost real settlements) — and only the RIVER_LIMIT biggest by
# discharge are kept so the file stays terse (the summary still counts
# them all). River names are NOT disambiguated from burg names in the YAML
# ("the river Chang" vs "the town Chang"): the lore prompt reads the text
# as-is and the ambiguity is accepted.
RIVER_LIMIT = 40
RIVER_NEAR = 5.0  # map units on the FMG grid (maps are typically 1280x720)
RIVER_SETTLEMENTS_CAP = 6


def _has_graph_arrays(pack):
    """True when the export carries the graph arrays the settlement
    grounding needs (full JSON exports do; .map-section imports never)."""
    cells = pack.get("cells")
    vertices = pack.get("vertices")
    cells_v = cells.get("v") if isinstance(cells, dict) else None
    verts_p = vertices.get("p") if isinstance(vertices, dict) else None
    return bool(cells_v and verts_p)


def _river_settlements(pack, burgs, rivers):
    """{river_index: [burg dicts within RIVER_NEAR of the course]}.
    Needs the graph arrays (cells.v, vertices.p) — full JSON exports have
    them; a .map-section import gracefully yields no settlement lists."""
    if not _has_graph_arrays(pack):
        return {}
    cells = pack.get("cells")
    vertices = pack.get("vertices")
    cells_v = cells["v"]
    verts_p = vertices["p"]
    centers = []
    for vlist in cells_v:
        pts = [verts_p[j] for j in vlist if isinstance(j, int)
               and 0 <= j < len(verts_p)]
        if pts:
            centers.append((sum(p[0] for p in pts) / len(pts),
                            sum(p[1] for p in pts) / len(pts)))
        else:
            centers.append(None)
    near_limit = RIVER_NEAR * RIVER_NEAR
    out: dict[int, list] = {}
    for b in burgs:
        bx, by = b.get("x", 0), b.get("y", 0)
        best = None
        for r in rivers:
            ri = r.get("i")
            if not isinstance(ri, int):
                continue  # a river without an index cannot be keyed
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


def summarize(info, pack):
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

    nations = []
    for s in sorted(states, key=lambda s: -(s.get("cells", 0))):
        own = burgs_by_state.get(s["i"], [])
        nations.append({
            "id": f"{slugify(s['name'])}-{s['i']}",
            "name": s["name"],
            "kind": s.get("type", "Generic"),
            "capital": burg_name(s.get("capital")),
            "culture": culture_by_id.get(s.get("culture")),
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
            "neighbors": [
                state_by_id[n]["name"]
                for n in s.get("neighbors", []) if n in state_by_id
            ][:6],
            "color": s.get("color"),
        })

    settlements = sorted(burgs, key=lambda b: -(b.get("population", 0)))[:15]
    major_settlements = [{
        "name": b["name"],
        "nation": state_by_id.get(b.get("state"), {}).get("name"),
        "population_thousands": round(b.get("population", 0), 1),
        "capital": bool(b.get("capital")),
        "port": bool(b.get("port")),
        "kind": b.get("type", "Town"),
        "x": round(b.get("x", 0), 1),
        "y": round(b.get("y", 0), 1),
    } for b in settlements]

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

    return {
        "meta": {
            "source": "Azgaar Fantasy Map Generator",
            "map_name": info.get("mapName"),
            "seed": str(info.get("seed")),
            "fmg_version": info.get("version"),
        },
        "summary": {
            "nations": len(nations),
            "provinces": len(provinces),
            "settlements": len(burgs),
            "cultures": len(cultures),
            "religions": len(religions),
            "major_landmasses": len(features),
            "rivers": len(rivers_all),
            # distinguish "no settlements computed" (.map section import,
            # no graph arrays) from "rivers with truly none nearby"
            "river_settlements": ("nearby settlements per river, most "
                                  "populous first"
                                  if _has_graph_arrays(pack) else
                                  "not computed — export lacks graph arrays"),
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
    info, _settings, pack, found = load_fmg(path)
    # full JSON exports report every section found, so `missing` is .map-only
    missing = sorted(set(_SIGNATURES) - found)
    doc = summarize(info, pack)
    # provenance records the dump's own seed + name, never a wall-clock or
    # mtime stamp: re-importing an unchanged dump must stay byte-identical
    # ("never hand-edit" only works if regeneration is diff-clean), and an
    # mtime is not reproducible content — it permanently dirtied tracked
    # derivatives on every regeneration (found 2026-09-23)
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
