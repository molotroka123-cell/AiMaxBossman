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
        material="PLA", nozzle_mm=0.4, layer_height_mm=0.2, line_width_mm=0.42,
        measured_process_tolerance_mm=0.15, filament_brand="Test",
        measured_at="2026-08-01", coupon_measurements={"outer_xy": [20.0, 19.88]},
    )
    path = profile.save(tmp_path / "cal.json")
    assert CalibrationProfile.load(path) == profile


def test_calibration_profile_rejects_a_non_positive_tolerance():
    with pytest.raises(ValueError, match="measured_process_tolerance_mm"):
        CalibrationProfile(
            id="x", printer_profile_id="y", material="PLA",
            nozzle_mm=0.4, layer_height_mm=0.2, line_width_mm=0.42,
            measured_process_tolerance_mm=0.0, measured_at="2026-08-01",
            coupon_measurements={"outer_xy": [20.0, 19.88]},
        )


def test_default_compensation_is_zero():
    profile = CalibrationProfile(
        id="x", printer_profile_id="y", material="PLA",
        nozzle_mm=0.4, layer_height_mm=0.2, line_width_mm=0.42,
        measured_process_tolerance_mm=0.15, measured_at="2026-08-01",
        coupon_measurements={"outer_xy": [20.0, 19.88]},
    )
    assert profile.hole_compensation_mm == 0.0
    assert profile.xy_scale == 1.0 and profile.z_scale == 1.0


def test_coupon_spec_is_a_valid_designspec():
    parsed = DesignSpec.model_validate(coupon_spec())
    assert len(parsed.features) == 2
    assert parsed.critical_dimensions["hole_nominal"] == 5.0


# ------------------------------------------- what a calibration profile is
"""A measured capability has to be traceable to a specific process.

"+/-0.15 mm" is meaningless without the printer, nozzle, material, layer
height and line width it was measured on, the coupon numbers behind it, and a
version so a re-measurement can supersede it rather than silently merge.
"""


def full_profile(**over) -> CalibrationProfile:
    payload = {
        "id": "pla-0.4-0.2",
        "printer_profile_id": "elegoo-neptune-3-plus-stock-0.4",
        "material": "PLA",
        "nozzle_mm": 0.4,
        "layer_height_mm": 0.2,
        "line_width_mm": 0.42,
        "measured_process_tolerance_mm": 0.15,
        "filament_brand": "Test",
        "measured_at": "2026-08-01",
        "version": 1,
        "coupon_measurements": {"outer_xy": [20.0, 19.88], "hole": [5.0, 4.78]},
    }
    payload.update(over)
    return CalibrationProfile(**payload)


def test_a_calibration_profile_identifies_the_whole_process():
    profile = full_profile()
    d = profile.as_dict()
    for key in ("printer_profile_id", "material", "nozzle_mm", "layer_height_mm",
                "line_width_mm", "measured_at", "version", "coupon_measurements"):
        assert key in d, key
    assert profile.line_width_mm == 0.42
    assert profile.version == 1


def test_a_calibration_profile_without_a_measurement_date_is_refused():
    with pytest.raises(ValueError, match="measured_at"):
        full_profile(measured_at="")


def test_a_calibration_profile_without_coupon_numbers_is_refused():
    with pytest.raises(ValueError, match="coupon_measurements"):
        full_profile(coupon_measurements={})


def test_coupon_measurements_must_be_nominal_and_measured_pairs():
    with pytest.raises(ValueError, match="coupon_measurements"):
        full_profile(coupon_measurements={"outer_xy": [20.0]})
    with pytest.raises(ValueError, match="coupon_measurements"):
        full_profile(coupon_measurements={"outer_xy": [20.0, 0.0]})


def test_a_calibration_profile_version_must_be_positive():
    with pytest.raises(ValueError, match="version"):
        full_profile(version=0)


def test_line_width_must_be_positive():
    with pytest.raises(ValueError, match="line_width_mm"):
        full_profile(line_width_mm=0.0)


def test_a_full_calibration_profile_round_trips(tmp_path):
    profile = full_profile()
    assert CalibrationProfile.load(profile.save(tmp_path / "cal.json")) == profile


# ------------------------------------ where the number in the gate came from
def test_a_measured_profile_backs_the_tolerance_claim(profile):
    gate = evaluate_requirements(
        spec(required_tolerance_mm=0.05, material="PLA"),
        calibration=full_profile(), profile=profile,
    )
    assert gate.status == NEEDS_CALIBRATION
    assert gate.calibration["source"] == "measured_profile"
    assert gate.calibration["profile_id"] == "pla-0.4-0.2"
    assert gate.calibration["version"] == 1


def test_a_bare_number_is_recorded_as_an_unverified_caller_assertion():
    gate = evaluate_requirements(spec(required_tolerance_mm=0.05), 0.2)
    assert gate.status == NEEDS_CALIBRATION
    assert gate.calibration["source"] == "caller_assertion_unverified"
    assert any("not backed by a measured calibration profile" in w for w in gate.warnings)


def test_no_calibration_at_all_is_recorded_as_none():
    gate = evaluate_requirements(spec(required_tolerance_mm=0.3))
    assert gate.calibration["source"] == "none"
    assert gate.calibration["compensation_applied"] is False


def test_compensation_is_never_applied_without_an_explicit_profile():
    for gate in (evaluate_requirements(spec(required_tolerance_mm=0.3)),
                 evaluate_requirements(spec(required_tolerance_mm=0.3), 0.2)):
        assert gate.calibration["compensation_applied"] is False


def test_a_profile_for_another_printer_is_refused(profile):
    gate = evaluate_requirements(
        spec(required_tolerance_mm=0.3),
        calibration=full_profile(printer_profile_id="some-other-printer"),
        profile=profile,
    )
    assert not gate.ready
    assert any("another printer" in q for q in gate.questions)


def test_a_profile_for_another_material_does_not_silently_apply(profile):
    gate = evaluate_requirements(
        spec(required_tolerance_mm=0.3, material="PETG"),
        calibration=full_profile(material="PLA"), profile=profile,
    )
    assert any("measured on PLA" in w for w in gate.warnings)


def test_a_profile_for_another_nozzle_does_not_silently_apply(profile):
    gate = evaluate_requirements(
        spec(required_tolerance_mm=0.3),
        calibration=full_profile(nozzle_mm=0.6), profile=profile,
    )
    assert any("0.6" in w and "nozzle" in w for w in gate.warnings)
