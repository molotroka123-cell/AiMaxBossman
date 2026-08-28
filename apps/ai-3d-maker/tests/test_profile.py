"""Verified machine limits must survive loading untouched, and must never be
mixed with the app's own unverified process defaults."""

from __future__ import annotations

import json

import pytest

from ai_3d_maker.profile import PrinterProfile
from conftest import PROFILE_PATH


def test_verified_limits_match_manufacturer_data(profile):
    assert (profile.build_x, profile.build_y, profile.build_z) == (320.0, 320.0, 400.0)
    assert (profile.verified.platform_x_mm, profile.verified.platform_y_mm) == (330.0, 330.0)
    assert profile.nozzle == 0.4
    assert profile.max_nozzle_temp == 260
    assert profile.max_bed_temp == 100
    assert profile.technology == "FDM"


def test_transfer_methods_are_tf_card_and_usb_only(profile):
    assert set(profile.transfer) == {"TF card", "USB cable"}


def test_verified_and_unverified_stay_separate(profile):
    dumped = profile.as_dict()
    assert set(dumped["verified_machine_limits"]) & set(dumped["process_defaults_unverified"]) == set()
    assert "nominal_layer_height_mm" not in dumped["verified_machine_limits"]
    assert "max_nozzle_temp_c" not in dumped["process_defaults_unverified"]
    assert profile.process_defaults_unverified.note


def test_round_trip_preserves_the_two_groups(profile, tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile.as_dict()), encoding="utf-8")
    again = PrinterProfile.load(path)
    assert again.verified == profile.verified
    assert again.process_defaults_unverified == profile.process_defaults_unverified


def test_usable_volume_applies_the_fit_margin(profile):
    ux, uy, uz = profile.usable_xyz()
    assert ux == pytest.approx(316.0)
    assert uy == pytest.approx(316.0)
    assert uz == pytest.approx(400.0)


def test_profile_without_verified_limits_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"id": "x", "model": "y"}), encoding="utf-8")
    with pytest.raises(ValueError, match="verified_machine_limits"):
        PrinterProfile.load(path)


def test_seed_profile_file_still_declares_both_sections():
    raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    assert "verified_machine_limits" in raw
    assert "process_defaults_unverified" in raw
