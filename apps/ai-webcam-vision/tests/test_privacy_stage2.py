"""Privacy: a flag that does nothing is worse than no flag.

The denied capabilities already fail startup. The gap audited here is the
switch that is accepted, reported as enabled, and then quietly ignored — an
owner reading the API believes the clinic is recording when it is not, or the
reverse.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.errors import PrivacyDenied
from ai_webcam_vision.runtime.service import VisionService
from ai_webcam_vision.transport.mock import SyntheticFrameSource

ROOT = Path(__file__).resolve().parents[1]


def test_denied_capabilities_fail_startup_not_continue(base_env):
    for flag in ("AWV_CAPTURE_AUDIO", "AWV_FACE_IDENTIFICATION", "AWV_PATIENT_IDENTIFICATION"):
        with pytest.raises(PrivacyDenied):
            Settings.from_env(dict(base_env, **{flag: "true"}))


def test_a_denied_capability_makes_the_process_exit_nonzero(tmp_path):
    """Not an exception swallowed somewhere: the process must not start."""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(ROOT / "src"),
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_FACE_IDENTIFICATION": "true",
    }
    result = subprocess.run(
        [sys.executable, "-m", "ai_webcam_vision.main", "check"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode != 0, result.stdout
    assert json.loads(result.stderr)["code"] == "privacy_denied"


def test_audio_capture_also_refuses_to_start(tmp_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(ROOT / "src"),
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_CAPTURE_AUDIO": "true",
    }
    result = subprocess.run(
        [sys.executable, "-m", "ai_webcam_vision.main", "check"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode != 0
    assert json.loads(result.stderr)["code"] == "privacy_denied"


def test_requesting_recording_fails_instead_of_being_ignored(base_env):
    """There is no recorder. Accepting AWV_RECORDING_ENABLED=true and
    reporting it as enabled would be a lie the API tells for free."""
    with pytest.raises(PrivacyDenied):
        Settings.from_env(dict(base_env, AWV_RECORDING_ENABLED="true"))


def test_recording_stays_off_and_is_reported_off(settings):
    assert settings.privacy.recording_enabled is False
    assert settings.public_dict()["privacy"]["recording_enabled"] is False


async def test_defaults_are_closed_end_to_end(settings):
    service = VisionService(settings, source=SyntheticFrameSource())
    try:
        privacy = service.capabilities()["privacy"]
        assert privacy["recording_enabled"] is False
        assert privacy["snapshots_enabled"] is False
        assert privacy["telemetry_enabled"] is False
        assert privacy["crm_egress_enabled"] is False
        assert privacy["audio_capture"] is False
        assert privacy["face_identification"] is False
        assert privacy["patient_identification"] is False
        for denied in ("audio_capture", "face_identification",
                       "patient_identification_from_pixels", "raw_video_retention"):
            assert denied in privacy["denied_by_design"]
    finally:
        await service.aclose()


def test_no_face_or_audio_machinery_exists_in_the_source_tree():
    """Denied by design means there is nothing to switch on."""
    suspicious = (
        "face_recognition", "cv2.CascadeClassifier", "dlib", "insightface",
        "pyaudio", "sounddevice", "-f alsa", "-f pulse", "-i default",
    )
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in suspicious:
            if token in text:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, offenders


def test_ffmpeg_invocations_never_request_an_audio_stream():
    """Every ffmpeg call must be video-only by construction."""
    text = (ROOT / "src" / "ai_webcam_vision" / "transport" / "ffmpeg.py").read_text(encoding="utf-8")
    assert '"-an"' in text, "audio must be explicitly disabled, not merely unused"


async def test_no_audio_reaches_a_real_capture(ffmpeg_path, video_fixture, tmp_path):
    """A fixture that has an audio track: the capture must ignore it."""
    with_audio = tmp_path / "with-audio.mp4"
    subprocess.run(
        [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=5:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(with_audio)],
        check=True, timeout=120,
    )
    settings = Settings.from_env({
        "AWV_CAMERA_MODE": "file",
        "AWV_CAMERA_FIXTURE": str(with_audio),
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(tmp_path / "state"),
    })
    service = VisionService(settings)
    try:
        frame = await service._grab_with_retry()
        assert frame.nbytes == settings.frame_width * settings.frame_height
        written = [p for p in settings.state_dir.rglob("*") if p.is_file()]
        assert not any(p.suffix in {".wav", ".aac", ".mp3", ".m4a"} for p in written)
    finally:
        await service.aclose()


def test_the_manifest_permissions_match_the_code(base_env):
    import yaml

    manifest = yaml.safe_load((ROOT / "app.manifest.yaml").read_text(encoding="utf-8"))
    permissions = manifest["permissions"]
    assert permissions["audio.capture"] == "deny"
    assert permissions["face.identify"] == "deny"
    assert permissions["patient.identify"] == "deny"
    assert permissions["raw_video.store"] == "deny"
    # And "deny" is enforced, not decorative.
    for flag in ("AWV_CAPTURE_AUDIO", "AWV_FACE_IDENTIFICATION",
                 "AWV_PATIENT_IDENTIFICATION", "AWV_RECORDING_ENABLED"):
        with pytest.raises(PrivacyDenied):
            Settings.from_env(dict(base_env, **{flag: "true"}))
