"""world/map.svg from an EXISTING world/source.* — read-only, no regen.

Why this module exists (design §M1 build item 2): FMG has no headless
LAYER renderer. The headless pipeline (docs/map-pipeline.md Tier 1) runs
generation only and dumps JSON — the rendered map is drawn by the browser
app, and a browser re-render of the same seed is NOT the same world
(options randomization paths differ). So the only divergence-free way to
show a hero for a campaign assembled without an svg is to render from the
dump itself. Two render-only paths, both pure reads:

  - a `.map` save carries FMG's own rendered SVG embedded in the file —
    slice it out verbatim (mode "fmg-embedded": the real FMG art).
  - a JSON dump (headless or full export) carries the whole pack: cells
    polygons, features, states (with FMG's own colors + poles), burgs —
    draw a schematic political atlas from it (mode "schematic-render").

Neither path touches geography.yaml; the route only adds a picture beside
the existing source. Every drawn shape is a derived artifact of the dump.
"""

import json
import math
import re
from pathlib import Path

import spine

# parchment-atlas palette (schematic mode)
OCEAN = "#9fc3d4"
LAKE = "#8ab4c9"
LAND = "#dcd6c0"
INK = "#3a3128"
BURG_FILL = "#2f2a24"
PORT_STROKE = "#1d5a8a"

# clause/sentence punctuation that ends a title cut — see propose_title
# (kept here too because it is a rendering concern of the same "derived
# from the dump" family; the canonical copy lives in routers/simulation.py)

_MIN_STATE_CELLS_FOR_LABEL = 20


def _xt(text):
    """XML-escape text bound for the SVG (& first) — nation names come from
    the FMG dump verbatim, and 'Sea & Sky' must not make map.svg unparseable
    (same contract as dnd-mapping battlemap.py's xt())."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def extract_embedded_svg(text: str):
    """The verbatim FMG art embedded in a .map save (raw markup between
    <svg and </svg>), or None. Same content-signature spirit as
    import_fmg.py: find the tags, not a line number."""
    start = text.find("<svg")
    if start < 0:
        return None
    end = text.find("</svg>", start)
    if end < 0:
        return None
    return text[start:end + len("</svg>")]


def _cell_path(points) -> str:
    it = iter(points)
    x, y = next(it)
    d = [f"M{x:.1f} {y:.1f}"]
    for x, y in it:
        d.append(f"L{x:.1f} {y:.1f}")
    d.append("Z")
    return "".join(d)


def _state_color(state) -> str:
    # fullmatch, not startswith: the color is interpolated into a fill
    # attribute, so a crafted/corrupt dump value like '#"><svg' would
    # otherwise corrupt (or inject into) the generated map.svg
    c = state.get("color") if isinstance(state, dict) else None
    if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{3,8}", c):
        return c
    return LAND


def render_dump_svg(data: dict, out: Path) -> dict:
    """Schematic political atlas from a JSON dump's own polygons.

    One <path> per fill color (thousands of cell polygons collapse into a
    handful of elements), burg dots on top, nation labels at FMG's own
    poles. Returns counts for the job result.
    """
    pack = data.get("pack") or {}
    verts = ((pack.get("vertices") or {}).get("p")) or []
    cells = pack.get("cells")
    if not verts or not isinstance(cells, dict) or "v" not in cells:
        raise ValueError(
            "unsupported dump: pack has no vertices.p / columnar cells — "
            "only FMG JSON exports and headless dumps carry drawable cells")
    v = cells.get("v")
    f = cells.get("f")
    s = cells.get("state")
    n_cells = len(v)
    if not (len(f) == len(s) == n_cells):
        raise ValueError(
            f"unsupported dump: cells.f/state length mismatch ({len(f)}/"
            f"{len(s)} vs {n_cells})")

    raw_features = pack.get("features") or []
    raw_states = pack.get("states") or []

    def feature_type(idx):
        if 0 <= idx < len(raw_features) and isinstance(raw_features[idx], dict):
            return raw_features[idx].get("type", "land")
        return "land"

    def state_color(idx):
        if 0 <= idx < len(raw_states) and isinstance(raw_states[idx], dict):
            return _state_color(raw_states[idx])
        return LAND

    groups: dict = {}
    for ci in range(n_cells):
        ftype = feature_type(f[ci])
        if ftype == "ocean":
            fill = OCEAN
        elif ftype == "lake":
            fill = LAKE
        else:
            st = s[ci]
            fill = state_color(st) if st else LAND
        pts = [verts[vi] for vi in v[ci] if 0 <= vi < len(verts)]
        if len(pts) < 3:
            continue
        groups.setdefault(fill, []).append(_cell_path(pts))

    width = int(((data.get("info") or {}).get("width"))
                or ((data.get("settings") or {}).get("graph") or {}).get("width")
                or 1280)
    height = int(((data.get("info") or {}).get("height"))
                 or ((data.get("settings") or {}).get("graph") or {}).get("height")
                 or 720)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{OCEAN}"/>',
    ]
    for fill in (OCEAN, LAKE):  # water first, land and politics over it
        if groups.get(fill):
            parts.append(f'<path d="{"".join(groups[fill])}" fill="{fill}"/>')
    for fill, paths in sorted(groups.items()):
        if fill in (OCEAN, LAKE):
            continue
        parts.append(f'<path d="{"".join(paths)}" fill="{fill}"/>')

    # burgs (raw list keeps FMG's index-0 placeholder; filter dicts)
    burgs = [b for b in (pack.get("burgs") or []) if isinstance(b, dict)]
    n_burgs = 0
    for b in burgs:
        if b.get("x") is None or b.get("y") is None:
            continue
        n_burgs += 1
        r = 3.2 if b.get("capital") else 2.1
        stroke = f' stroke="{PORT_STROKE}" stroke-width="1"' if b.get("port") else ""
        parts.append(
            f'<circle cx="{b["x"]:.1f}" cy="{b["y"]:.1f}" r="{r}" '
            f'fill="{BURG_FILL}"{stroke}/>'
        )

    # nation labels at FMG's own poles, sized by how much map they hold.
    # cells-per-state is counted from the columnar array: FMG dumps carry
    # states[].cells as either a list or just a count, never predictably.
    n_labels = 0
    state_cell_counts: dict = {}
    for st_id in s:
        state_cell_counts[st_id] = state_cell_counts.get(st_id, 0) + 1
    for st in raw_states:
        if not isinstance(st, dict) or not st.get("name"):
            continue
        n_st_cells = state_cell_counts.get(st.get("i"), 0)
        pole = st.get("pole")
        # a malformed pole (wrong shape, non-numbers) must not raise inside
        # the render — skip the label, keep the map
        if n_st_cells < _MIN_STATE_CELLS_FOR_LABEL                 or not isinstance(pole, (list, tuple)) or len(pole) < 2                 or not all(isinstance(v, (int, float)) for v in pole[:2]):
            continue
        n_labels += 1
        size = 9 + min(7.0, math.sqrt(n_st_cells) / 4.0)
        parts.append(
            f'<text x="{pole[0]:.1f}" y="{pole[1]:.1f}" '
            f'font-family="Georgia, serif" font-size="{size:.1f}" '
            f'fill="{INK}" fill-opacity="0.75" text-anchor="middle" '
            f'letter-spacing="2">{_xt(st["name"].upper())}</text>'
        )

    parts.append("</svg>")
    # atomic like every canon write (ticket 64): a crash mid-write must not
    # leave a truncated map.svg for the next snapshot to commit under an
    # unrelated message (the parent dir is made by the atomic write)
    spine.write_text_atomic(out, "".join(parts))
    return {"burgs": n_burgs, "states": n_labels, "cells": n_cells}


def render_world_map(source: Path, out: Path) -> dict:
    """Dispatch on the source's format. Returns {"mode", ...}; raises
    ValueError with an honest reason when a dump cannot be drawn."""
    text = source.read_text(encoding="utf-8")
    if not text.lstrip().startswith("{"):
        svg = extract_embedded_svg(text)
        if svg is None:
            raise ValueError(
                "no embedded SVG found in this .map save — cannot render "
                "without regenerating (refusing: that would risk divergence)")
        # atomic like every canon write (ticket 64); the parent dir is
        # made by the atomic write
        spine.write_text_atomic(out, svg)
        return {"mode": "fmg-embedded", "bytes": len(svg)}
    info = render_dump_svg(json.loads(text), out)
    return {"mode": "schematic-render", **info}
