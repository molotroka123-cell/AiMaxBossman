"""Frame transports: real ffmpeg, file fixture, synthetic mock."""

from __future__ import annotations

from ..config import CameraMode, Settings
from ..secretstore import SecretUrl, StreamTarget, build_stream_url
from .base import Frame, FrameSource, ProbeResult, SourceDescriptor, SourceKind
from .ffmpeg import FfmpegFrameSource, FfmpegInfo, FfmpegRunner
from .mock import FaultScript, SyntheticFrameSource, SyntheticScene
from .retry import RetryStats, backoff_delays, with_retry

__all__ = [
    "CameraMode",
    "FaultScript",
    "FfmpegFrameSource",
    "FfmpegInfo",
    "FfmpegRunner",
    "Frame",
    "FrameSource",
    "ProbeResult",
    "RetryStats",
    "SourceDescriptor",
    "SourceKind",
    "SyntheticFrameSource",
    "SyntheticScene",
    "backoff_delays",
    "build_source",
    "with_retry",
]


def _rtsp_url_provider(settings: Settings):
    target = StreamTarget(
        scheme="rtsp",
        host=settings.camera_host,
        port=settings.camera_port,
        path=settings.camera_stream,
    )

    def provider() -> SecretUrl:
        return build_stream_url(target, settings.camera_username, settings.camera_password)

    return provider


def _file_url_provider(settings: Settings):
    path = settings.camera_fixture

    def provider() -> SecretUrl:
        # A local file carries no credentials; it is still routed through the
        # single URL assembly point so that nothing else builds source strings.
        value = str(path)
        return SecretUrl(value, value, label="fixture_path")

    return provider


def build_source(settings: Settings, runner: FfmpegRunner | None = None) -> FrameSource:
    """Create the frame source the configuration asks for. No silent fallback.

    If a real camera is configured and ffmpeg is missing, the returned source
    reports the failure honestly instead of degrading to synthetic frames.
    """
    if settings.camera_mode is CameraMode.MOCK:
        return SyntheticFrameSource(width=settings.frame_width, height=settings.frame_height)

    runner = runner or FfmpegRunner(settings.ffmpeg_path)

    if settings.camera_mode is CameraMode.RTSP:
        descriptor = SourceDescriptor(
            kind=SourceKind.RTSP_CAMERA,
            is_mock_camera=False,
            uses_real_transport=True,
            target=f"rtsp://***:***@{settings.camera_host}:{settings.camera_port}/{settings.camera_stream}",
            detail="physical camera over RTSP",
        )
        provider = _rtsp_url_provider(settings)
    else:
        descriptor = SourceDescriptor(
            kind=SourceKind.FILE_FIXTURE,
            is_mock_camera=True,
            uses_real_transport=True,
            target=f"file://{settings.camera_fixture}",
            detail="recorded video fixture decoded by the real ffmpeg transport",
        )
        provider = _file_url_provider(settings)

    return FfmpegFrameSource(
        url_provider=provider,
        runner=runner,
        descriptor=descriptor,
        width=settings.frame_width,
        height=settings.frame_height,
        connect_timeout=settings.connect_timeout,
        capture_timeout=settings.capture_timeout,
    )
