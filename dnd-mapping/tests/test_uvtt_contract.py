"""The consumer contract: what Atlas's importer computes from our .uvtt.

Ticket 59: the 2026-10-20 exporter bugs (map_size in pixels, walls
transposed off-canvas) and the 2026-10-23 ones (open doors dropped as
"windows", near-black ambient, open wall rings) all shipped because every
uvtt assertion in the repo was a producer-side invariant — nothing
anywhere imported a generated file the way the consumer does. This test
runs gen -> render -> wrap, then loads the .uvtt through a vendored port
of the companion's Atlas importer (dcg-avtt-integration v0.2.0, itself a
port of Atlas's uvttToScene.ts — see atlas_import_port.py) and asserts
DERIVED semantics: the numbers Atlas computes, not the bytes we wrote.

Zero dependencies — run directly:  python tests/test_uvtt_contract.py
(also pytest-clean: real asserts, no engine or node involved).
"""

import contextlib
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import atlas_import_port as atlas  # noqa: E402
import battlemap  # noqa: E402
import test_battlemap as tb  # noqa: E402  (tiny_png + the ok/FAILED scaffolding)

FAILED = tb.FAILED  # shared with the sibling's ok() reporter
ok = tb.ok

# The ambient the table can actually play under: Atlas reads
# environment.ambient_light's Rec. 709 luma as lighting.ambient — the
# live bug shipped the dungeon's backdrop #120d0b as 0.05 and outside
# torch radius the table saw black. Sane is well off both floor and
# full daylight.
MIN_AMBIENT_LUMA, MAX_AMBIENT_LUMA = 0.2, 0.9

# A cell Atlas derives at 70 px (our nominal grid) — sanity bounds keep
# the derivation honest without pinning the exact art size: below 10 the
# importer itself refuses; far above means image/map-size disagreement.
MIN_CELL_PX, MAX_CELL_PX = 10, 200


@contextlib.contextmanager
def site(name, gen):
    """gen -> render -> rasterize (tiny stdlib PNG) -> wrap, with the site
    dir alive for the importer (the temp tree goes away on exit)."""
    with tempfile.TemporaryDirectory() as tmp:
        battlemap.SITES_DIR = pathlib.Path(tmp)
        battlemap.SCENE_DIR = battlemap.SITES_DIR / name
        battlemap.SCENE_FILE = battlemap.SCENE_DIR / "scene.json"
        gen()
        battlemap.render()
        scene = json.loads(battlemap.SCENE_FILE.read_text(encoding="utf-8"))
        w = int(round(battlemap.u(scene["grids"]["x"] * scene["meters_per_grid"])))
        h = int(round(battlemap.u(scene["grids"]["y"] * scene["meters_per_grid"])))
        battlemap.SCENE_DIR.joinpath("scene.png").write_bytes(tb.tiny_png(w, h))
        battlemap.wrap()
        yield scene, next(battlemap.SCENE_DIR.glob("*.uvtt"))


def assert_consumer_contract(label, scene, uvtt_file):
    """What Atlas computes from the file — the same things, for every
    generator that emits doors."""
    try:
        doc = atlas.parse_uvtt(uvtt_file)
        conv = atlas.convert(doc)
    except atlas.SceneRefused as exc:
        ok(f"{label}: the importer accepts the map", False, f"refused: {exc}")
        return
    ok(f"{label}: the importer accepts the map", True)

    width, height = conv["image_size"]
    cells_x = conv["map_cells"][0]
    ok(f"{label}: cell size derives from imageWidth / map cells",
       abs(conv["cell_size"] - width / cells_x) < 0.01,
       f"cell={conv['cell_size']} image={width} cells={conv['map_cells']}")
    ok(f"{label}: cell size in sane px bounds",
       MIN_CELL_PX < conv["cell_size"] < MAX_CELL_PX,
       f"cell={conv['cell_size']}")

    ok(f"{label}: nothing the importer had to clip or drop",
       conv["skipped"]["outside"] == 0, str(conv["skipped"]))
    inside = all(
        0 <= w["p1"]["x"] <= width and 0 <= w["p1"]["y"] <= height
        and 0 <= w["p2"]["x"] <= width and 0 <= w["p2"]["y"] <= height
        for w in conv["walls"].values())
    ok(f"{label}: every wall segment lies on the image", inside)
    ok(f"{label}: walls were placed", conv["counts"]["walls"] > 0,
       str(conv["counts"]))

    # the importer chains each polyline; an open chain is a ring short one
    # edge — a see/shoot-through gap at one corner of every room wall
    closed = [line for line in doc["polylines"] if line[0] == line[-1]]
    ok(f"{label}: every wall ring is a closed loop",
       len(closed) == len(doc["polylines"]) > 0,
       f"{len(closed)}/{len(doc['polylines'])} rings closed")

    ok(f"{label}: every generated portal is in the file",
       len(doc["portals"]) == len(scene["doors"]) > 0,
       f"{len(doc['portals'])} portals vs {len(scene['doors'])} doors")
    ok(f"{label}: every portal survives as a door (none dropped as a window)",
       conv["counts"]["doors"] == len(scene["doors"])
       and conv["skipped"]["windows"] == 0,
       f"doors={conv['counts']['doors']} windows-skipped={conv['skipped']['windows']}")

    ok(f"{label}: ambient luma within sane bounds",
       MIN_AMBIENT_LUMA <= conv["ambient_luma"] <= MAX_AMBIENT_LUMA,
       f"ambient={conv['ambient_luma']}")


def test_dungeon_survives_the_atlas_importer():
    with site("contract-dungeon",
              lambda: battlemap.gen_dungeon("20261023", "contract-dungeon", 7)) \
            as (scene, uvtt_file):
        assert_consumer_contract("dungeon", scene, uvtt_file)


def test_cave_entrance_survives_the_atlas_importer():
    with site("contract-cave",
              lambda: battlemap.gen_cave("20261023", "contract-cave")) \
            as (scene, uvtt_file):
        assert_consumer_contract("cave", scene, uvtt_file)


def test_the_contract_refuses_both_2026_10_20_bugs():
    """The guard has teeth: doctor a good file with each shipped bug and
    the importer must flag it — map_size in PIXELS is refused 'too small
    for its grid', and transposed wall coordinates fall off the image and
    land in skipped/outside instead of on the map."""
    with site("contract-teeth",
              lambda: battlemap.gen_dungeon("20261020", "contract-teeth", 5)) as (_s, f):
        good = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))

        def write(doctor):
            doctored = json.loads(json.dumps(good))
            doctor(doctored)
            out = pathlib.Path(f).parent / "doctored.uvtt"
            out.write_text(json.dumps(doctored), encoding="utf-8")
            return out

        def to_pixel_dialect(d):
            image_w = d["resolution"]["map_size"]["x"] * battlemap.PIXELS_PER_GRID
            image_h = d["resolution"]["map_size"]["y"] * battlemap.PIXELS_PER_GRID
            d["resolution"]["map_size"] = {"x": image_w, "y": image_h}

        try:
            atlas.convert(atlas.parse_uvtt(write(to_pixel_dialect)))
            ok("pixel-dialect map_size is refused", False, "importer accepted it")
        except atlas.SceneRefused:
            ok("pixel-dialect map_size is refused", True)

        def transpose_walls(d):
            for line in d["line_of_sight"]:
                for p in line:
                    p["x"], p["y"] = p["y"], p["x"]

        conv = atlas.convert(atlas.parse_uvtt(write(transpose_walls)))
        ok("transposed walls land outside the image (flagged, not placed)",
           conv["skipped"]["outside"] > 0 or conv["counts"]["walls"] == 0,
           str(conv["skipped"]))


if __name__ == "__main__":
    test_dungeon_survives_the_atlas_importer()
    test_cave_entrance_survives_the_atlas_importer()
    test_the_contract_refuses_both_2026_10_20_bugs()
    if FAILED:
        print(f"FAILED: {', '.join(FAILED)}")
        sys.exit(1)
    print("all uvtt consumer-contract tests passed")
