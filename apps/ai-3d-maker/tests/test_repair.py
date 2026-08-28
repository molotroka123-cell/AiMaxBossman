"""Repair must fix what it can and must NOT pretend to fix holes."""

from __future__ import annotations

import pytest

from ai_3d_maker.cad import primitives
from ai_3d_maker.mesh import Mesh
from ai_3d_maker.meshcheck import inspect_mesh
from ai_3d_maker.repair import components, keep_largest_component, repair_mesh, weld
from conftest import (
    make_degenerate_mesh,
    make_inverted_mesh,
    make_open_box_mesh,
    make_two_component_mesh,
)


def test_repair_removes_degenerate_triangles():
    fixed, report = repair_mesh(make_degenerate_mesh())
    assert report.removed_degenerate == 2
    assert inspect_mesh(fixed).degenerate_triangles == 0
    assert inspect_mesh(fixed).status == "PASS"


def test_repair_removes_duplicate_faces():
    mesh = primitives.box((10.0, 10.0, 10.0))
    dirty = Mesh(list(mesh.vertices), list(mesh.faces) + [mesh.faces[0]])
    fixed, report = repair_mesh(dirty)
    assert report.removed_duplicate == 1
    assert inspect_mesh(fixed).duplicate_triangles == 0


def test_repair_flips_an_inside_out_mesh():
    fixed, report = repair_mesh(make_inverted_mesh())
    assert report.flipped_global
    assert inspect_mesh(fixed).signed_volume_mm3 > 0


def test_repair_unifies_winding():
    mesh = primitives.box((10.0, 10.0, 10.0))
    faces = list(mesh.faces)
    a, b, c = faces[3]
    faces[3] = (a, c, b)
    fixed, report = repair_mesh(Mesh(list(mesh.vertices), faces))
    assert report.reoriented_faces >= 1
    assert inspect_mesh(fixed).is_winding_consistent


def test_repair_does_not_close_holes():
    """The honest behaviour: an open mesh stays open and stays failing."""
    fixed, _ = repair_mesh(make_open_box_mesh())
    report = inspect_mesh(fixed)
    assert not report.is_watertight
    assert report.status == "FAIL"


def test_repair_keeps_components_by_default():
    fixed, report = repair_mesh(make_two_component_mesh())
    assert report.removed_components == 0
    assert inspect_mesh(fixed).components == 2


def test_repair_can_drop_smaller_components_on_request():
    fixed, report = repair_mesh(make_two_component_mesh(), drop_extra_components=True)
    assert report.removed_components == 1
    assert inspect_mesh(fixed).components == 1


def test_repair_is_idempotent():
    once, _ = repair_mesh(make_degenerate_mesh())
    twice, report = repair_mesh(once)
    assert not report.changed
    assert twice.faces == once.faces


def test_repair_of_a_clean_mesh_changes_nothing():
    mesh = primitives.cylinder(8.0, 12.0, segments=64)
    fixed, report = repair_mesh(mesh)
    assert not report.changed
    assert len(fixed.faces) == len(mesh.faces)


def test_weld_merges_near_duplicate_vertices():
    verts = [(0.0, 0.0, 0.0), (1e-9, 0.0, 0.0), (1.0, 0.0, 0.0)]
    welded, removed = weld(Mesh(verts, [(0, 2, 1)]), 1e-5)
    assert removed == 1
    assert len(welded.vertices) == 2


def test_components_helper_groups_faces():
    groups = components(make_two_component_mesh())
    assert len(groups) == 2
    assert sum(len(g) for g in groups) == 24


def test_keep_largest_component_picks_the_bigger_body():
    small = primitives.box((2.0, 2.0, 2.0))
    big = primitives.box((20.0, 20.0, 20.0)).translated((50.0, 0.0, 0.0))
    offset = len(small.vertices)
    combined = Mesh(
        small.vertices + big.vertices,
        list(small.faces) + [(a + offset, b + offset, c + offset) for a, b, c in big.faces],
    )
    kept, dropped = keep_largest_component(combined)
    assert dropped == 1
    assert kept.extents() == pytest.approx((20.0, 20.0, 20.0))
