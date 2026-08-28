"""Privacy posture: closed by default, and the switches actually switch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.crm.clients import DisabledCrm, HttpCrm, MockCrm
from ai_webcam_vision.errors import EgressBlocked, PrivacyDenied
from ai_webcam_vision.pipeline.snapshots import SnapshotStore
from ai_webcam_vision.runtime.service import VisionService
from ai_webcam_vision.secretstore import Secret
from ai_webcam_vision.transport.mock import SyntheticFrameSource


def test_recording_and_snapshots_are_off_by_default(settings):
    assert settings.privacy.recording_enabled is False
    assert settings.privacy.snapshots_enabled is False
    assert settings.privacy.telemetry_enabled is False


async def test_snapshot_capture_is_refused_when_disabled(settings):
    service = VisionService(settings, source=SyntheticFrameSource())
    try:
        with pytest.raises(PrivacyDenied):
            await service.capture_snapshot()
        assert service.snapshots.enabled is False
    finally:
        await service.aclose()


async def test_no_snapshot_files_are_written_during_a_normal_sample(settings):
    source = SyntheticFrameSource()
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        assert not settings.snapshot_dir.exists() or list(settings.snapshot_dir.iterdir()) == []
        written = [p.name for p in settings.state_dir.rglob("*") if p.is_file()]
        assert not any(name.endswith((".jpg", ".jpeg", ".png", ".mp4", ".mkv")) for name in written), written
    finally:
        await service.aclose()


async def test_enabled_snapshot_is_small_grayscale_and_owner_only(
    tmp_path: Path, ffmpeg_path: str, video_fixture: Path
):
    settings = Settings.from_env({
        "AWV_CAMERA_MODE": "file",
        "AWV_CAMERA_FIXTURE": str(video_fixture),
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_SNAPSHOTS_ENABLED": "true",
        "AWV_SNAPSHOT_MAX_WIDTH": "96",
        "AWV_SNAPSHOT_BLUR_SIGMA": "8",
        "AWV_SNAPSHOT_RETENTION": "2",
    })
    service = VisionService(settings)
    try:
        result = await service.capture_snapshot()
        path = Path(result["path"])
        assert path.exists()
        assert oct(path.stat().st_mode)[-3:] == "600"
        assert oct(settings.snapshot_dir.stat().st_mode)[-3:] == "700"
        assert result["bytes"] < 20_000
        assert result["grayscale"] is True
        assert result["blur_sigma"] == 8.0

        # Retention keeps the directory from growing without bound.
        for _ in range(3):
            await service.capture_snapshot()
        assert len(service.snapshots.list()) <= 2
    finally:
        await service.aclose()


async def test_mock_source_refuses_to_fabricate_a_snapshot():
    source = SyntheticFrameSource()
    with pytest.raises(Exception) as excinfo:
        await source.grab_snapshot_jpeg(160, 4.0)
    assert "does not produce snapshots" in str(excinfo.value)


async def test_disabled_crm_makes_no_call_and_says_it_is_absent():
    crm = DisabledCrm()
    context = await crm.context("room", __import__("datetime").datetime.now())
    assert context.available is False
    assert context.is_mock is True
    assert crm.descriptor.kind == "disabled"


async def test_http_crm_refuses_to_transmit_when_egress_is_disabled():
    crm = HttpCrm("https://crm.invalid", Secret("token-value-123"), timeout=1, egress_enabled=False)
    with pytest.raises(EgressBlocked):
        await crm.context("room", __import__("datetime").datetime.now())


async def test_mock_crm_is_always_flagged_as_mock():
    from ai_webcam_vision.crm.base import CrmContext

    crm = MockCrm([CrmContext(available=True, source="scripted", appointment_active=True)])
    context = await crm.context("room", __import__("datetime").datetime.now())
    assert context.is_mock is True
    assert crm.descriptor.is_mock is True


async def test_default_run_opens_no_outbound_socket(settings, monkeypatch):
    """A default deployment must not send anything anywhere."""
    import socket

    real_connect = socket.socket.connect
    attempts: list[object] = []

    def guard(self, address):  # noqa: ANN001
        attempts.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard)

    source = SyntheticFrameSource()
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        service.health()
        service.capabilities()
    finally:
        await service.aclose()

    external = [a for a in attempts if not (isinstance(a, tuple) and a[0] in {"127.0.0.1", "::1", "localhost"})]
    assert not external, f"unexpected outbound connections: {external}"


def test_snapshot_policy_is_described_honestly(settings):
    store = SnapshotStore(settings.snapshot_dir, settings.privacy)
    policy = store.policy()
    assert policy["enabled"] is False
    assert policy["grayscale"] is True
    assert policy["max_width"] == 160


async def test_capabilities_list_denied_features(settings):
    service = VisionService(settings, source=SyntheticFrameSource())
    try:
        denied = service.capabilities()["privacy"]["denied_by_design"]
        assert "face_identification" in denied
        assert "audio_capture" in denied
        assert "raw_video_retention" in denied
    finally:
        await service.aclose()
