"""Synthetic frame source.

This is a mock and says so in every descriptor it produces. It exists for two
purposes: running the whole pipeline without a camera, and injecting faults
deterministically.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from ..errors import CaptureError, CaptureTimeout, DependencyMissing
from .base import Frame, FrameSource, ProbeResult, SourceDescriptor, SourceKind, utcnow

MOCK_DESCRIPTOR = SourceDescriptor(
    kind=SourceKind.SYNTHETIC,
    is_mock_camera=True,
    uses_real_transport=False,
    target="synthetic://generated-frames",
    detail="deterministic generated pixels; no camera and no ffmpeg involved",
)


@dataclass
class SyntheticScene:
    """What the generated room looks like right now."""

    chair_occupied: bool = False
    room_activity: bool = False
    work_activity: bool = False
    #: Sensor noise amplitude, kept low so thresholds stay meaningful.
    noise: int = 1


@dataclass
class FaultScript:
    """A deterministic sequence of outcomes for :class:`SyntheticFrameSource`.

    Entries: ``"ok"``, ``"fail"``, ``"timeout"``, ``"dependency"``.
    """

    steps: list[str] = field(default_factory=list)
    default: str = "ok"
    _index: int = 0

    def next(self) -> str:
        if self._index < len(self.steps):
            step = self.steps[self._index]
            self._index += 1
            return step
        return self.default

    @property
    def consumed(self) -> int:
        return self._index


class SyntheticFrameSource(FrameSource):
    def __init__(
        self,
        *,
        width: int = 160,
        height: int = 90,
        scene: SyntheticScene | None = None,
        script: FaultScript | None = None,
        seed: int = 20260828,
    ) -> None:
        self.descriptor = MOCK_DESCRIPTOR
        self.scene = scene or SyntheticScene()
        self.script = script or FaultScript()
        self._width = width
        self._height = height
        self._rng = np.random.default_rng(seed)
        self._counter = itertools.count(1)
        self.closed = False
        self.grab_calls = 0

    # ------------------------------------------------------------- pixels
    def _render(self, seq: int) -> np.ndarray:
        h, w = self._height, self._width
        frame = np.full((h, w), 40, dtype=np.uint8)
        # Static furniture, present in the baseline as well.
        frame[int(h * 0.60):, :] = 55
        if self.scene.chair_occupied:
            y1, y2 = int(h * 0.25), int(h * 0.90)
            x1, x2 = int(w * 0.25), int(w * 0.78)
            frame[y1:y2, x1:x2] = 190
        if self.scene.room_activity:
            y1, y2 = int(h * 0.10), int(h * 0.55)
            x1, x2 = int(w * 0.02), int(w * 0.20)
            frame[y1:y2, x1:x2] = 170
        if self.scene.work_activity:
            # Moves with the sequence number so consecutive frames differ.
            offset = (seq * 7) % max(1, int(w * 0.30))
            y1, y2 = int(h * 0.20), int(h * 0.60)
            x1 = int(w * 0.35) + offset
            frame[y1:y2, x1:x1 + max(2, int(w * 0.10))] = 230
        if self.scene.noise:
            noise = self._rng.integers(0, self.scene.noise + 1, size=(h, w), dtype=np.uint8)
            frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        return frame

    def _apply_script(self) -> None:
        step = self.script.next()
        if step == "ok":
            return
        if step == "fail":
            raise CaptureError("injected transport failure")
        if step == "timeout":
            raise CaptureTimeout("injected capture timeout")
        if step == "dependency":
            raise DependencyMissing("injected missing dependency")
        raise ValueError(f"unknown fault step {step!r}")

    # ------------------------------------------------------------ protocol
    async def probe(self) -> ProbeResult:
        try:
            self._apply_script()
        except (CaptureError, DependencyMissing) as exc:
            return ProbeResult(False, self.descriptor, None, exc.code, exc.safe_message)
        return ProbeResult(True, self.descriptor, 0.0)

    async def grab(self) -> Frame:
        self.grab_calls += 1
        self._apply_script()
        seq = next(self._counter)
        return Frame(
            seq=seq,
            ts=utcnow(),
            width=self._width,
            height=self._height,
            data=self._render(seq).tobytes(),
            source_kind=self.descriptor.kind,
        )

    async def grab_snapshot_jpeg(self, max_width: int, blur_sigma: float) -> bytes:
        # A mock source must not fabricate a JPEG that could be mistaken for a
        # real still from a real room.
        raise CaptureError("synthetic source does not produce snapshots")

    async def aclose(self) -> None:
        self.closed = True
