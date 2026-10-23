"""A vendored, stdlib-only port of the Atlas .uvtt importer the seam
targets — reduced from the companion's faithful port (dcg-avtt-integration
v0.2.0, ``src/avtt/uvtt.py`` + ``src/avtt/scene.py``, themselves ports of
Atlas's ``parseUvtt.ts`` + ``uvttToScene.ts`` + ``uvttClip.ts``). Vendored
here rather than imported because the companion tree is a nested git repo
that worktrees and fresh clones do not carry — the consumer-contract test
must run everywhere the fast tier does.

What is ported, faithfully (the semantics the contract test asserts):

  - the image is measured from its own PNG header, never the file's
    claims; one cell = imageWidth / mapSize.x, with the importer's own
    refusals (sides disagreeing by >2 px; a cell under 10 px);
  - walls, doors and lights may lie on the image plus one cell around
    it; anything outside is CLIPPED or DROPPED and counted in
    ``skipped`` — never silently;
  - portals: ``closed`` places a door wall; NOT closed is a WINDOW, and
    Atlas places nothing for windows (uvttToScene.ts: "Dungeondraft's
    windows: Atlas places nothing");
  - ``lighting.ambient`` is the Rec. 709 luma of
    ``environment.ambient_light`` in hundredths, 1 when the file names
    none.

Omitted, with the reason: the light-emission math and the .atlasmap
envelope (the contract asserts no light or envelope semantics — lights
are counted, and the walls/doors/ambient/counts below are exactly what
the assertions need). Keep this file in step with the companion when the
pin moves: re-read the tagged ``src/avtt/scene.py`` before changing any
constant or refusal here.
"""

import base64
import binascii
import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

# Pinned constants (their home modules at the companion's v0.2.0 tag)
SIDE_TOLERANCE_PX = 2       # uvttToScene.ts: exporters round each side to whole pixels
CELL_PIXELS_LIMIT = 10      # uvttTypes.ts UVTT_LIMITS.cellPixels
MAP_CELLS_LIMIT = 4_096     # UVTT_LIMITS.mapCells
DISTANCE_LIMIT = 1_000_000  # UVTT_LIMITS.distance


class SceneRefused(Exception):
    """The importer's own whole-map refusal, after the file parsed."""


@dataclass(frozen=True)
class CellRect:
    min_x: float
    min_y: float
    max_x: float
    max_y: float


# --- JS number semantics the port must reproduce -----------------------------

def _js_round(value: float) -> int:
    """``Math.round``: halves go up (toward +inf); Python's round goes to even."""
    return math.floor(value + 0.5)


# --- uvttClip.ts --------------------------------------------------------------

def placeable_rect(size) -> CellRect:
    """Where walls, doors and lights may lie: on the image and up to one
    cell around it."""
    return CellRect(-1, -1, size[0] + 1, size[1] + 1)


def is_within(point, rect: CellRect) -> bool:
    return rect.min_x <= point[0] <= rect.max_x and rect.min_y <= point[1] <= rect.max_y


def clip_segment(a, b, rect: CellRect):
    """The part of segment a-b within rect, or None when none of it is
    (touching the rectangle in one point is none). Liang-Barsky, as the
    importer clips."""
    if is_within(a, rect) and is_within(b, rect):
        return a, b
    dx, dy = b[0] - a[0], b[1] - a[1]
    enter, leave = 0.0, 1.0
    for direction, room in ((-dx, a[0] - rect.min_x), (dx, rect.max_x - a[0]),
                            (-dy, a[1] - rect.min_y), (dy, rect.max_y - a[1])):
        if direction == 0:
            if room < 0:
                return None
        elif direction < 0:
            enter = max(enter, room / direction)
        else:
            leave = min(leave, room / direction)
    if enter >= leave:
        return None

    def at(t):
        return (min(rect.max_x, max(rect.min_x, a[0] + dx * t)),
                min(rect.max_y, max(rect.min_y, a[1] + dy * t)))

    return (at(enter) if enter > 0 else a, at(leave) if leave < 1 else b)


# --- light/ambient helpers (uvttToScene.ts) -----------------------------------

def _brightness_of(color: str) -> float:
    """How bright a ``#rrggbb`` colour is, 0 to 1 (Rec. 709 luma), in
    hundredths — this is exactly what Atlas writes as lighting.ambient."""
    channel = lambda start: int(color[start:start + 2], 16) / 255
    return _js_round((0.2126 * channel(1) + 0.7152 * channel(3)
                      + 0.0722 * channel(5)) * 100) / 100


def cell_size_for(size, width: int, height: int):
    """World pixels one cell spans on an image of width x height pixels, or
    None when the image does not fit the stated map size (its cells would
    not be square)."""
    cell_size = width / size[0]
    return cell_size if abs(cell_size * size[1] - height) <= SIDE_TOLERANCE_PX else None


def checked_cell_size(size, width: int, height: int) -> float:
    """``cell_size_for`` plus the importer's whole-map refusals: an image
    that does not fit the stated map size, and one too small for its grid."""
    cell_size = cell_size_for(size, width, height)
    if cell_size is None:
        raise SceneRefused(f"The map image is {width} x {height} pixels, which does "
                           f"not fit a map of {size[0]:g} x {size[1]:g} squares.")
    if cell_size < CELL_PIXELS_LIMIT:
        raise SceneRefused(f"The map image is too small for its grid: {width} x {height} "
                           f"pixels for {size[0]:g} x {size[1]:g} squares.")
    return cell_size


# --- parseUvtt.ts (reduced: the fields the conversion consumes) ---------------

_COLOR = re.compile(r"^#?(?:[0-9a-f]{2})?([0-9a-f]{6})$", re.IGNORECASE)
_DATA_URL_HEAD = re.compile(r"^\s*data:[^,]{0,100},")


def _read_number(value, what: str, minimum: float, maximum: float) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if not ok:
        raise SceneRefused(f"{what} is missing or not a number.")
    if value < minimum or value > maximum:
        raise SceneRefused(f"{what} is out of range.")
    return float(value)


def _read_point(value, what: str):
    if not isinstance(value, dict):
        raise SceneRefused(f"{what} is missing or not a position.")
    return (_read_number(value.get("x"), f"{what} (x)", -DISTANCE_LIMIT, DISTANCE_LIMIT),
            _read_number(value.get("y"), f"{what} (y)", -DISTANCE_LIMIT, DISTANCE_LIMIT))


def _read_flag(value, fallback: bool) -> bool:
    """A yes or no, however an exporter writes it (uvttFields.ts readFlag)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false")
    return fallback


def _read_color(value):
    match = _COLOR.match(value.strip()) if isinstance(value, str) else None
    return f"#{match.group(1).lower()}" if match else None


def _png_size(data: bytes):
    """(width, height) from the image's own IHDR — the image is measured,
    never trusted from the file's claims."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise SceneRefused("The map image in the file is not a PNG image.")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def parse_uvtt(path) -> dict:
    """The .uvtt as the importer reads it (cell-coordinate based)."""
    root = json.loads(Path(path).read_text(encoding="utf-8"))
    resolution = root.get("resolution") or {}
    size_in = resolution.get("map_size") or {}
    doc = {
        "size": (_read_number(size_in.get("x"), "The map's width", 1, MAP_CELLS_LIMIT),
                 _read_number(size_in.get("y"), "The map's height", 1, MAP_CELLS_LIMIT)),
        "polylines": [],
        "portals": [],
        "ambient_light": None,
        "image_size": None,
    }
    origin_in = resolution.get("map_origin")
    doc["origin"] = _read_point(origin_in, "The map's origin") if origin_in is not None else (0, 0)
    for key in ("line_of_sight", "objects_line_of_sight"):
        for line in root.get(key) or []:
            doc["polylines"].append([_read_point(p, f"Point {i + 1} of a wall line")
                                     for i, p in enumerate(line)])
    for index, portal in enumerate(root.get("portals") or []):
        what = f"door {index + 1}"
        if not isinstance(portal, dict):
            raise SceneRefused(f"Door {index + 1} is missing or not an object.")
        bounds = portal.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise SceneRefused(f"Door {index + 1} does not have two ends.")
        doc["portals"].append({
            "bounds": (_read_point(bounds[0], f"The first end of {what}"),
                       _read_point(bounds[1], f"The second end of {what}")),
            # absent reads as closed, exactly as the importer reads it
            "closed": _read_flag(portal.get("closed"), True),
        })
    doc["lights"] = [
        {"position": _read_point(light.get("position"), "The position of a light"),
         "range": _read_number((light or {}).get("range"), "The range of a light",
                               0, DISTANCE_LIMIT)}
        for light in root.get("lights") or [] if isinstance(light, dict)
    ]
    environment = root.get("environment")
    environment = environment if isinstance(environment, dict) else {}
    doc["ambient_light"] = _read_color(environment.get("ambient_light"))
    doc["baked_lighting"] = _read_flag(environment.get("baked_lighting"), False)
    image = root.get("image")
    if not isinstance(image, str) or image == "":
        raise SceneRefused("The file holds no map image.")
    try:
        data = base64.b64decode(_DATA_URL_HEAD.sub("", image))
    except (binascii.Error, ValueError):
        raise SceneRefused("The map image in the file is damaged (it is not valid base64).")
    doc["image_size"] = _png_size(data)
    return doc


# --- uvttToScene.ts (the conversion the contract asserts against) -------------

def convert(doc: dict) -> dict:
    """The conversion dict the scene file is built from: cell size with its
    refusals, walls in world pixels, doors, skipped counts (nothing drops
    silently), the ambient luma, and the whole-map placement refusal."""
    cell_size = checked_cell_size(doc["size"], *doc["image_size"])
    rect = placeable_rect(doc["size"])
    ox, oy = doc["origin"]

    def from_corner(point):
        return (point[0] - ox, point[1] - oy)

    def to_world(cell):
        return {"x": cell[0] * cell_size, "y": cell[1] * cell_size}

    walls, counts = {}, {"walls": 0, "doors": 0, "lights": 0}
    skipped = {"outside": 0, "unlit": 0, "windows": 0}
    on_image = 0
    segments = 0

    def add_wall(from_pt, to_pt, rest) -> bool:
        nonlocal segments
        if from_pt == to_pt:
            return False
        segments += 1
        wall_id = f"wall_uvtt_{segments}"
        walls[wall_id] = {"id": wall_id, "kind": "wall",
                          "p1": to_world(from_pt), "p2": to_world(to_pt), **rest}
        return True

    for line_index, line in enumerate(doc["polylines"]):
        cells = [from_corner(point) for point in line]
        for index in range(1, len(cells)):
            inside = clip_segment(cells[index - 1], cells[index], rect)
            if inside is None:
                skipped["outside"] += 1
                continue
            on_image += 1
            if add_wall(inside[0], inside[1],
                        {"type": "solid", "chainId": f"chain_uvtt_{line_index + 1}"}):
                counts["walls"] += 1
    for portal in doc["portals"]:
        from_pt, to_pt = from_corner(portal["bounds"][0]), from_corner(portal["bounds"][1])
        if not is_within(from_pt, rect) or not is_within(to_pt, rect):
            skipped["outside"] += 1
            continue
        on_image += 1
        if not portal["closed"]:
            skipped["windows"] += 1  # Dungeondraft's windows: Atlas places nothing
        elif add_wall(from_pt, to_pt, {"type": "door", "closed": True}):
            counts["doors"] += 1
    for light in doc["lights"]:
        position = from_corner(light["position"])
        if not is_within(position, rect):
            skipped["outside"] += 1
            continue
        on_image += 1
        if light["range"] == 0:
            skipped["unlit"] += 1  # a light without reach lights nothing
        else:
            counts["lights"] += 1

    if doc["baked_lighting"] or doc["ambient_light"] is None:
        ambient = 1  # a scene whose image carries its own light is shown as it is
    else:
        ambient = _brightness_of(doc["ambient_light"])

    # importUvttFile.ts: nothing of the file on its image means the file's
    # origin is off — the importer's last whole-map refusal
    if on_image == 0 and skipped["outside"] > 0:
        raise SceneRefused("Nothing in the file lies on its map image: its walls, "
                           "doors and lights are all outside it.")
    return {"cell_size": cell_size, "image_size": doc["image_size"], "map_cells": doc["size"],
            "walls": walls, "counts": counts, "skipped": skipped, "on_image": on_image,
            "ambient_luma": ambient}
