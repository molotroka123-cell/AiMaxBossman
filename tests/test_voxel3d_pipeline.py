"""Regression guards for tools/voxel3d (spec -> grid -> .vox / .glb / BossBlocks structure).

Pure-stdlib parts are always tested; the OSS reader cross-checks (trimesh, pygltflib, py-vox-io)
and the Pillow preview run only when those packages are installed.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from voxel3d import bossblocks, mesh, vox  # noqa: E402
from voxel3d.generate import EXAMPLE_SPEC, extract_json, generate  # noqa: E402
from voxel3d.spec import build, check_grid, components, repair_spec, validate  # noqa: E402

TABLE = EXAMPLE_SPEC


def _grid(spec):
    assert validate(spec) == []
    grid, fixes, errors = build(spec)
    assert errors == []
    return grid, fixes


def test_example_spec_builds_connected_grounded_object():
    grid, fixes = _grid(TABLE)
    info = check_grid(grid)
    assert fixes == []
    assert info["voxels"] == 24 + 4 * 3
    assert info["connected"] and info["grounded"] and info["within_bounds"]


def test_every_op_type_rasterizes_inside_bounds():
    spec = {"name": "all_ops", "size": [12, 12, 12], "palette": {"a": "#ff0000", "b": "#00ff00"},
            "ops": [{"op": "box", "from": [0, 0, 0], "to": [11, 0, 11], "mat": "a"},
                    {"op": "box", "from": [2, 1, 2], "to": [6, 5, 6], "mat": "b", "hollow": True},
                    {"op": "sphere", "center": [9, 3, 9], "radius": [2, 2, 1], "mat": "a"},
                    {"op": "cylinder", "base": [3, 1, 9], "radius": 1, "height": 4, "mat": "b"},
                    {"op": "cone", "base": [9, 1, 3], "radius": 2, "height": 5, "mat": "a"},
                    {"op": "line", "from": [0, 1, 0], "to": [0, 8, 0], "mat": "b"},
                    {"op": "voxels", "at": [[11, 1, 11]], "mat": "b"},
                    {"op": "layer", "y": 6, "rows": ["", "..bb"], "key": {"b": "b"}},
                    {"op": "box", "from": [4, 3, 4], "to": [4, 3, 4], "mat": "air"},
                    {"op": "mirror", "axis": "x"}]}
    grid, _ = _grid(spec)
    assert all(grid.inside(c) for c in grid.cells)
    assert (0, 8, 0) in grid.cells and (11, 8, 0) in grid.cells  # line + mirror
    assert (4, 3, 4) not in grid.cells  # hollow box interior / air carve


def test_validator_reports_model_errors_in_plain_words():
    bad = {"name": "x", "size": [40, 5, 5], "palette": {"Wood!": "brown"},
           "ops": [{"op": "blob"}, {"op": "box", "from": [0, 0], "to": [1, 1, 1], "mat": "stone"}]}
    errs = "\n".join(validate(bad))
    assert "size" in errs and "unknown op 'blob'" in errs and "'from' must be [x, y, z]" in errs
    assert "not in the palette" in errs and "#rrggbb" in errs


def test_disconnected_parts_are_an_error_but_tiny_floaters_are_repaired():
    split = {"name": "s", "size": [10, 4, 4], "palette": {"a": "#777777"},
             "ops": [{"op": "box", "from": [0, 0, 0], "to": [2, 2, 2], "mat": "a"},
                     {"op": "box", "from": [6, 0, 0], "to": [8, 2, 2], "mat": "a"}]}
    assert any("disconnected" in e for e in validate(split))
    split["allow_floating"] = True
    assert validate(split) == []
    speck = {"name": "t", "size": [10, 10, 10], "palette": {"a": "#777777"},
             "ops": [{"op": "box", "from": [0, 0, 0], "to": [5, 5, 5], "mat": "a"},
                     {"op": "voxels", "at": [[9, 9, 9]], "mat": "a"}]}
    grid, fixes, errors = build(speck)
    assert errors == [] and len(components(grid.cells)) == 1
    assert any("floating" in f for f in fixes)


def test_build_grounds_and_clips_with_logged_repairs():
    spec = {"name": "g", "size": [4, 8, 4], "palette": {"a": "#777777"},
            "ops": [{"op": "box", "from": [1, 3, 1], "to": [2, 9, 2], "mat": "a"}]}
    grid, fixes, errors = build(spec)
    assert errors == [] and check_grid(grid)["grounded"]
    assert grid.size == (4, 10, 4)  # grown to fit y=9 (<= 32), then grounded
    assert any("grid fitted" in f for f in fixes) and any("stands on y=0" in f for f in fixes)


def test_repair_spec_fixes_common_model_slips():
    spec = {"name": "r", "size": [50, 4, 4], "palette": [{"name": "Oak Wood", "color": "a52"}],
            "ops": [{"type": "box", "from": [0, 0, 0], "to": [1, 1, 1], "material": "oak_wod"}]}
    fixed, fixes = repair_spec(spec)
    assert fixed["size"] == [32, 4, 4] and fixed["palette"] == {"oak_wood": "#aa5522"}
    assert fixed["ops"][0]["op"] == "box" and fixed["ops"][0]["mat"] == "oak_wood"
    assert validate(fixed) == [] and len(fixes) >= 3


def test_vox_file_layout_and_roundtrip(tmp_path):
    grid, _ = _grid(TABLE)
    p = vox.write(grid, tmp_path / "t.vox")
    raw = p.read_bytes()
    assert raw[:4] == b"VOX " and struct.unpack_from("<i", raw, 4)[0] == 150
    d = vox.read_basic(p)
    X, Y, Z = grid.size
    assert d["size"] == (X, Z, Y)  # MagicaVoxel is Z-up
    assert {(x, y, z): c for x, y, z, c in d["voxels"]} == {(x, Z - 1 - z, y): k for (x, y, z), k in grid.cells.items()}
    assert d["rgba"][0][:3] == (0x9c, 0x6b, 0x3c)


def test_greedy_mesh_area_equals_exposed_faces_and_merges_quads():
    grid, _ = _grid(TABLE)
    quads = mesh.greedy_quads(grid)
    assert abs(sum(mesh.quad_area(q) for q in quads) - grid.exposed_faces()) < 1e-9
    assert len(quads) < grid.exposed_faces() / 2  # greedy merging actually happened
    cube = {"name": "c", "size": [8, 8, 8], "palette": {"a": "#777777"},
            "ops": [{"op": "box", "from": [0, 0, 0], "to": [7, 7, 7], "mat": "a"}]}
    assert len(mesh.greedy_quads(_grid(cube)[0])) == 6


def test_glb_container_is_well_formed(tmp_path):
    grid, _ = _grid(TABLE)
    stats = mesh.write_glb(grid, tmp_path / "t.glb", "table")
    raw = (tmp_path / "t.glb").read_bytes()
    magic, version, length = struct.unpack_from("<4sII", raw)
    assert (magic, version, length) == (b"glTF", 2, len(raw))
    jlen, jtype = struct.unpack_from("<I4s", raw, 12)
    doc = json.loads(raw[20:20 + jlen])
    assert jtype == b"JSON" and len(doc["meshes"][0]["primitives"]) == 2 == stats["primitives"]
    assert stats["triangles"] * 3 == sum(doc["accessors"][p["indices"]]["count"] for p in doc["meshes"][0]["primitives"])


def test_glb_and_vox_pass_independent_oss_readers(tmp_path):
    pytest.importorskip("trimesh")
    pytest.importorskip("pygltflib")
    from voxel3d.verify import check_glb, check_vox

    grid, _ = _grid(TABLE)
    mesh.write_glb(grid, tmp_path / "t.glb")
    vox.write(grid, tmp_path / "t.vox")
    g = check_glb(grid, tmp_path / "t.glb")
    assert g["ok"], g
    assert g["bounds"] == [[-3.0, 0.0, -2.0], [3.0, 4.0, 2.0]]
    assert check_vox(grid, tmp_path / "t.vox")["ok"]


def test_bossblocks_structure_uses_the_game_save_schema():
    grid, _ = _grid(TABLE)
    st = bossblocks.structure(grid, "table", "a small wooden table")
    assert st["format"] == "bossblocks.structure" and st["fits_world_at_origin"]
    assert all(set(b) == {"x", "y", "z", "kind", "color"} and b["kind"] in (1, 2, 3) for b in st["blocks"])
    assert min(b["x"] for b in st["blocks"]) == -3 and min(b["y"] for b in st["blocks"]) == 0
    assert bossblocks.nearest_kind("#9c6b3c") == 2  # brown wood -> Sand, not Grass (CIELAB)
    assert bossblocks.nearest_kind("#3f8f3a") == 1 and bossblocks.nearest_kind("#707070") == 3


def test_install_requires_a_godot_project(tmp_path):
    grid, _ = _grid(TABLE)
    glb, js = tmp_path / "t.glb", tmp_path / "t.json"
    mesh.write_glb(grid, glb)
    bossblocks.write_structure(grid, js, "table")
    with pytest.raises(FileNotFoundError):
        bossblocks.install(tmp_path / "nogame", "table", glb, js)
    game = tmp_path / "game"
    game.mkdir()
    (game / "project.godot").write_text("config_version=5\n")
    written = bossblocks.install(game, "table", glb, js)
    assert (game / "assets/voxel/table.glb").is_file() and (game / "structures/table.json").is_file()
    assert (game / "scripts/voxel_structures.gd").is_file() and len(written) == 6


def test_preview_png_is_rendered(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    from voxel3d.render import render_preview

    grid, _ = _grid(TABLE)
    p = render_preview(grid, tmp_path / "p.png", size=128)
    img = Image.open(p)
    assert img.size == (256, 128)
    assert len(img.getcolors(1 << 16)) > 4  # not a blank canvas


def test_generate_loop_feeds_validator_errors_back_to_the_model():
    replies = iter(['not json', json.dumps({"name": "t", "size": [4, 4, 4], "palette": {"a": "#777"},
                                            "ops": [{"op": "box", "from": [0, 0, 0], "to": [1, 1], "mat": "a"}]}),
                    json.dumps(TABLE)])
    seen = []

    def chat(messages):
        seen.append(messages[-1]["content"])
        return next(replies)

    spec, errors, fixes, transcript = generate("a table", chat, tries=3, log=lambda m: None)
    assert errors == [] and spec["name"] == "wooden_table"
    assert "not a single JSON object" in seen[1] and "'to' must be [x, y, z]" in seen[2]


def test_extract_json_tolerates_fences_and_think_blocks():
    assert extract_json('<think>hm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json("no json here") is None


def test_roof_op_gable_and_pyramid_shapes():
    base = {"name": "r", "size": [7, 6, 5], "palette": {"a": "#aa3333"}}
    gable = dict(base, ops=[{"op": "roof", "from": [0, 0, 0], "to": [6, 0, 4], "style": "gable", "axis": "x", "mat": "a"}])
    grid, _ = _grid(gable)
    assert sorted({c[1] for c in grid.cells}) == [0, 1, 2]  # z 0..4 shrinks by 1 per layer
    assert {c[2] for c in grid.cells if c[1] == 2} == {2} and {c[0] for c in grid.cells if c[1] == 2} == set(range(7))
    pyramid = dict(base, ops=[{"op": "roof", "from": [0, 0, 0], "to": [6, 0, 4], "style": "pyramid", "mat": "a"}])
    grid, _ = _grid(pyramid)
    assert {(c[0], c[2]) for c in grid.cells if c[1] == 2} == {(2, 2), (3, 2), (4, 2)}
    assert any("'style'" in e for e in validate(dict(base, ops=[{"op": "roof", "from": [0, 0, 0], "to": [1, 0, 1],
                                                                  "style": "dome", "mat": "a"}])))


def test_grid_is_fitted_when_shapes_reach_past_the_declared_size():
    # the real run-1 house failure: a roof above the declared height used to be "entirely outside"
    spec = {"name": "h", "size": [5, 4, 5], "palette": {"w": "#888888", "r": "#cc3333"},
            "ops": [{"op": "box", "from": [0, 0, 0], "to": [4, 3, 4], "mat": "w", "hollow": True},
                    {"op": "roof", "from": [0, 4, 0], "to": [4, 4, 4], "style": "pyramid", "mat": "r"},
                    {"op": "box", "from": [-2, 0, 2], "to": [-1, 0, 2], "mat": "w"}]}
    assert validate(spec) == []
    grid, fixes, errors = build(spec)
    assert errors == [] and grid.size == (7, 7, 5)
    assert any("grid fitted" in f and "shifted by [2, 0, 0]" in f for f in fixes)
    too_big = {"name": "b", "size": [4, 4, 4], "palette": {"w": "#888888"},
               "ops": [{"op": "box", "from": [0, 0, 0], "to": [40, 0, 0], "mat": "w"}]}
    grid, fixes, errors = build(too_big)
    assert grid.size[0] == 4 and any("clipped" in f for f in fixes)  # > 32: clip, never grow past MAX_DIM


def test_long_voxel_lists_are_rejected_with_guidance():
    spec = {"name": "v", "size": [30, 1, 1], "palette": {"a": "#777777"},
            "ops": [{"op": "voxels", "at": [[x, 0, 0] for x in range(30)], "mat": "a"}]}
    assert any("at most 24 per voxels op" in e for e in validate(spec))


def test_lint_flags_solid_blocks_and_generate_gives_one_improvement_turn():
    from voxel3d.spec import lint

    solid = {"name": "bridge", "size": [11, 4, 5], "palette": {"w": "#9c6b3c"},
             "ops": [{"op": "box", "from": [0, 0, 0], "to": [10, 3, 4], "mat": "w"}]}
    assert lint(build(solid)[0]) and not lint(build(TABLE)[0])
    open_bridge = dict(solid, ops=[{"op": "box", "from": [0, 0, 0], "to": [10, 0, 4], "mat": "w"},
                                   {"op": "box", "from": [0, 1, 0], "to": [10, 2, 0], "mat": "w"},
                                   {"op": "box", "from": [0, 1, 4], "to": [10, 2, 4], "mat": "w"}])
    replies = iter([json.dumps(solid), json.dumps(open_bridge)])
    prompts = []

    def chat(messages):
        prompts.append(messages[-1]["content"])
        return next(replies)

    spec, errors, _, _ = generate("a bridge", chat, tries=3, log=lambda m: None)
    assert errors == [] and spec == open_bridge and "solid block" in prompts[1]
    # an invalid improvement keeps the first valid draft
    replies = iter([json.dumps(solid), "garbage"])
    spec, errors, _, _ = generate("a bridge", chat, tries=3, log=lambda m: None)
    assert errors == [] and spec["ops"] == solid["ops"]


def test_cut_off_replies_get_specific_feedback():
    from voxel3d.generate import CUT_OFF

    replies = iter(['{"name": "house", "ops": [' + '{"op": "voxels"}, ' * 200, json.dumps(TABLE)])
    seen = []

    def chat(messages):
        seen.append(messages[-1]["content"])
        return next(replies)

    spec, errors, _, _ = generate("a house", chat, tries=2, log=lambda m: None)
    assert errors == [] and CUT_OFF in seen[1]


def test_generate_survives_all_calls_failing():
    def chat(messages):
        raise TimeoutError("busy")

    spec, errors, _, _ = generate("x", chat, tries=2, log=lambda m: None)
    assert spec is None and "model call failed" in errors[0]


def test_mesh_voxelization_keeps_colours_and_height(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    pytest.importorskip("scipy")
    import numpy as np

    from voxel3d.from_mesh import voxelize

    box = trimesh.creation.box(extents=[1.0, 2.0, 1.0])
    box.visual.vertex_colors = np.tile([200, 30, 30, 255], (len(box.vertices), 1))
    path = tmp_path / "box.obj"
    box.export(path)
    grid, notes = voxelize(path, height=8, colors=4)
    info = check_grid(grid)
    assert info["connected"] and info["grounded"] and 8 <= grid.size[1] <= 9
    assert len(grid.palette) == 1 and grid.palette[0][1] in ("#c81e1e", "#c81f1f")
