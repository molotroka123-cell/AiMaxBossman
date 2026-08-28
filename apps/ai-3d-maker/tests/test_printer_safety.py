"""The physical boundary.

These tests exist because getting this wrong heats a nozzle in someone's house.
No test here touches hardware; the physical smoke test is BLOCKED BY HARDWARE.
"""

from __future__ import annotations

import pytest

from ai_3d_maker.errors import ConfirmationRequiredError, UnsafeGcodeError
from ai_3d_maker.gcode import scan_gcode
from ai_3d_maker.printer import (
    PhysicalAction,
    PhysicalRequest,
    Transport,
    confirmation_token,
    dry_run,
    execute_physical,
)
from conftest import SAFE_GCODE


def make_request(**over) -> PhysicalRequest:
    payload = {
        "action": PhysicalAction.START_PRINT,
        "job_id": "job1",
        "artifact_sha256": "a" * 64,
        "confirmation": "",
        "transport": Transport.SIMULATOR,
    }
    payload.update(over)
    return PhysicalRequest(**payload)


# --------------------------------------------------------------- confirmation
def test_physical_action_without_confirmation_is_refused():
    with pytest.raises(ConfirmationRequiredError) as exc:
        execute_physical(make_request(), allow_physical=True, scan=None)
    assert "expected_confirmation" in exc.value.detail


def test_confirmation_token_is_bound_to_the_job_and_artifact():
    a = confirmation_token("job1", "a" * 64)
    b = confirmation_token("job2", "a" * 64)
    c = confirmation_token("job1", "b" * 64)
    assert a != b != c and a != c
    assert a.startswith("PRINT-CONFIRM-")


def test_a_token_for_a_different_artifact_does_not_work():
    wrong = confirmation_token("job1", "b" * 64)
    with pytest.raises(ConfirmationRequiredError):
        execute_physical(make_request(confirmation=wrong), allow_physical=True, scan=None)


def test_correct_confirmation_on_the_simulator_still_touches_no_hardware():
    token = confirmation_token("job1", "a" * 64)
    result = execute_physical(make_request(confirmation=token), allow_physical=True, scan=None)
    assert result.status == "SIMULATED"
    assert result.performed_physical_action is False


# ---------------------------------------------------------------- gcode gate
def test_unsafe_gcode_can_never_be_sent(profile):
    scan = scan_gcode("M104 S350", profile)
    token = confirmation_token("job1", "a" * 64)
    with pytest.raises(UnsafeGcodeError):
        execute_physical(
            make_request(confirmation=token, transport=Transport.TF_CARD),
            allow_physical=True, scan=scan,
        )


def test_the_gcode_gate_runs_before_the_confirmation_gate(profile):
    """Even a perfectly confirmed job is refused when the G-code is unsafe."""
    scan = scan_gcode("M500", profile)
    with pytest.raises(UnsafeGcodeError):
        execute_physical(make_request(confirmation="whatever"), allow_physical=True, scan=scan)


# ------------------------------------------------------------------- config
def test_hardware_transport_is_refused_when_the_config_disables_it(tmp_path):
    token = confirmation_token("job1", "a" * 64)
    with pytest.raises(ConfirmationRequiredError, match="AI3D_ALLOW_PHYSICAL_PRINT"):
        execute_physical(
            make_request(confirmation=token, transport=Transport.TF_CARD),
            allow_physical=False, scan=None, media_dir=str(tmp_path),
        )


def test_tf_card_refuses_to_start_a_print(tmp_path):
    token = confirmation_token("job1", "a" * 64)
    result = execute_physical(
        make_request(confirmation=token, transport=Transport.TF_CARD, action=PhysicalAction.START_PRINT),
        allow_physical=True, scan=None, media_dir=str(tmp_path),
    )
    assert result.status == "REFUSED"
    assert result.performed_physical_action is False
    assert "from its own screen by a person" in result.message


def test_tf_card_refuses_to_preheat(tmp_path):
    token = confirmation_token("job1", "a" * 64)
    result = execute_physical(
        make_request(confirmation=token, transport=Transport.TF_CARD, action=PhysicalAction.PREHEAT),
        allow_physical=True, scan=None, media_dir=str(tmp_path),
    )
    assert result.status == "REFUSED"


def test_tf_card_transfer_copies_the_file_when_everything_is_satisfied(tmp_path, profile):
    source = tmp_path / "model.gcode"
    source.write_text(SAFE_GCODE, encoding="utf-8")
    media = tmp_path / "media"
    media.mkdir()
    token = confirmation_token("job1", "a" * 64)
    result = execute_physical(
        make_request(
            confirmation=token, transport=Transport.TF_CARD,
            action=PhysicalAction.TRANSFER_TO_MEDIA, artifact_path=source,
        ),
        allow_physical=True, scan=scan_gcode(SAFE_GCODE, profile), media_dir=str(media),
    )
    assert result.status == "DONE"
    assert result.performed_physical_action is True
    assert (media / "model.gcode").read_text(encoding="utf-8") == SAFE_GCODE


def test_transfer_without_mounted_media_is_refused(tmp_path):
    token = confirmation_token("job1", "a" * 64)
    result = execute_physical(
        make_request(
            confirmation=token, transport=Transport.TF_CARD,
            action=PhysicalAction.TRANSFER_TO_MEDIA,
        ),
        allow_physical=True, scan=None, media_dir=str(tmp_path / "not-mounted"),
    )
    assert result.status == "REFUSED"
    assert result.performed_physical_action is False


def test_usb_serial_is_blocked_by_hardware():
    token = confirmation_token("job1", "a" * 64)
    result = execute_physical(
        make_request(confirmation=token, transport=Transport.USB_SERIAL),
        allow_physical=True, scan=None,
    )
    assert result.status == "BLOCKED_BY_HARDWARE"
    assert result.performed_physical_action is False
    assert "never been exercised against the physical machine" in result.message


# ------------------------------------------------------------------ dry run
def test_dry_run_reports_filament_and_temperatures(profile):
    scan = scan_gcode(SAFE_GCODE, profile)
    report = dry_run(SAFE_GCODE, profile, scan)
    assert report.status == "SIMULATED"
    assert report.extrusion_moves == 3
    assert report.filament_mm == pytest.approx(3.6)
    assert report.max_nozzle_target_c == 205
    assert report.max_bed_target_c == 60
    assert report.within_envelope


def test_dry_run_counts_layer_markers(profile):
    text = ";LAYER:0\nG1 X1 Y1 E1\n;LAYER:1\nG1 X2 Y2 E2\n"
    scan = scan_gcode(text, profile)
    assert dry_run(text, profile, scan).layers == 2


def test_dry_run_flags_gcode_that_failed_the_scan(profile):
    text = "M104 S400\n"
    scan = scan_gcode(text, profile)
    report = dry_run(text, profile, scan)
    assert not report.within_envelope
    assert any("must not be sent" in n for n in report.notes)


def test_dry_run_never_reports_a_physical_action(profile):
    scan = scan_gcode(SAFE_GCODE, profile)
    assert dry_run(SAFE_GCODE, profile, scan).as_dict()["status"] == "SIMULATED"


@pytest.mark.skip(reason="BLOCKED BY HARDWARE: no ELEGOO Neptune 3 Plus is attached to this host")
def test_physical_print_smoke_on_real_neptune_3_plus():
    """Never run in CI. Requires the physical machine, filament and a human present."""
    raise AssertionError("this test must only ever run with the real printer and a human watching")


# ------------------------------------------------------------- units in dry run
def test_dry_run_reports_filament_in_millimetres_in_inch_mode(profile):
    """G20 makes every E value an inch; 1 inch of filament is 25.4 mm."""
    text = "G20\nG90\nM82\nG28\nG92 E0\nG1 X1 Y1 Z0.008 E1\n"
    scan = scan_gcode(text, profile)
    report = dry_run(text, profile, scan)
    assert report.filament_mm == pytest.approx(25.4)


# ------------------------------------------- gcode may never be sent unscanned
def test_gcode_artifact_cannot_be_transferred_without_a_scan(tmp_path):
    """`gcode_scanned_before_any_transfer` in app.manifest.yaml must be true in code."""
    source = tmp_path / "model.gcode"
    source.write_text(SAFE_GCODE, encoding="utf-8")
    media = tmp_path / "media"
    media.mkdir()
    token = confirmation_token("job1", "a" * 64)
    with pytest.raises(UnsafeGcodeError, match="not been scanned"):
        execute_physical(
            make_request(
                confirmation=token, transport=Transport.TF_CARD,
                action=PhysicalAction.TRANSFER_TO_MEDIA, artifact_path=source,
            ),
            allow_physical=True, scan=None, media_dir=str(media),
        )
    assert not (media / "model.gcode").exists()


def test_an_unscanned_gcode_file_under_a_disguised_extension_is_still_refused(tmp_path):
    """Renaming model.gcode to model.gco must not skip the scan."""
    source = tmp_path / "model.gco"
    source.write_text(SAFE_GCODE, encoding="utf-8")
    media = tmp_path / "media"
    media.mkdir()
    token = confirmation_token("job1", "a" * 64)
    with pytest.raises(UnsafeGcodeError):
        execute_physical(
            make_request(
                confirmation=token, transport=Transport.TF_CARD,
                action=PhysicalAction.TRANSFER_TO_MEDIA, artifact_path=source,
            ),
            allow_physical=True, scan=None, media_dir=str(media),
        )


def test_an_stl_artifact_still_transfers_without_a_gcode_scan(tmp_path):
    """The scan requirement applies to machine instructions, not to geometry."""
    source = tmp_path / "model.stl"
    source.write_bytes(b"solid x\nendsolid x\n")
    media = tmp_path / "media"
    media.mkdir()
    token = confirmation_token("job1", "a" * 64)
    result = execute_physical(
        make_request(
            confirmation=token, transport=Transport.TF_CARD,
            action=PhysicalAction.TRANSFER_TO_MEDIA, artifact_path=source,
        ),
        allow_physical=True, scan=None, media_dir=str(media),
    )
    assert result.status == "DONE"
