from __future__ import annotations

import pytest

from ai_webcam_vision.config import CameraMode, CrmKind, Settings
from ai_webcam_vision.errors import ConfigError, PrivacyDenied


def test_environment_is_read_at_call_time_not_import_time(base_env):
    """The legacy pack bound env values when the module was imported."""
    env = dict(base_env)
    env["AWV_ROOM_ID"] = "room-a"
    assert Settings.from_env(env).room_id == "room-a"
    env["AWV_ROOM_ID"] = "room-b"
    assert Settings.from_env(env).room_id == "room-b"


def test_defaults_are_mock_and_closed(base_env):
    settings = Settings.from_env({"AWV_STATE_DIR": base_env["AWV_STATE_DIR"]})
    assert settings.camera_mode is CameraMode.MOCK
    assert settings.crm_kind is CrmKind.DISABLED
    assert settings.privacy.recording_enabled is False
    assert settings.privacy.snapshots_enabled is False
    assert settings.privacy.telemetry_enabled is False
    assert settings.privacy.crm_egress_enabled is False
    assert settings.host == "127.0.0.1"


def test_invalid_zone_is_a_config_error(base_env):
    env = dict(base_env, AWV_CHAIR_ZONE="0.9,0.1,0.2,0.8")
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_invalid_numbers_are_config_errors(base_env):
    with pytest.raises(ConfigError):
        Settings.from_env(dict(base_env, AWV_PORT="not-a-port"))
    with pytest.raises(ConfigError):
        Settings.from_env(dict(base_env, AWV_CAMERA_STREAM="stream9"))
    with pytest.raises(ConfigError):
        Settings.from_env(dict(base_env, AWV_RECORDING_ENABLED="maybe"))


def test_sample_rate_ceiling_is_enforced(base_env):
    env = dict(base_env, AWV_MAX_SAMPLE_RATE_HZ="1", AWV_ACTIVE_INTERVAL_SECONDS="0.1")
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_denied_capabilities_fail_startup(base_env):
    for flag in ("AWV_CAPTURE_AUDIO", "AWV_FACE_IDENTIFICATION", "AWV_PATIENT_IDENTIFICATION"):
        with pytest.raises(PrivacyDenied):
            Settings.from_env(dict(base_env, **{flag: "true"}))


def test_http_crm_requires_explicit_egress(base_env):
    env = dict(
        base_env,
        AWV_CRM_KIND="generic_http",
        AWV_CRM_BASE_URL="https://crm.internal",
    )
    with pytest.raises(ConfigError):
        Settings.from_env(env)
    settings = Settings.from_env(dict(env, AWV_CRM_EGRESS_ENABLED="true"))
    assert settings.uses_real_crm is True


def test_file_mode_requires_a_fixture(base_env):
    with pytest.raises(ConfigError):
        Settings.from_env(dict(base_env, AWV_CAMERA_MODE="file"))


def test_state_paths_live_under_state_dir(settings):
    assert settings.db_path.parent == settings.state_dir
    assert settings.baseline_path.parent == settings.state_dir
    assert settings.snapshot_dir.parent == settings.state_dir
