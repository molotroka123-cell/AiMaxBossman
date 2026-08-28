"""STL parsing and deterministic export, including corrupt-input handling."""

from __future__ import annotations

import hashlib
import struct

import pytest

from ai_3d_maker.cad import primitives
from ai_3d_maker.errors import MeshLoadError
from ai_3d_maker.mesh import (
    STL_HEADER_TAG,
    Mesh,
    load_stl,
    mesh_digest,
    sha256_file,
    stl_bytes,
    write_ascii_stl,
    write_stl,
)
from conftest import write_garbage_stl, write_nan_stl, write_truncated_stl


# ------------------------------------------------------------- round tripping
def test_binary_round_trip_preserves_geometry(tmp_path, cube_mesh):
    path = write_stl(cube_mesh, tmp_path / "cube.stl")
    again = load_stl(path)
    assert len(again.faces) == len(cube_mesh.faces)
    assert again.extents() == pytest.approx(cube_mesh.extents())
    assert again.volume() == pytest.approx(cube_mesh.volume(), rel=1e-5)


def test_ascii_round_trip_preserves_geometry(tmp_path, cube_mesh):
    path = write_ascii_stl(cube_mesh, tmp_path / "cube_ascii.stl")
    again = load_stl(path)
    assert len(again.faces) == len(cube_mesh.faces)
    assert again.volume() == pytest.approx(cube_mesh.volume(), rel=1e-4)


def test_header_carries_a_fixed_tag(tmp_path, cube_mesh):
    path = write_stl(cube_mesh, tmp_path / "cube.stl")
    assert path.read_bytes()[:len(STL_HEADER_TAG)] == STL_HEADER_TAG


# ------------------------------------------------------------- determinism
def test_same_mesh_exports_to_identical_bytes(tmp_path, cube_mesh):
    a = write_stl(cube_mesh, tmp_path / "a.stl")
    b = write_stl(cube_mesh.copy(), tmp_path / "b.stl")
    assert sha256_file(a) == sha256_file(b)


def test_export_is_stable_across_repeat_runs(cube_mesh):
    digests = {hashlib.sha256(stl_bytes(cube_mesh)).hexdigest() for _ in range(5)}
    assert len(digests) == 1


def test_mesh_digest_matches_written_file(tmp_path, cube_mesh):
    path = write_stl(cube_mesh, tmp_path / "cube.stl")
    assert mesh_digest(cube_mesh) == sha256_file(path)


def test_reload_and_reexport_is_a_fixed_point(tmp_path, cube_mesh):
    first = write_stl(cube_mesh, tmp_path / "first.stl")
    reloaded = load_stl(first)
    second = write_stl(reloaded, tmp_path / "second.stl")
    assert sha256_file(first) == sha256_file(second)


def test_different_geometry_gives_a_different_digest(cube_mesh):
    other = primitives.box((20.0, 20.0, 20.1))
    assert mesh_digest(cube_mesh) != mesh_digest(other)


# -------------------------------------------------------------- broken input
def test_truncated_binary_stl_is_refused_not_crashed(tmp_path):
    path = write_truncated_stl(tmp_path / "truncated.stl")
    with pytest.raises(MeshLoadError, match="truncated"):
        load_stl(path)


def test_garbage_bytes_are_refused(tmp_path):
    path = write_garbage_stl(tmp_path / "garbage.stl")
    with pytest.raises(MeshLoadError):
        load_stl(path)


def test_nan_coordinates_are_refused(tmp_path):
    path = write_nan_stl(tmp_path / "nan.stl")
    with pytest.raises(MeshLoadError, match="NaN"):
        load_stl(path)


def test_empty_file_is_refused(tmp_path):
    path = tmp_path / "empty.stl"
    path.write_bytes(b"")
    with pytest.raises(MeshLoadError, match="empty"):
        load_stl(path)


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(MeshLoadError, match="cannot read"):
        load_stl(tmp_path / "nope.stl")


def test_absurd_triangle_count_is_refused_without_allocating(tmp_path):
    path = tmp_path / "huge.stl"
    path.write_bytes(b"h" * 80 + struct.pack("<I", 2_000_000_000) + b"\0" * 50)
    with pytest.raises(MeshLoadError, match="limit"):
        load_stl(path, max_triangles=1000)


def test_ascii_stl_with_a_short_facet_is_refused(tmp_path):
    path = tmp_path / "short.stl"
    path.write_text(
        "solid s\n facet normal 0 0 1\n  outer loop\n"
        "   vertex 0 0 0\n   vertex 1 0 0\n  endloop\n endfacet\nendsolid s\n",
        encoding="utf-8",
    )
    with pytest.raises(MeshLoadError, match="expected 3"):
        load_stl(path)


def test_ascii_stl_with_non_numeric_vertex_is_refused(tmp_path):
    path = tmp_path / "bad.stl"
    path.write_text(
        "solid s\n facet normal 0 0 1\n  outer loop\n"
        "   vertex a b c\n   vertex 1 0 0\n   vertex 0 1 0\n  endloop\n endfacet\nendsolid s\n",
        encoding="utf-8",
    )
    with pytest.raises(MeshLoadError, match="non-numeric"):
        load_stl(path)


def test_unterminated_facet_is_refused(tmp_path):
    path = tmp_path / "unterminated.stl"
    path.write_text(
        "solid s\n facet normal 0 0 1\n  outer loop\n   vertex 0 0 0\n", encoding="utf-8"
    )
    with pytest.raises(MeshLoadError, match="unterminated"):
        load_stl(path)


# ------------------------------------------------------------------ geometry
def test_transform_helpers(cube_mesh):
    moved = cube_mesh.translated((5.0, 0.0, 0.0))
    lo, _ = moved.bounds()
    assert lo[0] == pytest.approx(5.0)
    bigger = cube_mesh.scaled(2.0)
    assert bigger.extents() == pytest.approx((40.0, 40.0, 40.0))
    assert bigger.volume() == pytest.approx(cube_mesh.volume() * 8)


def test_mirror_scaling_keeps_volume_positive(cube_mesh):
    mirrored = cube_mesh.scaled((-1.0, 1.0, 1.0))
    assert mirrored.volume() > 0


def test_axis_swap_keeps_volume_positive(cube_mesh):
    box = primitives.box((10.0, 20.0, 30.0))
    swapped = box.rotated_axis_swap((2, 0, 1))
    assert swapped.extents() == pytest.approx((30.0, 10.0, 20.0))
    assert swapped.volume() == pytest.approx(box.volume())


def test_empty_mesh_bounds_are_zero():
    assert Mesh().bounds() == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
