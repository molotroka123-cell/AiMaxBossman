"""G-code safety scanner: the last digital gate before hardware."""

from __future__ import annotations

from ai_3d_maker.gcode import scan_gcode
from conftest import SAFE_GCODE


def test_realistic_safe_gcode_passes(profile):
    scan = scan_gcode(SAFE_GCODE, profile)
    assert scan.status in {"PASS", "WARN"}
    assert not any(i["severity"] == "ERROR" for i in scan.issues)
    assert scan.max_nozzle_target_c == 205
    assert scan.max_bed_target_c == 60
    assert scan.safe


def test_nozzle_above_the_verified_cap_is_rejected(profile):
    scan = scan_gcode("M104 S300", profile)
    assert scan.status == "FAILED"
    assert "260" in scan.issues[0]["message"]


def test_nozzle_exactly_at_the_cap_is_allowed(profile):
    assert scan_gcode("M109 S260", profile).status != "FAILED"


def test_bed_above_the_verified_cap_is_rejected(profile):
    scan = scan_gcode("M140 S120", profile)
    assert scan.status == "FAILED"
    assert "100" in scan.issues[0]["message"]


def test_bed_exactly_at_the_cap_is_allowed(profile):
    assert scan_gcode("M190 S100", profile).status != "FAILED"


def test_extrusion_outside_x_envelope_is_rejected(profile):
    scan = scan_gcode("G90\nM82\nG28\nG1 X400 Y10 Z0.2 E1", profile)
    assert scan.status == "FAILED"
    assert any("outside" in i["message"] for i in scan.issues)


def test_extrusion_outside_z_envelope_is_rejected(profile):
    scan = scan_gcode("G90\nM82\nG28\nG1 X10 Y10 Z500 E1", profile)
    assert scan.status == "FAILED"


def test_negative_extrusion_coordinate_is_rejected(profile):
    scan = scan_gcode("G90\nM82\nG28\nG1 X-5 Y10 Z0.2 E1", profile)
    assert scan.status == "FAILED"


def test_travel_outside_the_envelope_is_not_an_error(profile):
    """Non-extruding moves are allowed to be outside; only extrusion is fatal."""
    scan = scan_gcode("G90\nM82\nG28\nG0 X400 Y400 Z0.2", profile)
    assert scan.status != "FAILED"


def test_relative_moves_are_tracked(profile):
    scan = scan_gcode("G90\nM82\nG28\nG1 X300 Y10 Z0.2\nG91\nG1 X100 E1", profile)
    assert scan.status == "FAILED"


def test_relative_extrusion_is_tracked(profile):
    scan = scan_gcode("G90\nM83\nG28\nG1 X400 Y10 Z0.2 E0.5", profile)
    assert scan.status == "FAILED"


def test_g92_reset_is_honoured(profile):
    scan = scan_gcode("G90\nM82\nG28\nG1 X10 Y10 Z0.2\nG92 X0\nG1 X100 E1", profile)
    assert scan.status != "FAILED"


def test_eeprom_write_is_rejected(profile):
    assert scan_gcode("M500", profile).status == "FAILED"


def test_factory_reset_is_rejected(profile):
    assert scan_gcode("M502", profile).status == "FAILED"


def test_firmware_update_command_is_rejected(profile):
    assert scan_gcode("M997", profile).status == "FAILED"


def test_steps_per_unit_change_is_rejected(profile):
    assert scan_gcode("M92 X80 Y80 Z400", profile).status == "FAILED"


def test_probe_offset_change_is_rejected(profile):
    assert scan_gcode("M851 Z-1.5", profile).status == "FAILED"


def test_zero_padded_command_cannot_bypass_the_block_list(profile):
    assert scan_gcode("M0500", profile).status == "FAILED"


def test_unspaced_parameters_are_still_parsed(profile):
    assert scan_gcode("M104S300", profile).status == "FAILED"


def test_lowercase_commands_are_normalised(profile):
    assert scan_gcode("m140 s150", profile).status == "FAILED"


def test_comments_are_ignored(profile):
    assert scan_gcode("; M500 mentioned in a comment\nG28", profile).status != "FAILED"


def test_parenthesised_comments_are_ignored(profile):
    assert scan_gcode("G28 (M500 here)", profile).status != "FAILED"


def test_strict_mode_flags_unknown_commands(profile):
    scan = scan_gcode("G28\nM6969", profile, strict_unknown=True)
    assert scan.status == "WARN"
    assert any(i["command"] == "M6969" for i in scan.issues)


def test_non_strict_mode_stays_quiet_about_unknown_commands(profile):
    assert scan_gcode("G28\nM6969", profile, strict_unknown=False).status == "PASS"


def test_extrusion_before_homing_is_warned(profile):
    scan = scan_gcode("G90\nM82\nG1 X10 Y10 Z0.2 E1", profile)
    assert scan.status == "WARN"
    assert any("before any G28" in i["message"] for i in scan.issues)


def test_scan_records_the_envelope_actually_used(profile):
    scan = scan_gcode(SAFE_GCODE, profile)
    assert scan.extrusion_bounds_max_mm["X"] <= profile.build_x
    assert scan.extrusion_bounds_min_mm["X"] >= 0
    assert scan.profile_id == profile.id


def test_empty_gcode_passes_trivially(profile):
    scan = scan_gcode("", profile)
    assert scan.status == "PASS"
    assert scan.commands_scanned == 0
