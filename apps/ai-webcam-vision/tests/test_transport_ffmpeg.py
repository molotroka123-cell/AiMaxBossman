"""The transport boundary, exercised against real ffmpeg.

There is no Tapo C200 here, so the camera itself is BLOCKED BY HARDWARE. What
is fully exercised is everything between this process and the camera: process
spawn, argument construction, timeouts, kill-on-timeout, stderr handling,
frame geometry and failure classification.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.errors import CaptureError, CaptureTimeout, DependencyMissing
from ai_webcam_vision.transport import build_source
from ai_webcam_vision.transport.base import SourceKind
from ai_webcam_vision.transport.ffmpeg import FfmpegFrameSource, FfmpegRunner

from conftest import closed_port


def file_settings(fixture: Path, tmp_path: Path, ffmpeg_path: str, **extra) -> Settings:
    env = {
        "AWV_CAMERA_MODE": "file",
        "AWV_CAMERA_FIXTURE": str(fixture),
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_CAPTURE_TIMEOUT_SECONDS": "30",
        "AWV_CONNECT_TIMEOUT_SECONDS": "30",
    }
    env.update(extra)
    return Settings.from_env(env)


def test_runner_reports_missing_binary_honestly(tmp_path):
    runner = FfmpegRunner(str(tmp_path / "definitely-not-ffmpeg"))
    info = runner.info()
    assert info.available is False
    assert info.path is None
    assert "not found" in (info.reason or "")
    with pytest.raises(DependencyMissing):
        runner.require()


def test_runner_reports_real_binary(ffmpeg_runner):
    info = ffmpeg_runner.info(refresh=True)
    assert info.available is True
    assert info.version and "ffmpeg" in info.version.lower()


async def test_probe_succeeds_against_fixture(video_fixture, tmp_path, ffmpeg_path):
    source = build_source(file_settings(video_fixture, tmp_path, ffmpeg_path))
    result = await source.probe()
    assert result.ok is True
    assert result.descriptor.kind is SourceKind.FILE_FIXTURE
    assert result.descriptor.uses_real_transport is True
    assert result.descriptor.is_mock_camera is True
    assert result.latency_ms is not None


async def test_grab_returns_exact_frame_geometry(video_fixture, tmp_path, ffmpeg_path):
    settings = file_settings(video_fixture, tmp_path, ffmpeg_path,
                             AWV_FRAME_WIDTH="160", AWV_FRAME_HEIGHT="90")
    source = build_source(settings)
    frame = await source.grab()
    assert frame.width == 160 and frame.height == 90
    assert frame.nbytes == 160 * 90
    assert frame.source_kind is SourceKind.FILE_FIXTURE
    second = await source.grab()
    assert second.seq == frame.seq + 1


async def test_missing_input_file_is_a_capture_error(tmp_path, ffmpeg_path):
    settings = file_settings(tmp_path / "no-such-file.mp4", tmp_path, ffmpeg_path)
    source = build_source(settings)
    with pytest.raises(CaptureError) as excinfo:
        await source.grab()
    assert excinfo.value.code == "capture_failed"
    result = await source.probe()
    assert result.ok is False


async def test_refused_rtsp_endpoint_fails_fast_and_scrubbed(tmp_path, ffmpeg_path):
    settings = Settings.from_env({
        "AWV_CAMERA_MODE": "rtsp",
        "AWV_CAMERA_HOST": "127.0.0.1",
        "AWV_CAMERA_PORT": str(closed_port()),
        "AWV_CAMERA_USERNAME": "probe_user",
        "AWV_CAMERA_PASSWORD": "RefusedEndpointProbe",
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_CONNECT_TIMEOUT_SECONDS": "5",
        "AWV_CAPTURE_TIMEOUT_SECONDS": "10",
    })
    source = build_source(settings)
    assert source.descriptor.is_mock_camera is False
    assert "***:***@" in source.descriptor.target
    with pytest.raises(CaptureError) as excinfo:
        await source.grab()
    assert "RefusedEndpointProbe" not in str(excinfo.value)


async def test_capture_timeout_kills_the_child(tmp_path, ffmpeg_path):
    """A hung ffmpeg must never outlive the call that started it."""
    runner = FfmpegRunner(ffmpeg_path)
    started = time.monotonic()
    with pytest.raises(CaptureTimeout):
        # An endless generated stream: it will not finish on its own.
        await runner.run(
            ["-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x180:rate=25", "-f", "null", "-"],
            timeout=1.0,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 10, "the timeout did not stop the process promptly"

    await asyncio.sleep(0.2)
    try:
        import psutil

        children = psutil.Process().children(recursive=True)
        alive = [c for c in children if c.is_running() and c.status() != psutil.STATUS_ZOMBIE]
        assert not alive, f"ffmpeg survived the timeout: {alive}"
    except ImportError:  # pragma: no cover - psutil is a dev dependency
        pytest.skip("psutil unavailable; cannot assert the child was reaped")


async def test_short_frame_is_rejected(video_fixture, tmp_path, ffmpeg_path):
    """A truncated payload must be an error, not a silently padded frame."""
    settings = file_settings(video_fixture, tmp_path, ffmpeg_path)
    source = build_source(settings)
    assert isinstance(source, FfmpegFrameSource)

    class TruncatingRunner(FfmpegRunner):
        async def run(self, args, *, timeout, url=None, expect_stdout=True):  # noqa: ANN001
            data = await FfmpegRunner.run(self, args, timeout=timeout, url=url, expect_stdout=expect_stdout)
            return data[:10]

    truncated = build_source(settings, TruncatingRunner(ffmpeg_path))
    with pytest.raises(CaptureError) as excinfo:
        await truncated.grab()
    assert "short frame" in str(excinfo.value)


async def test_snapshot_is_downscaled_grayscale_jpeg(video_fixture, tmp_path, ffmpeg_path):
    settings = file_settings(video_fixture, tmp_path, ffmpeg_path)
    source = build_source(settings)
    payload = await source.grab_snapshot_jpeg(max_width=64, blur_sigma=8.0)
    assert payload[:2] == b"\xff\xd8", "not a JPEG"
    assert len(payload) < 20_000, "snapshot is far larger than a 64px still should be"
