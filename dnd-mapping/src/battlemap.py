"""Battlemap pipeline: GeoJSON footprints -> scene -> grid-aligned map -> UVTT.

Stages (each one sentence):
  extract  — pull real building footprints from the settlemaker GeoJSON into a
             scene file (meters), anchored so the battlemap agrees with the
             city map by construction.
  render   — scene file -> vector SVG at exact battlemap pixel dimensions
             (70 px per 1.524 m grid square), with a faint baked-in grid.
  wrap     — scene + rendered PNG -> Universal VTT (.uvtt): walls from
             building rings, doors as portals, image as base64.

Usage:
  python src/battlemap.py extract            # -> worlds/ruravaqan/scenes/harbour-quay/scene.json
  python src/battlemap.py render             # -> scene.svg
  python src/battlemap.py wrap               # -> harbour-quay.uvtt (needs PNG)

The PNG is rasterized from scene.svg with sharp
(scripts/render-scene-png.mjs) at the exact dimensions render() prints.
"""

import base64
import json
import math
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEOJSON = ROOT / "worlds/ruravaqan/ruravaqan.geojson"
SCENE_DIR = ROOT / "worlds/ruravaqan/scenes/harbour-quay"
SCENE_FILE = SCENE_DIR / "scene.json"

# Scratch home for generated sites: campaign-data/maps/sites — under the
# campaign-data root ($CAMPAIGN_DATA_ROOT when set — the studio sets it on
# every subprocess it launches — else the repo's campaign-data/). This home
# is regenerated on demand; canon copies live in the campaign repos
# (ticket 35). worlds/ruravaqan above is a tracked generation record and
# stays in the tree.
DATA_ROOT = pathlib.Path(
    os.environ.get("CAMPAIGN_DATA_ROOT") or (ROOT.parent / "campaign-data"))
SITES_DIR = DATA_ROOT / "maps" / "sites"

PIXELS_PER_GRID = 70
METERS_PER_GRID = 1.524  # a 5-foot square
PX_PER_M = PIXELS_PER_GRID / METERS_PER_GRID
METERS_PER_UNIT = 10.759197265865302  # from the GeoJSON scale metadata

# The one ambient-light value every wrapped scene ships as UVTT
# environment.ambient_light — a LIGHTING choice (Atlas reads its Rec. 709
# luma as lighting.ambient), deliberately not the SVG backdrop colour it
# used to double as: the dungeon's near-black backdrop #120d0b shipped as
# lighting.ambient 0.05 and the table saw black outside torch radius
# (found live 2026-10-23, ticket 59). #6e6a63 reads as luma 0.42 —
# dungeon-dim, the map still readable.
AMBIENT_LIGHT = "#6e6a63"


def u(m):
    """meters -> scene pixels"""
    return round(m * PX_PER_M, 2)


def xt(text):
    """XML-escape text bound for the SVG (& first, or the rest double-escapes).
    Scene names reach <text> labels verbatim — 'Marsh & Fen' must not make
    scene.svg unparseable for the rasterizer."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


# ---------------------------------------------------------------- extract

def extract():
    g = json.loads(GEOJSON.read_text(encoding="utf-8"))
    meta = g["metadata"]
    assert abs(meta["scale"]["meters_per_unit"] - METERS_PER_UNIT) < 1e-6

    buildings = [f for f in g["features"]
                 if f["properties"].get("layer") == "building"
                 and f["properties"].get("wardType") == "harbour"]

    def centroid(poly):
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    # y grows southward: keep the two buildings closest to the water edge
    picked = sorted(buildings, key=lambda f: -centroid(f["geometry"]["coordinates"][0])[1])[:2]
    xs = [p[0] for f in picked for ring in f["geometry"]["coordinates"] for p in ring]
    ys = [p[1] for f in picked for ring in f["geometry"]["coordinates"] for p in ring]

    # Quay-strip scene: the warehouses' seaward walls line the top ~10 m,
    # then cobblestone, then the sea. The buildings' northern bulk is
    # intentionally off-map — this is an exterior scene, not a district map.
    scene_w_grids, scene_h_grids = 46, 30
    w_m = scene_w_grids * METERS_PER_GRID
    h_m = scene_h_grids * METERS_PER_GRID
    x0 = sum(xs) / len(xs) - w_m / 2 / METERS_PER_UNIT
    quay_top_m = 10.0                                   # warehouse walls end here
    quay_y_m = 24.0                                     # water line
    y0 = max(ys) - quay_top_m / METERS_PER_UNIT

    def to_m(pts):
        return [[round((px - x0) * METERS_PER_UNIT, 2), round((py - y0) * METERS_PER_UNIT, 2)]
                for px, py in pts]

    feats = []
    doors = []
    for i, f in enumerate(picked):
        ring = to_m(f["geometry"]["coordinates"][0])
        bid = f["properties"]["building_id"]
        feats.append({"kind": "building", "id": bid, "polygon": ring,
                      "label": f"Warehouse {i + 1}", "walls": True})
        lowest = sorted(zip(ring, ring[1:] + ring[:1]), key=lambda e: -(e[0][1] + e[1][1]))[0]
        (ax, ay), (bx, by) = lowest
        mid = ((ax + bx) / 2, (ay + by) / 2)
        angle = math.degrees(math.atan2(by - ay, bx - ax))
        doors.append({"at": [round(mid[0], 2), round(mid[1], 2)],
                      "width_m": 2.0, "angle": round(angle, 1),
                      "closed": True, "kind": "door", "label": f"{bid} door"})
    # crates on the quay + bollards along the water line (dressing, not atlas data)
    rng = random.Random(636343)
    props = []
    for f in picked:
        ring_m = to_m(f["geometry"]["coordinates"][0])
        cx = sum(p[0] for p in ring_m) / len(ring_m)
        for _ in range(6):
            props.append({"shape": "rect", "at": [round(cx - 16 + rng.uniform(0, 32), 2),
                                                  round(rng.uniform(quay_top_m + 1.5, quay_y_m - 1.5), 2)],
                          "w": 1.2, "h": 1.2, "color": "#7a5c3a", "label": "crates", "blocks": False})
    for bx in range(4, int(w_m) - 3, 6):
        props.append({"shape": "circle", "at": [float(bx), quay_y_m - 0.8],
                      "r": 0.25, "color": "#3d3a35", "label": "bollard", "blocks": False})
    # one pier (artistic: the real piers are SVG-only, not GeoJSON polygons)
    props.append({"shape": "rect", "at": [12.0, round(quay_y_m + 8, 2)], "w": 5.0, "h": 18.0,
                  "color": "#8a7a5e", "label": "pier", "blocks": False})

    scene = {
        "name": "Harbour Quay",
        "provenance": {
            "anchored_to": str(GEOJSON.relative_to(ROOT)),
            "note": ("building footprints are real GeoJSON geometry; the pier and "
                     "crates/bollards are dressing; scene is cropped to the quay strip"),
            "meters_per_unit": METERS_PER_UNIT,
        },
        "grids": {"x": scene_w_grids, "y": scene_h_grids},
        "pixels_per_grid": PIXELS_PER_GRID,
        "meters_per_grid": METERS_PER_GRID,
        "features": [
            {"kind": "water", "polygon": [[0, quay_y_m], [round(w_m, 2), quay_y_m],
                                          [round(w_m, 2), round(h_m, 2)], [0, round(h_m, 2)]],
             "label": "The Sea"},
            {"kind": "ground", "polygon": [[0, 0], [round(w_m, 2), 0], [round(w_m, 2), quay_y_m],
                                           [0, quay_y_m]], "label": "cobble quay"},
            *feats,
            *[{"kind": "prop", **p} for p in props],
        ],
        "doors": doors,
        "labels": [
            {"text": "HARBOUR QUAY", "at": [round(w_m / 2, 2), 4.0], "size": 30},
            {"text": "the sea", "at": [round(w_m / 2, 2), round(quay_y_m + 14, 2)], "size": 22},
        ],
    }
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    SCENE_FILE.write_text(json.dumps(scene, indent=1), encoding="utf-8")
    print(f"scene: {scene_w_grids}x{scene_h_grids} grids ({w_m:.0f}x{h_m:.0f} m), "
          f"{len(picked)} buildings, {len(doors)} doors -> {SCENE_FILE}")


# ---------------------------------------------------------------- site generators
# Dungeons/caves/camps bypass the settlemaker GeoJSON: their own geometry
# feeds the SAME scene schema, so render and wrap work unchanged.
# Output: <campaign-data>/maps/sites/<name>/ (SITES_DIR, top of file).

import random


def _save_site(name, scene):
    out = SITES_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "scene.json").write_text(json.dumps(scene, indent=1), encoding="utf-8")
    return out


def _scene(name, provenance, grids, backdrop, features, doors, labels):
    """The scene skeleton shared by the site generators; pixels_per_grid and
    meters_per_grid (UVTT-critical) have exactly this one home. The backdrop
    paints the SVG; ambient_light is the UVTT lighting value — decoupled,
    with one shared default, because a backdrop chosen as art is not a
    lighting level (ticket 59)."""
    return {
        "name": name,
        "provenance": provenance,
        "grids": {"x": grids[0], "y": grids[1]},
        "pixels_per_grid": PIXELS_PER_GRID,
        "meters_per_grid": METERS_PER_GRID,
        "backdrop": backdrop,
        "ambient_light": AMBIENT_LIGHT,
        "features": features,
        "doors": doors,
        "labels": labels,
    }


def _ground_rect(w_m, h_m, label, color=None):
    """Full-rect ground polygon covering the whole scene."""
    ground = {"kind": "ground", "polygon": [[0, 0], [w_m, 0], [w_m, h_m], [0, h_m]]}
    if color:
        ground["color"] = color
    ground["label"] = label
    return ground


def _cell_walls(cells, cols, rows):
    """Wall rectangles around every edge between floor and non-floor cells,
    collinear runs merged into single rings. Returns [ [(x,y)...] ] in meters."""
    T = 0.25  # wall thickness (m)
    # h_edges ride (kind, y, x) — runs() groups by the SECOND element and
    # the consumer reads it as the fixed axis; grouping a horizontal edge
    # by its X used to lay every horizontal wall run at a Y taken from a
    # cell column (walls transposed and half off-canvas, found live
    # 2026-10-20 when Atlas first refused the map). v_edges already group
    # by their fixed X.
    h_edges, v_edges = [], []
    for y in range(rows):
        for x in range(cols):
            if not cells[y][x]:
                continue
            if y == 0 or not cells[y - 1][x]:
                h_edges.append(("top", y, x))
            if y == rows - 1 or not cells[y + 1][x]:
                h_edges.append(("bot", y, x))
            if x == 0 or not cells[y][x - 1]:
                v_edges.append(("lef", x, y))
            if x == cols - 1 or not cells[y][x + 1]:
                v_edges.append(("rig", x, y))

    def runs(edges):
        groups = {}
        for kind, a, b in edges:
            groups.setdefault((kind, a), []).append(b)
        out = []
        for (kind, fixed), vals in groups.items():
            vals.sort()
            start = prev = vals[0]
            for v in vals[1:]:
                if v == prev + 1:
                    prev = v
                    continue
                out.append((kind, fixed, start, prev))
                start = prev = v
            out.append((kind, fixed, start, prev))
        return out

    rings = []
    for kind, y, x0, x1 in runs(h_edges):
        yy = y * METERS_PER_GRID + (0 if kind == "top" else METERS_PER_GRID)
        rings.append([[x0 * METERS_PER_GRID - T, yy - T], [(x1 + 1) * METERS_PER_GRID + T, yy - T],
                      [(x1 + 1) * METERS_PER_GRID + T, yy + T], [x0 * METERS_PER_GRID - T, yy + T]])
    for kind, x, y0, y1 in runs(v_edges):
        xx = x * METERS_PER_GRID + (0 if kind == "lef" else METERS_PER_GRID)
        rings.append([[xx - T, y0 * METERS_PER_GRID - T], [xx + T, y0 * METERS_PER_GRID - T],
                      [xx + T, (y1 + 1) * METERS_PER_GRID + T], [xx - T, (y1 + 1) * METERS_PER_GRID + T]])
    return rings


def _floor_features(cells, cols, rows, floor_color="#6b6459", wall_color="#2e2b28"):
    feats = []
    for y in range(rows):
        for x in range(cols):
            if cells[y][x]:
                feats.append({"kind": "prop", "shape": "rect",
                              "at": [(x + 0.5) * METERS_PER_GRID, (y + 0.5) * METERS_PER_GRID],
                              "w": METERS_PER_GRID, "h": METERS_PER_GRID,
                              "color": floor_color, "label": "floor", "blocks": False})
    for ring in _cell_walls(cells, cols, rows):
        feats.append({"kind": "wall", "polygon": ring, "walls": True,
                      "color": wall_color, "label": "rock"})
    return feats


def _door_cell(acy, acx, bx, by, bw, bh, bcx, bcy):
    """The cell + leaf angle for the door where the L corridor from room A
    enters room B. The door belongs to the wall the corridor actually
    crosses into B, and the leaf runs along that wall: top/bottom walls are
    horizontal (angle 0), side walls vertical (angle 90).

    Found 2026-10-23 (ticket 59): when A's center row lies inside B's row
    span (rooms moat-separated in columns but overlapping in rows), the
    vertical leg stays inside B and never pierces its top/bottom wall —
    the HORIZONTAL leg enters through B's side wall at A's center row,
    and that is the breach cell. The old code still answered with the far
    top/bottom wall, landing doors on solid rock."""
    if bcy == acy:
        return (bx if bcx > acx else bx + bw - 1), bcy, 90
    if by <= acy <= by + bh - 1:
        # A's center row is inside B's row span: the horizontal leg is the
        # one that breaches, through B's side wall, at A's center row
        return (bx if bcx > acx else bx + bw - 1), acy, 90
    edge_x = min(max(bcx, bx), bx + bw - 1)
    edge_y = by if bcy > acy else by + bh - 1
    return edge_x, edge_y, 0


def gen_dungeon(seed="1", name="dungeon", n_rooms=7):
    rng = random.Random(seed)
    cols, rows = 42, 28
    cells = [[False] * cols for _ in range(rows)]
    rooms = []
    for _ in range(n_rooms * 12):
        w, h = rng.randint(4, 8), rng.randint(3, 6)
        x, y = rng.randint(1, cols - w - 2), rng.randint(1, rows - h - 2)
        if any(cells[yy][xx] for yy in range(y - 1, y + h + 1) for xx in range(x - 1, x + w + 1)):
            continue
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                cells[yy][xx] = True
        rooms.append((x, y, w, h))
        if len(rooms) >= n_rooms:
            break
    if len(rooms) < n_rooms:
        # the alternative is shipping fewer rooms in silence
        print(f"note: placed {len(rooms)} of {n_rooms} requested rooms "
              f"(the grid could not fit more)")
    # connect room centers in sequence with L corridors (1 cell wide)
    def carve(x0, y0, x1, y1):
        for xx in range(min(x0, x1), max(x0, x1) + 1):
            cells[y0][xx] = True
        for yy in range(min(y0, y1), max(y0, y1) + 1):
            cells[yy][x1] = True
    doors = []
    for (ax, ay, aw, ah), (bx, by, bw, bh) in zip(rooms, rooms[1:]):
        acx, acy = ax + aw // 2, ay + ah // 2
        bcx, bcy = bx + bw // 2, by + bh // 2
        carve(acx, acy, bcx, bcy)
        edge_x, edge_y, angle = _door_cell(acy, acx, bx, by, bw, bh, bcx, bcy)
        doors.append({"at": [(edge_x + 0.5) * METERS_PER_GRID, (edge_y + 0.5) * METERS_PER_GRID],
                      "width_m": 1.8, "angle": angle,
                      # closed:true is the honest initial state of a keyed
                      # dungeon door — and the only thing Atlas ever places:
                      # in the UVTT contract closed:false is a WINDOW and the
                      # importer drops it entirely (ticket 59; this used to
                      # be a coin flip that ate half the doors live)
                      "closed": True, "kind": "door", "label": "door"})
    feats = _floor_features(cells, cols, rows, floor_color="#6b6459", wall_color="#241f1d")
    lights = [{"kind": "light", "at": [(x + w / 2) * METERS_PER_GRID, (y + h / 2) * METERS_PER_GRID],
               "r": 6.0, "color": "#ffb45e", "label": "torch"} for x, y, w, h in rooms[:4]]
    scene = _scene(
        # rooms rides in the provenance (ticket 60): it is the one gen-dungeon
        # parameter besides the seed, and without it a provenance-driven
        # regen could not reproduce the scene's room count
        name, {"generator": f"battlemap.py gen-dungeon seed={seed} "
                            f"rooms={n_rooms}"}, (cols, rows),
        "#120d0b",
        [_ground_rect(cols * METERS_PER_GRID, rows * METERS_PER_GRID, "bedrock"),
         *feats, *lights],
        doors,
        [{"text": name.upper(), "at": [cols * METERS_PER_GRID / 2, 2.2], "size": 26}],
    )
    out = _save_site(name, scene)
    print(f"dungeon '{name}': {len(rooms)} rooms, {len(doors)} doors -> {out}")


def gen_cave(seed="1", name="cave"):
    rng = random.Random(seed)
    cols, rows = 46, 30
    grid = [[True if rng.random() < 0.44 else False for _ in range(cols)] for _ in range(rows)]
    for _ in range(4):  # cellular automata smoothing: rock if <5 floor neighbours
        nxt = [[False] * cols for _ in range(rows)]
        for y in range(rows):
            for x in range(cols):
                n = sum(grid[yy][xx] for yy in range(max(0, y - 1), min(rows, y + 2))
                        for xx in range(max(0, x - 1), min(cols, x + 2)))
                nxt[y][x] = n >= 5
        grid = nxt
    # keep the largest connected floor region
    seen = [[False] * cols for _ in range(rows)]
    best = []
    for y in range(rows):
        for x in range(cols):
            if grid[y][x] and not seen[y][x]:
                stack, region = [(x, y)], []
                seen[y][x] = True
                while stack:
                    cx, cy = stack.pop()
                    region.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < cols and 0 <= ny < rows and grid[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
                if len(region) > len(best):
                    best = region
    cells = [[False] * cols for _ in range(rows)]
    for x, y in best:
        cells[y][x] = True
    feats = _floor_features(cells, cols, rows, floor_color="#6f6a5e", wall_color="#26221f")
    # entrance: a floor cell on the region's northern edge
    north = min(best, key=lambda p: p[1])
    doors = [{"at": [(north[0] + 0.5) * METERS_PER_GRID, (north[1] + 0.2) * METERS_PER_GRID],
              # the entrance is a DOOR: closed:false reads as a window and
              # Atlas places nothing for it (ticket 59) — the cave used to
              # ship an entrance that did not exist
              "width_m": 2.0, "angle": 0, "closed": True, "kind": "door",
              "label": "entrance"}]
    lights = [{"kind": "light", "at": [(x + 0.5) * METERS_PER_GRID, (y + 0.5) * METERS_PER_GRID],
               "r": 5.0, "color": "#ffb45e", "label": "campfire"} for x, y in best[::len(best) // 3 + 1][:3]]
    scene = _scene(
        name,
        {"generator": f"battlemap.py gen-cave seed={seed} (cellular automata, largest cavity)"},
        (cols, rows), "#0e0c0a",
        [_ground_rect(cols * METERS_PER_GRID, rows * METERS_PER_GRID, "rock"),
         *feats, *lights],
        doors,
        [{"text": name.upper(), "at": [cols * METERS_PER_GRID / 2, 2.2], "size": 26}],
    )
    out = _save_site(name, scene)
    print(f"cave '{name}': {len(best)} floor cells -> {out}")


def gen_camp(seed="1", name="camp"):
    rng = random.Random(seed)
    w_m, h_m = 70.0, 46.0
    feats = [_ground_rect(w_m, h_m, "marsh grass", "#5e6b4a")]
    # marsh pools: irregular blobs (dressing)
    for i, (cx, cy) in enumerate([(12, 34), (58, 12)]):
        pts = []
        for k in range(10):
            a = k / 10 * 2 * math.pi
            r = rng.uniform(3.5, 6.5)
            pts.append([round(cx + math.cos(a) * r, 2), round(cy + math.sin(a) * r, 2)])
        feats.append({"kind": "water", "polygon": pts, "label": "marsh pool"})
    # tents in a ring around the fire
    tents = []
    for k in range(7):
        a = k / 7 * 2 * math.pi + rng.uniform(-0.2, 0.2)
        tx, ty = 35 + math.cos(a) * rng.uniform(9, 13), 22 + math.sin(a) * rng.uniform(6, 9)
        tents.append({"kind": "prop", "shape": "circle", "at": [round(tx, 2), round(ty, 2)],
                      "r": 1.4, "color": "#6d5a3f", "label": "tent", "blocks": True})
    feats += tents
    # palisade arcs (partial walls, 0.3 m thick) north and east
    def palisade(x0, y0, x1, y1):
        t = 0.3
        if y0 == y1:  # horizontal
            poly = [[x0, y0 - t], [x1, y0 - t], [x1, y1 + t], [x0, y1 + t]]
        else:  # vertical
            poly = [[x0 - t, y0], [x1 + t, y0], [x1 + t, y1], [x0 - t, y1]]
        feats.append({"kind": "wall", "walls": True, "color": "#4a3d2c",
                      "label": "palisade", "polygon": poly})
    palisade(2, 2, 30, 2)
    palisade(40, 2, 68, 2)
    palisade(68, 2, 68, 20)
    # loot crates near the fire
    for _ in range(5):
        feats.append({"kind": "prop", "shape": "rect",
                      "at": [round(35 + rng.uniform(-6, 6), 2), round(30 + rng.uniform(0, 5), 2)],
                      "w": 1.2, "h": 1.2, "color": "#7a5c3a", "label": "loot", "blocks": False})
    lights = [
        {"kind": "light", "at": [35.0, 22.0], "r": 9.0, "color": "#ff9e4a", "label": "campfire"},
        {"kind": "light", "at": [8.0, 8.0], "r": 5.0, "color": "#ffb45e", "label": "watchfire"},
        {"kind": "light", "at": [62.0, 36.0], "r": 5.0, "color": "#ffb45e", "label": "watchfire"},
    ]
    scene = _scene(
        name, {"generator": f"battlemap.py gen-camp seed={seed}"}, (46, 30),
        "#1a2016", [*feats, *lights], [],
        [{"text": name.upper(), "at": [w_m / 2, 4.0], "size": 30},
         {"text": "the marsh", "at": [w_m / 2, h_m - 4], "size": 20}],
    )
    out = _save_site(name, scene)
    print(f"camp '{name}': {len(tents)} tents, {len(lights)} lights -> {out}")




COL = {"water": "#5b8ea6", "ground": "#a29a8c", "building": "#8b8071",
       "prop": "#7a5c3a", "grid": "#00000014", "wall": "#33302b"}


def render():
    scene = json.loads(SCENE_FILE.read_text(encoding="utf-8"))
    W = u(scene["grids"]["x"] * scene["meters_per_grid"])
    H = u(scene["grids"]["y"] * scene["meters_per_grid"])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    parts.append(f'<rect width="{W}" height="{H}" fill="{scene.get("backdrop", COL["ground"])}"/>')

    def poly_pts(pts):
        return " ".join(f"{u(x)},{u(y)}" for x, y in pts)

    # ground/water first
    for f in scene["features"]:
        if f["kind"] == "water":
            parts.append(f'<polygon points="{poly_pts(f["polygon"])}" fill="{COL["water"]}"/>')
        elif f["kind"] == "ground":
            parts.append(f'<polygon points="{poly_pts(f["polygon"])}" fill="{f.get("color", COL["ground"])}"/>')
    # faint grid
    for gx in range(scene["grids"]["x"] + 1):
        parts.append(f'<line x1="{u(gx * scene["meters_per_grid"])}" y1="0" x2="{u(gx * scene["meters_per_grid"])}" y2="{H}" stroke="{COL["grid"]}" stroke-width="1"/>')
    for gy in range(scene["grids"]["y"] + 1):
        parts.append(f'<line x1="0" y1="{u(gy * scene["meters_per_grid"])}" x2="{W}" y2="{u(gy * scene["meters_per_grid"])}" stroke="{COL["grid"]}" stroke-width="1"/>')
    # buildings/walls: floor, wall outline
    for f in scene["features"]:
        if f["kind"] == "building":
            parts.append(f'<polygon points="{poly_pts(f["polygon"])}" fill="{f.get("color", COL["building"])}" stroke="{COL["wall"]}" stroke-width="6"/>')
        elif f["kind"] == "wall":
            parts.append(f'<polygon points="{poly_pts(f["polygon"])}" fill="{f.get("color", COL["wall"])}"/>')
        elif f["kind"] == "prop":
            if f["shape"] == "rect":
                parts.append(f'<rect x="{u(f["at"][0] - f["w"] / 2)}" y="{u(f["at"][1] - f["h"] / 2)}" '
                             f'width="{u(f["w"])}" height="{u(f["h"])}" fill="{f.get("color", COL["prop"])}" '
                             f'stroke="{COL["wall"]}" stroke-width="2"/>')
            elif f["shape"] == "circle":
                parts.append(f'<circle cx="{u(f["at"][0])}" cy="{u(f["at"][1])}" r="{u(f["r"])}" '
                             f'fill="{f.get("color", COL["prop"])}"/>')
    # light glows on top
    for f in scene["features"]:
        if f["kind"] == "light":
            parts.append(f'<defs><radialGradient id="g{u(f["at"][0])}-{u(f["at"][1])}">'
                         f'<stop offset="0%" stop-color="{f["color"]}" stop-opacity="0.85"/>'
                         f'<stop offset="100%" stop-color="{f["color"]}" stop-opacity="0"/>'
                         f'</radialGradient></defs>')
            parts.append(f'<circle cx="{u(f["at"][0])}" cy="{u(f["at"][1])}" r="{u(f["r"])}" '
                         f'fill="url(#g{u(f["at"][0])}-{u(f["at"][1])})"/>')
    # doors: gaps drawn as light segments over the wall
    for d in scene["doors"]:
        (dx, dy) = d["at"]
        half = d["width_m"] / 2
        ex, ey = math.cos(math.radians(d["angle"])) * half, math.sin(math.radians(d["angle"])) * half
        parts.append(f'<line x1="{u(dx - ex)}" y1="{u(dy - ey)}" x2="{u(dx + ex)}" y2="{u(dy + ey)}" '
                     f'stroke="#c9b98a" stroke-width="8"/>')
    for lab in scene.get("labels", []):
        parts.append(f'<text x="{u(lab["at"][0])}" y="{u(lab["at"][1])}" font-family="Georgia, serif" '
                     f'font-size="{lab["size"]}" fill="#3b352c" text-anchor="middle" '
                     f'letter-spacing="4">{xt(lab["text"])}</text>')
    parts.append("</svg>")
    out = SCENE_DIR / "scene.svg"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"rendered {out} — rasterize with: node scripts/render-scene-png.mjs")


# ---------------------------------------------------------------- wrap UVTT

def _png_size(data: bytes):
    """(width, height) from PNG bytes — the IHDR is always bytes 16-24."""
    import struct
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def wrap(png_name="scene.png"):
    scene = json.loads(SCENE_FILE.read_text(encoding="utf-8"))
    png = (SCENE_DIR / png_name).read_bytes()
    W = u(scene["grids"]["x"] * scene["meters_per_grid"])
    H = u(scene["grids"]["y"] * scene["meters_per_grid"])
    pw, ph = _png_size(png)
    if (pw, ph) != (W, H):
        sys.exit(f"error: {png_name} is {pw}x{ph} but the scene resolves to "
                 f"{W}x{H} — re-render and re-rasterize before wrapping")

    def cell_units(px):
        """scene pixels -> whole cells when exact, else 4-decimal cells.
        map_size and every coordinate ride in CELL units: Atlas derives
        one cell as imageWidth / mapSize.x (uvttCellSize), so a map_size
        in pixels reads as a 1-pixel-per-cell grid and is refused "too
        small for its grid" — live-proven against the installed 0.7.1
        bundle (2026-10-20) and its faithful port (companion scene.py).
        The Dungeondraft dialect's pixels+pixels_per_grid pair is not
        what the seam's target reads; pixels_per_grid is dropped rather
        than shipped next to a cells-valued map_size it would
        contradict."""
        v = px / PIXELS_PER_GRID
        return int(v) if float(v).is_integer() else round(v, 4)

    def pt(p):
        return {"x": cell_units(u(p[0])), "y": cell_units(u(p[1]))}

    line_of_sight = []
    for f in scene["features"]:
        if not (f.get("walls") and f["kind"] in ("building", "wall")):
            continue
        ring = [pt(p) for p in f["polygon"]]
        # a wall ring is closed geometry: repeat the first point as the
        # last, or the importer chains N points into N-1 wall segments and
        # every open rectangle ships with a see/shoot-through gap at one
        # corner (ticket 59; the live ember-coast file carried 58 open
        # rings — every one of them short one edge)
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        line_of_sight.append(ring)
    # Universal VTT (dd2vtt): a portal is a two-point "bounds" segment plus a
    # "position" center; a light is "position" + "range". The earlier flat
    # shape imported as walls but silently dropped doors/lights in
    # spec-conforming VTTs.
    portals = []
    for d in scene["doors"]:
        cx, cy = cell_units(u(d["at"][0])), cell_units(u(d["at"][1]))
        half = cell_units(u(d["width_m"])) / 2
        ang = math.radians(d["angle"])
        dx, dy = math.cos(ang) * half, math.sin(ang) * half
        portals.append({
            "bounds": [{"x": round(cx - dx, 4), "y": round(cy - dy, 4)},
                       {"x": round(cx + dx, 4), "y": round(cy + dy, 4)}],
            "closed": d.get("closed", True),
            "color": "#a25a3c",
            "position": {"x": cx, "y": cy},
            "rotation": 0,
            "freely movable": False,
        })
    uvtt = {
        "format": 0.3,
        "resolution": {"map_size": {"x": cell_units(W), "y": cell_units(H)}},
        "image": base64.b64encode(png).decode(),
        "line_of_sight": line_of_sight,
        "portals": portals,
        "environment": {"ambient_light": scene.get("ambient_light", AMBIENT_LIGHT),
                        "sunlight_angle": 90},
        "lights": [
            {"angle": 360, "brightness": 0.4, "color": f["color"], "contrast": 0,
             "enabled": True, "intensity": 1,
             "position": {"x": cell_units(u(f["at"][0])), "y": cell_units(u(f["at"][1]))},
             "range": cell_units(u(f["r"])), "shadows": 0}
            for f in scene["features"] if f["kind"] == "light"
        ],
    }
    slug = re.sub(r"[^a-z0-9]+", "-", scene.get("name", "scene").lower()).strip("-")
    out = SCENE_DIR / f"{slug}.uvtt"
    out.write_bytes(json.dumps(uvtt).encode())
    print(f"wrote {out} ({out.stat().st_size // 1024} KB): {len(line_of_sight)} wall loops, "
          f"{len(portals)} portals, {W}x{H}px @ {PIXELS_PER_GRID}px/grid "
          f"= {cell_units(W)}x{cell_units(H)} cells")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    argv = sys.argv[2:]

    def arg(flag, default=None):
        if flag not in argv:
            return default
        i = argv.index(flag) + 1
        if i >= len(argv):
            sys.exit(f"error: {flag} needs a value")
        return argv[i]

    if cmd == "extract":
        extract()
    elif cmd in ("render", "wrap"):
        SCENE_DIR = pathlib.Path(arg("--dir", str(SCENE_DIR)))
        SCENE_FILE = SCENE_DIR / "scene.json"
        render() if cmd == "render" else wrap(arg("--png", "scene.png"))
    elif cmd in ("gen-dungeon", "gen-cave", "gen-camp"):
        name = arg("--name", cmd.removeprefix("gen-"))
        # --name becomes a directory under worlds/: separators or dot-dot escape
        # the engine tree (the studio validates its own input; this guards the
        # documented CLI entry point)
        if ("/" in name or "\\" in name or name in (".", "..")
                or pathlib.Path(name).name != name):
            sys.exit(f"error: --name must be a bare directory name, got {name!r}")
        if cmd == "gen-dungeon":
            try:
                rooms = int(arg("--rooms", "7"))
            except ValueError:
                sys.exit("error: --rooms must be an integer")
            gen_dungeon(arg("--seed", "1"), name, rooms)
        elif cmd == "gen-cave":
            gen_cave(arg("--seed", "1"), name)
        else:
            gen_camp(arg("--seed", "1"), name)
    else:
        print(__doc__)
        print("site generators: gen-dungeon --seed S --name N [--rooms N] | "
              "gen-cave --seed S --name N | gen-camp --seed S --name N")
        print("scene stages: extract | render --dir D | wrap --dir D [--png P]")
