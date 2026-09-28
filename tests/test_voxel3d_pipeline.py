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
    assert any("clipped" in f for f in fixes) and any("stands on y=0" in f for f in fixes)


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
