"""Units, scale, orientation, build-volume fit and the printability verdict."""

from __future__ import annotations

import pytest

from ai_3d_maker.cad import primitives
from ai_3d_maker.mesh import Mesh
from ai_3d_maker.meshcheck import inspect_mesh
from ai_3d_maker.printability import (
    decide_printability,
    evaluate_fit,
    minimum_wall_warning,
    normalize_units,
    orient_mesh,
    place_on_bed,
    scale_to_fit,
    thin_feature_warnings,
)
from conftest import make_open_box_mesh, make_two_component_mesh


# ------------------------------------------------------------------- units
def test_millimetres_pass_through_unchanged():
    mesh = primitives.box((10.0, 10.0, 10.0))
    out, report = normalize_units(mesh, "mm")
    assert not report.converted
    assert out.extents() == pytest.approx((10.0, 10.0, 10.0))


def test_inches_are_converted_to_millimetres():
    mesh = primitives.box((1.0, 2.0, 3.0))
    out, report = normalize_units(mesh, "in")
    assert report.converted
    assert report.factor_to_mm == pytest.approx(25.4)
    assert out.extents() == pytest.approx((25.4, 50.8, 76.2))
    assert out.units == "mm"


def test_centimetres_are_converted():
    out, report = normalize_units(primitives.box((1.0, 1.0, 1.0)), "cm")
    assert out.extents() == pytest.approx((10.0, 10.0, 10.0))
    assert report.factor_to_mm == 10.0


def test_unknown_unit_is_rejected():
    with pytest.raises(ValueError, match="unknown unit"):
        normalize_units(primitives.box((1.0, 1.0, 1.0)), "furlongs")


def test_suspiciously_tiny_model_warns_about_units():
    _, report = normalize_units(primitives.box((0.1, 0.1, 0.1)), "mm")
    assert any("may not be in mm" in w for w in report.warnings)


def test_suspiciously_huge_model_warns_about_units():
    _, report = normalize_units(primitives.box((100.0, 100.0, 100.0)), "m")
    assert any("smaller unit" in w for w in report.warnings)


# --------------------------------------------------------------------- fit
def test_small_model_fits_without_rotation(profile):
    report = evaluate_fit(primitives.box((100.0, 200.0, 50.0)), profile)
    assert report.fits
    assert not report.rotated
    assert report.axis_permutation == (0, 1, 2)


def test_model_at_the_exact_usable_limit_fits(profile):
    ux, uy, uz = profile.usable_xyz()
    report = evaluate_fit(primitives.box((ux, uy, uz)), profile)
    assert report.fits


def test_model_one_millimetre_over_does_not_fit(profile):
    ux, uy, uz = profile.usable_xyz()
    report = evaluate_fit(primitives.box((ux + 1.0, uy, uz)), profile, allow_rotate=False)
    assert not report.fits
    assert any("does not fit" in e for e in report.errors)


def test_tall_model_fits_only_after_reorientation(profile):
    report = evaluate_fit(primitives.box((390.0, 200.0, 300.0)), profile)
    assert report.fits
    assert report.rotated
    assert report.chosen_orientation_mm is not None
    assert report.chosen_orientation_mm[2] == pytest.approx(390.0)


def test_rotation_can_be_forbidden(profile):
    report = evaluate_fit(primitives.box((390.0, 200.0, 300.0)), profile, allow_rotate=False)
    assert not report.fits


def test_oversize_model_never_fits(profile):
    report = evaluate_fit(primitives.box((500.0, 500.0, 500.0)), profile)
    assert not report.fits
    assert "Neptune 3 Plus" in report.errors[0]


def test_neptune_envelope_numbers_are_the_ones_enforced(profile):
    assert evaluate_fit(primitives.box((315.0, 315.0, 399.0)), profile).fits
    assert not evaluate_fit(primitives.box((330.0, 330.0, 410.0)), profile).fits


def test_orient_mesh_applies_the_chosen_permutation(profile):
    mesh = primitives.box((390.0, 200.0, 300.0))
    report = evaluate_fit(mesh, profile)
    oriented = orient_mesh(mesh, report.axis_permutation)
    assert oriented.extents() == pytest.approx(report.chosen_orientation_mm)
    assert evaluate_fit(oriented, profile, allow_rotate=False).fits
    assert oriented.volume() > 0


def test_scale_to_fit_shrinks_an_oversize_model(profile):
    mesh = primitives.box((640.0, 640.0, 800.0))
    scaled, factor = scale_to_fit(mesh, profile)
    assert factor < 1.0
    assert evaluate_fit(scaled, profile).fits


def test_scale_to_fit_leaves_a_fitting_model_alone(profile):
    mesh = primitives.box((10.0, 10.0, 10.0))
    scaled, factor = scale_to_fit(mesh, profile)
    assert factor == 1.0
    assert scaled.extents() == pytest.approx(mesh.extents())


def test_place_on_bed_drops_the_model_to_z_zero(profile):
    mesh = primitives.box((10.0, 10.0, 10.0)).translated((0.0, 0.0, 47.0))
    placed = place_on_bed(mesh, profile)
    lo, _ = placed.bounds()
    assert lo[2] == pytest.approx(0.0)
    assert 0.0 < lo[0] < profile.build_x
    assert 0.0 < lo[1] < profile.build_y


# ------------------------------------------------------------------- walls
def test_minimum_wall_warning_triggers_below_three_lines(profile):
    assert minimum_wall_warning(0.8, profile.nozzle, 3)
    assert not minimum_wall_warning(1.2, profile.nozzle, 3)


def test_thin_part_is_warned_about(profile):
    warnings = thin_feature_warnings(primitives.box((50.0, 50.0, 0.3)), profile)
    assert warnings
    assert "unverified app default" in warnings[0]


def test_normal_part_produces_no_thin_warning(profile):
    assert thin_feature_warnings(primitives.box((50.0, 50.0, 5.0)), profile) == []


# ---------------------------------------------------------------- verdicts
def test_good_geometry_is_printable(profile):
    mesh = primitives.box((50.0, 50.0, 10.0))
    verdict = decide_printability(inspect_mesh(mesh), evaluate_fit(mesh, profile))
    assert verdict.printable
    assert verdict.status == "PRINTABLE"
    assert verdict.checks["watertight"] and verdict.checks["fits_build_volume"]


def test_open_mesh_is_not_printable_even_though_a_file_could_be_written(profile):
    mesh = make_open_box_mesh()
    verdict = decide_printability(inspect_mesh(mesh), evaluate_fit(mesh, profile))
    assert not verdict.printable
    assert verdict.status == "NOT_PRINTABLE"
    assert any("watertight" in r for r in verdict.reasons)


def test_oversize_mesh_is_not_printable(profile):
    mesh = primitives.box((500.0, 500.0, 500.0))
    verdict = decide_printability(inspect_mesh(mesh), evaluate_fit(mesh, profile))
    assert not verdict.printable
    assert any("does not fit" in r for r in verdict.reasons)


def test_multiple_components_warn_but_stay_printable(profile):
    mesh = make_two_component_mesh()
    verdict = decide_printability(inspect_mesh(mesh), evaluate_fit(mesh, profile))
    assert verdict.printable
    assert verdict.status == "PRINTABLE_WITH_WARNINGS"
    assert any("disconnected components" in w for w in verdict.warnings)


def test_multiple_components_can_be_made_fatal(profile):
    mesh = make_two_component_mesh()
    verdict = decide_printability(
        inspect_mesh(mesh), evaluate_fit(mesh, profile), allow_multiple_components=False
    )
    assert not verdict.printable


def test_empty_mesh_is_not_printable(profile):
    verdict = decide_printability(inspect_mesh(Mesh()), evaluate_fit(Mesh(), profile))
    assert not verdict.printable
