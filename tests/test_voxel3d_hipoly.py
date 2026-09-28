"""Regression guards for the high-poly (5k-10k triangle) prop pipeline in tools/voxel3d.

Needs numpy, trimesh, fast-simplification, pygltflib, scipy and Pillow; skipped otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

np = pytest.importorskip("numpy")
trimesh = pytest.importorskip("trimesh")
pytest.importorskip("fast_simplification")
pytest.importorskip("pygltflib")
pytest.importorskip("scipy")

from voxel3d import hipoly, hpspec, mesh_check, meshgen  # noqa: E402

EXAMPLES = meshgen.example_specs()


def _build(spec, tmp_path):
    spec, _ = hpspec.repair(spec)
    glb, stats, errors = hpspec.build(spec)
    assert errors == [], errors
    path = tmp_path / f"{spec['name']}.glb"
    path.write_bytes(glb)
    return path, stats, mesh_check.check_prop_glb(path, expect_height=float(spec["height"]))


def test_five_example_models_exist():
    assert set(EXAMPLES) == {"hp_tree", "hp_crystal_golem", "hp_treasure_chest", "hp_lantern", "hp_mushroom_house"}
    for spec in EXAMPLES.values():
        assert hpspec.validate(hpspec.repair(spec)[0]) == []


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_example_builds_within_budget_and_passes_validator(name, tmp_path):
    path, stats, check = _build(EXAMPLES[name], tmp_path)
    assert 5000 <= check["triangles"] <= 10000
    assert stats["dense_triangles"] >= check["triangles"]  # it really was decimated / never inflated
    assert check["ok"], {k: v for k, v in check.items() if k != "bounds"}
    assert check["inside_out_shells"] == 0 and check["nonmanifold_edges"] == 0
    assert check["pivot_ok"] and abs(check["height_m"] - EXAMPLES[name]["height"]) < 1e-2


def test_build_is_deterministic(tmp_path):
    a = hpspec.build(hpspec.repair(EXAMPLES["hp_lantern"])[0])[0]
    b = hpspec.build(hpspec.repair(EXAMPLES["hp_lantern"])[0])[0]
    assert a == b


def test_mirrored_parts_face_outward(tmp_path):
    spec = {"name": "pair", "height": 1.0, "parts": [
        {"name": "base", "shape": "box", "center": [0, 0.1, 0], "size": [1.2, 0.2, 0.4], "color": "#8b5a2b"},
        {"name": "post", "shape": "box", "center": [0.4, 0.6, 0], "size": [0.2, 0.9, 0.2], "mirror_x": True, "color": "#e8b53a"}]}
    model = hpspec.to_model(hpspec.repair(spec)[0])
    posts = [p for p in model.parts if p.name.startswith("post")]
    assert len(posts) == 2 and all(p.mesh.volume > 0 for p in posts)
    assert posts[1].mesh.bounds[1][0] < 0  # the copy really is on the -x side


def test_floating_part_is_reported_in_plain_words():
    spec = {"name": "bad", "height": 1.0, "parts": [
        {"name": "body", "shape": "ellipsoid", "center": [0, 0.5, 0], "radii": 0.5, "color": "#c0392b"},
        {"name": "moon", "shape": "ellipsoid", "center": [3, 3, 0], "radii": 0.2, "color": "#eeeeee"}]}
    _, _, errors = hpspec.build(hpspec.repair(spec)[0])
    assert any("moon floats" in e for e in errors)


def test_repair_fixes_common_model_slips():
    spec = {"name": "My Cone!", "height": 1.0, "parts": [
        {"type": "cone", "base": [0, 0, 0], "radius": 0.4, "height": 1.0, "color": "red", "material": "shiny"},
        {"shape": "lathe", "profile": [[0.3, 0], [0.2, 0.5]], "color": "#abc"}]}
    fixed, fixes = hpspec.repair(spec)
    assert fixed["name"] == "my_cone"
    assert fixed["parts"][0]["shape"] == "lathe" and fixed["parts"][0]["color"] == "#c0392b"
    assert fixed["parts"][0]["material"] == "matte"
    prof = fixed["parts"][1]["profile"]
    assert prof[0][0] == 0 and prof[-1][0] == 0  # closed to the axis -> watertight lathe
    assert fixed["parts"][1]["color"] == "#aabbcc"
    assert hpspec.validate(fixed) == [] and fixes


def test_validator_errors_are_actionable():
    errs = hpspec.validate({"height": 20, "parts": [{"name": "x", "shape": "blob"}, {"name": "y", "shape": "tube"}]})
    joined = " | ".join(errs)
    assert "height must be" in joined and "unknown shape 'blob'" in joined and "needs 'from'" in joined


def test_mesh_check_rejects_budget_and_inside_out(tmp_path):
    sphere = trimesh.creation.icosphere(subdivisions=3)  # 1280 triangles: under budget
    sphere.apply_translation([0, 1, 0])
    prims = [{"material": "matte", "pos": sphere.vertices, "nrm": sphere.vertex_normals,
              "col": np.tile([0.8, 0.2, 0.2], (len(sphere.vertices), 1)), "idx": sphere.faces}]
    p = tmp_path / "small.glb"
    p.write_bytes(hipoly.encode_glb(prims, "small"))
    c = mesh_check.check_prop_glb(p)
    assert not c["budget_ok"] and not c["ok"]
    flipped = [dict(prims[0], idx=sphere.faces[:, ::-1], nrm=-sphere.vertex_normals)]
    p2 = tmp_path / "flipped.glb"
    p2.write_bytes(hipoly.encode_glb(flipped, "flipped"))
    assert mesh_check.check_prop_glb(p2, tri_min=100)["inside_out_shells"] == 1


def test_image_to_3d_route_decimates_and_keeps_colours(tmp_path):
    """Stand-in for a TripoSR mesh: dense vertex-coloured sphere + a tiny floater, z-up."""
    body = trimesh.creation.icosphere(subdivisions=6)  # 81,920 faces
    red_top = np.where(body.vertices[:, 2:3] > 0, [[220, 40, 40, 255]], [[240, 230, 200, 255]]).astype(np.uint8)
    body.visual.vertex_colors = red_top
    speck = trimesh.creation.icosphere(subdivisions=1, radius=0.02)
    speck.apply_translation([2, 0, 0])
    speck.visual.vertex_colors = np.tile([0, 0, 0, 255], (len(speck.vertices), 1)).astype(np.uint8)
    src = tmp_path / "tsr.glb"
    trimesh.util.concatenate([body, speck]).export(src)
    glb, stats = hipoly.from_mesh_file(src, "blob", target=8000, height=1.5, up="z")
    out = tmp_path / "blob.glb"
    out.write_bytes(glb)
    c = mesh_check.check_prop_glb(out, expect_height=1.5)
    assert stats["dense_triangles"] > 80000 and stats["dropped_floaters"] == 1
    assert c["ok"] and 7600 <= c["triangles"] <= 8400
    prim = mesh_check.read_glb(out)[0]
    top = prim["pos"][:, 1] > 1.2  # z-up source: the red half is on top after the Y-up turn
    assert prim["col_srgb"][top][:, 0].mean() > 0.75 and prim["col_srgb"][top][:, 1].mean() < 0.35


def test_install_writes_props_and_manifest(tmp_path):
    from voxel3d import make_props

    game = tmp_path / "game"
    game.mkdir()
    (game / "project.godot").write_text("config_version=5\n")
    out = tmp_path / "out"
    out.mkdir()
    rep = make_props.make_one(dict(EXAMPLES["hp_lantern"]), out)
    plan = make_props.install(game, [rep], apply=False)
    assert not (game / "assets").exists() and plan[-1].endswith("props.json")
    make_props.install(game, [rep], apply=True)
    man = json.loads((game / "assets" / "props" / "props.json").read_text(encoding="utf-8"))
    assert man["props"]["hp_lantern"]["triangles"] == rep["check"]["triangles"]
    assert (game / "assets" / "props" / "hp_lantern.glb").is_file()
