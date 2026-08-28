"""Requirement gate and calibration bookkeeping."""

from __future__ import annotations

import pytest

from ai_3d_maker.requirements import BLOCKED, NEEDS_CALIBRATION, READY, evaluate_requirements
from ai_3d_maker.spec import DesignSpec
from ai_3d_maker.tolerance import CalibrationProfile, coupon_spec, scale_from_coupon, suggest_xy_scale


def spec(**manufacturing) -> DesignSpec:
    return DesignSpec.model_validate({
        "name": "x",
        "features": [{"primitive": {"id": "b", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "add"}],
        "manufacturing": manufacturing or {},
        "unresolved_questions": manufacturing.pop("questions", []) if "questions" in manufacturing else [],
    })


def test_clean_spec_is_ready():
    gate = evaluate_requirements(spec())
    assert gate.ready and gate.status == READY


def test_unresolved_questions_block():
    parsed = DesignSpec.model_validate({
        "name": "x",
        "features": [{"primitive": {"id": "b", "kind": "box", "size_mm": [10, 10, 10]}, "operation": "add"}],
        "unresolved_questions": ["Clearance or tapped?"],
    })
    gate = evaluate_requirements(parsed)
    assert not gate.ready
    assert gate.status == BLOCKED
    assert "Clearance or tapped?" in gate.questions


def test_tolerance_tighter_than_capability_needs_calibration():
    gate = evaluate_requirements(spec(required_tolerance_mm=0.05), 0.2)
    assert not gate.ready
    assert gate.status == NEEDS_CALIBRATION


def test_tolerance_within_capability_is_ready():
    assert evaluate_requirements(spec(required_tolerance_mm=0.3), 0.2).ready


def test_uncalibrated_tolerance_warns_without_blocking():
    gate = evaluate_requirements(spec(required_tolerance_mm=0.2))
    assert gate.ready
    assert any("no measured calibration profile" in w for w in gate.warnings)


def test_non_positive_tolerance_blocks():
    gate = evaluate_requirements(spec(required_tolerance_mm=0.0))
    assert not gate.ready


def test_fit_intent_without_a_tolerance_warns():
    gate = evaluate_requirements(spec(fit_intent="press"))
    assert gate.ready
    assert any("implies a tolerance" in w for w in gate.warnings)


def test_unlisted_material_warns_against_the_profile(profile):
    gate = evaluate_requirements(spec(material="NYLON"), profile=profile)
    assert gate.ready
    assert any("not in the manufacturer's listed set" in w for w in gate.warnings)


def test_listed_material_does_not_warn(profile):
    gate = evaluate_requirements(spec(material="PETG"), profile=profile)
    assert not any("listed set" in w for w in gate.warnings)


def test_thin_min_wall_request_warns(profile):
    gate = evaluate_requirements(spec(min_wall_mm=0.4), profile=profile)
    assert any("below the conservative app default" in w for w in gate.warnings)


# ------------------------------------------------------------- calibration
def test_scale_from_coupon():
    assert scale_from_coupon(20, 19.8) == pytest.approx(20 / 19.8)


def test_scale_from_coupon_rejects_nonsense():
    with pytest.raises(ValueError):
        scale_from_coupon(20, 0)


def test_suggested_xy_scale_averages_both_axes():
    assert 0.99 < suggest_xy_scale(20, 19.8, 20, 20.2) < 1.01


def test_calibration_profile_round_trips(tmp_path):
    profile = CalibrationProfile(
        id="pla-0.4-0.2", printer_profile_id="elegoo-neptune-3-plus-stock-0.4",
        material="PLA", nozzle_mm=0.4, layer_height_mm=0.2,
        measured_process_tolerance_mm=0.15, filament_brand="Test",
    )
    path = profile.save(tmp_path / "cal.json")
    assert CalibrationProfile.load(path) == profile


def test_calibration_profile_rejects_a_non_positive_tolerance():
    with pytest.raises(ValueError, match="measured_process_tolerance_mm"):
        CalibrationProfile(
            id="x", printer_profile_id="y", material="PLA",
            nozzle_mm=0.4, layer_height_mm=0.2, measured_process_tolerance_mm=0.0,
        )


def test_default_compensation_is_zero():
    profile = CalibrationProfile(
        id="x", printer_profile_id="y", material="PLA",
        nozzle_mm=0.4, layer_height_mm=0.2, measured_process_tolerance_mm=0.15,
    )
    assert profile.hole_compensation_mm == 0.0
    assert profile.xy_scale == 1.0 and profile.z_scale == 1.0


def test_coupon_spec_is_a_valid_designspec():
    parsed = DesignSpec.model_validate(coupon_spec())
    assert len(parsed.features) == 2
    assert parsed.critical_dimensions["hole_nominal"] == 5.0
