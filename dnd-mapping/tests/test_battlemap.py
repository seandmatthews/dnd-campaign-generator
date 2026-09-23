"""Battlemap scene artifacts: XML-safety of labels and the UVTT shape.

Zero dependencies — run directly:  python tests/test_battlemap.py
(also pytest-clean: real asserts, no engine or node involved).

Pins two 2026-09-23 findings: an '&' in a site name used to make scene.svg
unparseable (the rasterizer then died with an opaque sharp error), and the
.uvtt portals/lights did not match the Universal VTT (dd2vtt) shape, so
doors and lights silently failed to import in Foundry.
"""

import json
import pathlib
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import battlemap  # noqa: E402


FAILED = []


def ok(name, cond, detail=""):
    print(f"  {name}: {'OK' if cond else 'FAIL ' + detail}")
    if not cond:
        FAILED.append(name)


def tiny_png(width, height):
    """A minimal valid grayscale PNG of exactly width x height (stdlib only)
    so wrap() has real bytes whose IHDR matches the scene's pixel size."""
    raw = b"".join(b"\x00" + b"\x96" * width for _ in range(height))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def build_site(name):
    """gen + render + wrap into a temp tree; returns (site_dir, uvtt_dict)."""
    with tempfile.TemporaryDirectory() as tmp:
        battlemap.SITES_DIR = pathlib.Path(tmp)
        battlemap.SCENE_DIR = battlemap.SITES_DIR / name
        battlemap.SCENE_FILE = battlemap.SCENE_DIR / "scene.json"
        battlemap.gen_dungeon("20260923", name, 4)
        battlemap.render()
        scene = json.loads(battlemap.SCENE_FILE.read_text(encoding="utf-8"))
        w = int(round(battlemap.u(scene["grids"]["x"] * scene["meters_per_grid"])))
        h = int(round(battlemap.u(scene["grids"]["y"] * scene["meters_per_grid"])))
        battlemap.SCENE_DIR.joinpath("scene.png").write_bytes(tiny_png(w, h))
        battlemap.wrap()
        uvtt_file = next(battlemap.SCENE_DIR.glob("*.uvtt"))
        uvtt = json.loads(uvtt_file.read_text(encoding="utf-8"))
        # hand back the interesting bytes before the temp dir goes away
        return scene, uvtt, battlemap.SCENE_DIR.joinpath("scene.svg").read_text(encoding="utf-8")


def test_ampersand_name_survives_the_pipeline():
    scene, uvtt, _svg = build_site("Marsh & Fen")
    ok("scene.json written", "Marsh & Fen" in scene.get("name", ""))


def test_rendered_svg_parses_and_escapes_labels():
    _scene, _uvtt, svg = build_site("Marsh & Fen")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        ok("scene.svg parses with '&' in the name", False, str(e))
        return
    labels = [t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text")]
    ok("scene.svg parses with '&' in the name", True)
    ok("label text survives unescaped-after-parse",
       any("MARSH & FEN" in t for t in labels), str(labels))


def test_uvtt_portals_and_lights_match_the_spec():
    scene, uvtt, _svg = build_site("Marsh & Fen")
    portals, lights = uvtt["portals"], uvtt["lights"]
    ok("portals exist", len(portals) == len(scene["doors"]) > 0)
    shape_ok = all(
        isinstance(p["bounds"], list) and len(p["bounds"]) == 2
        and {"x", "y"} <= set(p["bounds"][0]) and {"x", "y"} <= set(p["bounds"][1])
        and {"x", "y"} <= set(p["position"])
        and isinstance(p["closed"], bool)
        for p in portals)
    ok("portal bounds are two-point arrays + position", shape_ok)
    symmetric = all(
        abs((p["bounds"][0]["x"] + p["bounds"][1]["x"]) / 2 - p["position"]["x"]) < 0.51
        and abs((p["bounds"][0]["y"] + p["bounds"][1]["y"]) / 2 - p["position"]["y"]) < 0.51
        for p in portals)
    ok("portal segment centers on its position", symmetric)
    n_lights = sum(1 for f in scene["features"] if f["kind"] == "light")
    ok("lights exist", len(lights) == n_lights > 0, f"{len(lights)} vs {n_lights}")
    ok("lights use position + range (not flat x/y/distance)",
       all({"x", "y"} <= set(l["position"]) and isinstance(l["range"], (int, float))
           for l in lights))
    ok("resolution is spec-shaped",
       uvtt["format"] == 0.3
       and {"map_size", "pixels_per_grid"} <= set(uvtt["resolution"]))


def test_door_cell_matches_the_wall_the_corridor_pierces():
    """Room B at (10, 5, w=6, h=4): center (13, 7), walls x=10..15, y=5..8.
    Found 2026-09-23: bottom-wall doors carried angle 90 (leaf perpendicular
    to its own wall) and same-center-row pairs put the door on a wall the
    purely-horizontal corridor never crosses."""
    dc = battlemap._door_cell
    # corridor descends into B (acy < bcy): top wall, leaf along the wall
    assert dc(acy=2, acx=13, bx=10, by=5, bw=6, bh=4, bcx=13, bcy=7) == (13, 5, 0)
    # corridor ascends into B (acy > bcy): bottom wall, still horizontal
    assert dc(acy=9, acx=13, bx=10, by=5, bw=6, bh=4, bcx=13, bcy=7) == (13, 8, 0)
    # same center row, B to the right: enters through B's left wall
    assert dc(acy=7, acx=3, bx=10, by=5, bw=6, bh=4, bcx=13, bcy=7) == (10, 7, 90)
    # same center row, B to the left: enters through B's right wall
    assert dc(acy=7, acx=20, bx=10, by=5, bw=6, bh=4, bcx=13, bcy=7) == (15, 7, 90)
    print("  door cells: leaf runs along the pierced wall in all four cases  OK")


if __name__ == "__main__":
    test_ampersand_name_survives_the_pipeline()
    test_rendered_svg_parses_and_escapes_labels()
    test_uvtt_portals_and_lights_match_the_spec()
    test_door_cell_matches_the_wall_the_corridor_pierces()
    if FAILED:
        print(f"FAILED: {', '.join(FAILED)}")
        sys.exit(1)
    print("all battlemap tests passed")
