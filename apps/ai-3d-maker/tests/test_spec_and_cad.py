"""DesignSpec validation and deterministic CAD compilation."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from ai_3d_maker.cad import csg
from ai_3d_maker.cad.compiler import compile_mesh, compile_scad
from ai_3d_maker.cad.external import cadquery_export, openscad_info
from ai_3d_maker.errors import CapabilityUnavailableError
from ai_3d_maker.mesh import mesh_digest
from ai_3d_maker.meshcheck import inspect_mesh
from ai_3d_maker.spec import DesignSpec

requires_csg = pytest.mark.skipif(not csg.is_available(), reason="no CSG backend installed")


def spec(**over) -> DesignSpec:
    payload = {
        "name": "test",
        "features": [{"primitive": {"id": "b", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "add"}],
    }
    payload.update(over)
    return DesignSpec.model_validate(payload)


# ------------------------------------------------------------------- schema
def test_negative_dimensions_are_rejected():
    with pytest.raises(ValidationError):
        spec(features=[{"primitive": {"id": "b", "kind": "box", "size_mm": [10, -2, 3]}, "operation": "add"}])


def test_wrong_dimension_count_is_rejected():
    with pytest.raises(ValidationError, match="diameter,height"):
        spec(features=[{"primitive": {"id": "c", "kind": "cylinder", "size_mm": [4, 8, 9]}, "operation": "add"}])


def test_duplicate_feature_ids_are_rejected():
    with pytest.raises(ValidationError, match="unique"):
        spec(features=[
            {"primitive": {"id": "b", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "add"},
            {"primitive": {"id": "b", "kind": "box", "size_mm": [5, 5, 5]}, "operation": "add"},
        ])


def test_first_feature_cannot_be_a_cut():
    with pytest.raises(ValidationError, match="nothing to cut"):
        spec(features=[{"primitive": {"id": "b", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "cut"}])


def test_absurd_dimensions_are_rejected():
    with pytest.raises(ValidationError, match="implausible"):
        spec(features=[{"primitive": {"id": "b", "kind": "box", "size_mm": [1e9, 1, 1]}, "operation": "add"}])


def test_unknown_fields_are_rejected():
    """Blocks smuggling extra instructions into the spec."""
    with pytest.raises(ValidationError):
        spec(features=[{
            "primitive": {"id": "b", "kind": "box", "size_mm": [1, 1, 1], "exec": "rm -rf /"},
            "operation": "add",
        }])


def test_example_specs_from_the_pack_still_validate(bracket_spec):
    parsed = DesignSpec.model_validate(bracket_spec)
    assert parsed.name == "simple_mounting_block"
    assert len(parsed.features) == 3


# --------------------------------------------------------------- scad source
def test_scad_is_deterministic(bracket_spec):
    parsed = DesignSpec.model_validate(bracket_spec)
    assert compile_scad(parsed) == compile_scad(parsed)


def test_scad_contains_the_expected_operations(bracket_spec):
    text = compile_scad(DesignSpec.model_validate(bracket_spec))
    assert "difference()" in text
    assert "cube(" in text
    assert "cylinder(" in text
    assert "units: mm" in text


# ----------------------------------------------------------------- geometry
def test_single_primitive_needs_no_csg_backend():
    result = compile_mesh(spec())
    assert result.backend == "not-needed"
    assert inspect_mesh(result.mesh).status == "PASS"


@requires_csg
def test_bracket_compiles_to_a_watertight_solid_with_two_holes(bracket_spec):
    parsed = DesignSpec.model_validate(bracket_spec)
    result = compile_mesh(parsed)
    report = inspect_mesh(result.mesh)
    assert report.is_watertight
    assert report.is_edge_manifold
    assert report.is_winding_consistent
    assert report.components == 1
    expected = 60 * 30 * 8 - 2 * math.pi * (2.0 ** 2) * 8
    assert report.signed_volume_mm3 == pytest.approx(expected, rel=2e-3)
    assert report.extents_mm == pytest.approx((60.0, 30.0, 8.0))


@requires_csg
def test_union_of_two_overlapping_boxes_is_one_solid():
    parsed = spec(features=[
        {"primitive": {"id": "a", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "add"},
        {"primitive": {"id": "b", "kind": "box", "size_mm": [10, 10, 10]},
         "transform": {"translate_mm": [5, 0, 0], "rotate_deg": [0, 0, 0]}, "operation": "add"},
    ])
    report = inspect_mesh(compile_mesh(parsed).mesh)
    assert report.is_watertight
    assert report.components == 1
    assert report.signed_volume_mm3 == pytest.approx(1500.0, rel=1e-4)


@requires_csg
def test_intersection_of_two_boxes():
    parsed = spec(features=[
        {"primitive": {"id": "a", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "add"},
        {"primitive": {"id": "b", "kind": "box", "size_mm": [10, 10, 10]},
         "transform": {"translate_mm": [6, 0, 0], "rotate_deg": [0, 0, 0]}, "operation": "intersect"},
    ])
    report = inspect_mesh(compile_mesh(parsed).mesh)
    assert report.is_watertight
    assert report.signed_volume_mm3 == pytest.approx(400.0, rel=1e-4)


@requires_csg
def test_cut_that_removes_everything_is_an_honest_error():
    parsed = spec(features=[
        {"primitive": {"id": "a", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "add"},
        {"primitive": {"id": "b", "kind": "box", "size_mm": [50, 50, 50]},
         "transform": {"translate_mm": [-20, -20, -20], "rotate_deg": [0, 0, 0]}, "operation": "cut"},
    ])
    with pytest.raises(Exception, match="no solid|empty"):
        compile_mesh(parsed)


@requires_csg
def test_compilation_is_deterministic(bracket_spec):
    parsed = DesignSpec.model_validate(bracket_spec)
    first = compile_mesh(parsed).mesh
    second = compile_mesh(parsed).mesh
    assert mesh_digest(first) == mesh_digest(second)


def test_rotation_is_applied():
    parsed = spec(features=[{
        "primitive": {"id": "b", "kind": "box", "size_mm": [10, 20, 30]},
        "transform": {"translate_mm": [0, 0, 0], "rotate_deg": [90, 0, 0]},
        "operation": "add",
    }])
    extents = compile_mesh(parsed).mesh.extents()
    assert extents == pytest.approx((10.0, 30.0, 20.0))


def test_missing_csg_backend_is_reported_not_faked(monkeypatch):
    monkeypatch.setattr(csg, "available_backend", lambda: csg.BackendInfo("none", False, reason="simulated absence"))
    parsed = spec(features=[
        {"primitive": {"id": "a", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "add"},
        {"primitive": {"id": "b", "kind": "box", "size_mm": [5, 5, 5]}, "operation": "cut"},
    ])
    with pytest.raises(CapabilityUnavailableError, match="no CSG backend"):
        compile_mesh(parsed)


# -------------------------------------------------------- external engines
def test_openscad_absence_is_reported_honestly():
    info = openscad_info("definitely-not-a-real-binary-xyz")
    assert info["available"] is False
    assert "not found" in info["reason"]


def test_cadquery_export_reports_not_available_when_missing(tmp_path):
    result = cadquery_export(spec(), tmp_path / "m.step", tmp_path / "m.stl")
    assert result["status"] in {"NOT_AVAILABLE", "PASS"}
    if result["status"] == "NOT_AVAILABLE":
        assert not (tmp_path / "m.step").exists()
        assert result["step"] is None
