"""Mesh health checks: watertight/manifold, degenerate facets, components."""

from __future__ import annotations

import math

import pytest

from ai_3d_maker.cad import primitives
from ai_3d_maker.mesh import Mesh
from ai_3d_maker.meshcheck import cross_check_with_trimesh, inspect_mesh
from conftest import (
    make_degenerate_mesh,
    make_inverted_mesh,
    make_open_box_mesh,
    make_two_component_mesh,
)


# ---------------------------------------------------------------- primitives
@pytest.mark.parametrize(
    "mesh,expected_volume",
    [
        (primitives.box((10.0, 20.0, 30.0)), 6000.0),
        (primitives.box((5.0, 5.0, 5.0), center=True), 125.0),
    ],
)
def test_boxes_are_watertight_and_correct(mesh, expected_volume):
    report = inspect_mesh(mesh)
    assert report.status == "PASS"
    assert report.is_watertight
    assert report.is_edge_manifold
    assert report.is_winding_consistent
    assert report.components == 1
    assert report.boundary_edges == 0
    assert report.signed_volume_mm3 == pytest.approx(expected_volume)


def test_cylinder_is_watertight_and_close_to_analytic_volume():
    mesh = primitives.cylinder(10.0, 20.0, segments=256)
    report = inspect_mesh(mesh)
    assert report.status == "PASS"
    assert report.is_watertight
    assert report.components == 1
    assert report.signed_volume_mm3 == pytest.approx(math.pi * 25 * 20, rel=1e-3)


def test_sphere_is_watertight_and_close_to_analytic_volume():
    mesh = primitives.sphere(20.0, segments=128)
    report = inspect_mesh(mesh)
    assert report.is_watertight
    assert report.is_winding_consistent
    assert report.signed_volume_mm3 == pytest.approx(4 / 3 * math.pi * 1000, rel=5e-3)


# ------------------------------------------------------------------ failures
def test_open_surface_is_detected_and_fails():
    report = inspect_mesh(make_open_box_mesh())
    assert not report.is_watertight
    assert report.boundary_edges == 4
    assert report.status == "FAIL"
    assert any("not closed" in e for e in report.errors)


def test_degenerate_triangles_are_counted():
    report = inspect_mesh(make_degenerate_mesh())
    assert report.degenerate_triangles == 2
    assert report.is_watertight  # the valid cube part is still closed


def test_disconnected_components_are_counted():
    report = inspect_mesh(make_two_component_mesh())
    assert report.components == 2
    assert report.is_watertight
    assert report.status == "WARN"


def test_inverted_mesh_is_flagged_by_negative_volume():
    report = inspect_mesh(make_inverted_mesh())
    assert report.signed_volume_mm3 < 0
    assert report.status == "FAIL"
    assert any("inverted" in e for e in report.errors)


def test_flipped_single_face_breaks_winding_consistency():
    mesh = primitives.box((10.0, 10.0, 10.0))
    faces = list(mesh.faces)
    a, b, c = faces[0]
    faces[0] = (a, c, b)
    report = inspect_mesh(Mesh(list(mesh.vertices), faces))
    assert not report.is_winding_consistent
    assert report.status == "FAIL"


def test_non_manifold_edge_is_detected():
    """Three triangles sharing one edge: a classic non-manifold T-junction."""
    verts = [
        (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
        (0.0, 10.0, 0.0), (0.0, -10.0, 0.0), (0.0, 0.0, 10.0),
    ]
    faces = [(0, 1, 2), (0, 1, 3), (0, 1, 4)]
    report = inspect_mesh(Mesh(verts, faces))
    assert report.non_manifold_edges >= 1
    assert not report.is_edge_manifold
    assert report.status == "FAIL"


def test_empty_mesh_fails():
    report = inspect_mesh(Mesh())
    assert report.status == "FAIL"
    assert report.triangles == 0


def test_flat_mesh_is_rejected_for_zero_thickness():
    verts = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
    report = inspect_mesh(Mesh(verts, [(0, 1, 2), (0, 2, 1)]))
    assert report.status == "FAIL"
    assert any("degenerate bounding box" in e for e in report.errors)


def test_duplicate_triangles_are_counted():
    mesh = primitives.box((10.0, 10.0, 10.0))
    faces = list(mesh.faces) + [mesh.faces[0]]
    report = inspect_mesh(Mesh(list(mesh.vertices), faces))
    assert report.duplicate_triangles == 1


# --------------------------------------------------------------- bbox/units
def test_extents_and_bbox_are_reported_in_mm():
    mesh = primitives.box((12.5, 33.0, 4.25)).translated((3.0, -2.0, 1.0))
    report = inspect_mesh(mesh)
    assert report.extents_mm == pytest.approx((12.5, 33.0, 4.25))
    assert report.bbox_min_mm == pytest.approx((3.0, -2.0, 1.0))
    assert report.bbox_max_mm == pytest.approx((15.5, 31.0, 5.25))
    assert report.units_declared == "mm"


def test_self_intersection_is_reported_as_not_checked():
    report = inspect_mesh(primitives.box((1.0, 1.0, 1.0)))
    assert report.self_intersection_check == "NOT_CHECKED"


# ------------------------------------------------------ independent opinion
def test_trimesh_cross_check_agrees_when_available(tmp_path, cube_stl):
    result = cross_check_with_trimesh(cube_stl)
    if result["status"] == "NOT_AVAILABLE":
        pytest.skip(f"trimesh not installed: {result['reason']}")
    assert result["watertight"] is True
    assert result["components"] in (1, None)
    assert result["volume_mm3"] == pytest.approx(8000.0, rel=1e-4)


def test_trimesh_cross_check_reports_failure_on_garbage(tmp_path):
    path = tmp_path / "bad.stl"
    path.write_bytes(b"not an stl at all")
    result = cross_check_with_trimesh(path)
    assert result["status"] in {"LOAD_FAILED", "NOT_AVAILABLE"}
