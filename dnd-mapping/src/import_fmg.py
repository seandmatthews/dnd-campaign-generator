"""Import an Azgaar FMG export into a geography.yaml for the atlas.

FMG sources accepted:
  - .map save file: pipe-separated header line, then a JSON blob
  - "full" JSON export: {info, settings, mapCoordinates, pack, grid, nameBases}

Maps are derived artifacts: the raw FMG file stays untouched in
worlds/raw/ and this script derives a terse geography.yaml beside the
canonical state. Re-run any time; never hand-edit the output.

Usage:
  python src/import_fmg.py <path-to-.map-or-.json> [more paths...]
  python src/import_fmg.py --all          # everything in worlds/raw/
"""

import json
import pathlib
import re
import sys
from datetime import datetime, timezone

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "worlds" / "raw"
OUT_DIR = ROOT / "worlds" / "candidates"


def load_fmg(path):
    """Return (info, settings, pack) from either supported format.

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
        return info, settings, pack

    lines = text.split("\n")
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

    pack = {
        # signatures chosen to be unique to each section — states also carry
        # "population" and "formName", so those can't be used for burgs/provinces
        "features": objs(section_json('"type":"ocean"')),
        "cultures": objs(section_json('"base":')),
        "states": objs(section_json('"salesTax"')),
        "burgs": objs(section_json('"port":')),
        "provinces": objs(section_json('"burg":')),
        "religions": objs(section_json('"No religion"')),
    }
    return info, settings, pack


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "unnamed"


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

    return {
        "meta": {
            "source": "Azgaar Fantasy Map Generator",
            "map_name": info.get("mapName"),
            "seed": str(info.get("seed")),
            "fmg_version": info.get("version"),
            "imported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        "summary": {
            "nations": len(nations),
            "provinces": len(provinces),
            "settlements": len(burgs),
            "cultures": len(cultures),
            "religions": len(religions),
            "major_landmasses": len(features),
        },
        "nations": nations,
        "cultures": [{
            "name": c["name"],
            "kind": c.get("type"),
            "expansionism": c.get("expansionism"),
        } for c in cultures],
        "major_settlements": major_settlements,
    }


def import_file(path):
    path = pathlib.Path(path)
    info, _settings, pack = load_fmg(path)
    doc = summarize(info, pack)
    slug = f"{slugify(info.get('mapName') or 'unnamed')}-{str(info.get('seed'))[-4:]}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{slug}.yaml"
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True, width=100)
    return out, doc


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    paths = (
        sorted(p for p in RAW_DIR.glob("candidate-*.map"))
        if args == ["--all"]
        else [pathlib.Path(a) for a in args]
    )
    for p in paths:
        out, doc = import_file(p)
        s = doc["summary"]
        print(
            f"{p.name} -> {out.name}: '{doc['meta']['map_name']}' "
            f"(seed {doc['meta']['seed']}): {s['nations']} nations, "
            f"{s['settlements']} settlements, {s['cultures']} cultures"
        )


if __name__ == "__main__":
    main()
