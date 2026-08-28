"""Transport contracts shared by every frame source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, runtime_checkable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceKind(StrEnum):
    """What is actually on the other end. Never inferred, never fuzzy."""

    RTSP_CAMERA = "rtsp_camera"      # real camera, real network, real ffmpeg
    FILE_FIXTURE = "file_fixture"    # local video file, real ffmpeg, no camera
    SYNTHETIC = "synthetic"          # generated pixels, no transport at all


@dataclass(frozen=True)
class SourceDescriptor:
    kind: SourceKind
    #: True whenever the pixels do not come from a physical camera.
    is_mock_camera: bool
    #: True when a real ffmpeg process moves the bytes (fixture mode included).
    uses_real_transport: bool
    #: Credential-free rendering of the target.
    target: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "is_mock_camera": self.is_mock_camera,
            "uses_real_transport": self.uses_real_transport,
            "target": self.target,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Frame:
    """A single grayscale frame, already downscaled to analysis resolution."""

    seq: int
    ts: datetime
    width: int
    height: int
    data: bytes
    source_kind: SourceKind

    @property
    def nbytes(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    descriptor: SourceDescriptor
    latency_ms: float | None = None
    error_code: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "source": self.descriptor.to_dict(),
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
            "error": self.error,
        }


@runtime_checkable
class FrameSource(Protocol):
    descriptor: SourceDescriptor

    async def probe(self) -> ProbeResult: ...

    async def grab(self) -> Frame: ...

    async def grab_snapshot_jpeg(self, max_width: int, blur_sigma: float) -> bytes: ...

    async def aclose(self) -> None: ...
