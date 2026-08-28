"""Baseline/zone evidence.

Deliberately not machine learning: a documented pixel heuristic with named
thresholds. Calling it a model would be a lie, so ``capabilities`` reports it
as ``heuristic_pixel_baseline``.

Everything happens on grayscale frames already downscaled by ffmpeg (default
160x90). At that resolution a face is a few pixels wide, which is a privacy
property, not an accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from ..errors import BaselineMissing, VisionError
from ..transport.base import Frame

#: Per-pixel intensity difference above which a pixel counts as "changed".
PIXEL_DELTA = 24

ANALYZER_ID = "heuristic_pixel_baseline"
ANALYZER_VERSION = "1"


@dataclass(frozen=True)
class Evidence:
    ts: datetime
    room_change: float
    chair_change: float
    work_motion: float
    motion_gate: bool
    frame_seq: int

    def to_dict(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "room_change": round(self.room_change, 5),
            "chair_change": round(self.chair_change, 5),
            "work_motion": round(self.work_motion, 5),
            "motion_gate": self.motion_gate,
            "frame_seq": self.frame_seq,
        }


def frame_to_array(frame: Frame) -> np.ndarray:
    expected = frame.width * frame.height
    if len(frame.data) != expected:
        raise VisionError(f"frame payload is {len(frame.data)} bytes, expected {expected}")
    return np.frombuffer(frame.data, dtype=np.uint8).reshape(frame.height, frame.width)


def crop(array: np.ndarray, zone: tuple[float, float, float, float]) -> np.ndarray:
    h, w = array.shape[:2]
    x1, y1, x2, y2 = zone
    left, right = int(x1 * w), max(int(x2 * w), int(x1 * w) + 1)
    top, bottom = int(y1 * h), max(int(y2 * h), int(y1 * h) + 1)
    view = array[top:bottom, left:right]
    if view.size == 0:  # pragma: no cover - guarded by config validation
        raise VisionError("zone crop is empty")
    return view


def changed_fraction(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        raise VisionError(f"cannot compare shapes {a.shape} and {b.shape}")
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(np.count_nonzero(diff > PIXEL_DELTA) / diff.size)


class BaselineStore:
    """The empty-room reference, kept as a downscaled grayscale array.

    Stored as ``.npy`` rather than a viewable photograph: it is analysis data,
    not an image of the room to be browsed.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cache: np.ndarray | None = None

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def save(self, frame: Frame) -> np.ndarray:
        array = frame_to_array(frame)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "wb") as handle:
            np.save(handle, array)
        self.path.chmod(0o600)
        self._cache = array
        return array

    def load(self) -> np.ndarray:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            raise BaselineMissing("capture an empty-room baseline first")
        self._cache = np.load(self.path)
        return self._cache

    def clear_cache(self) -> None:
        self._cache = None


class Analyzer:
    def __init__(
        self,
        baseline: BaselineStore,
        chair_zone: tuple[float, float, float, float],
        work_zone: tuple[float, float, float, float],
    ) -> None:
        self.baseline = baseline
        self.chair_zone = chair_zone
        self.work_zone = work_zone
        self._previous: np.ndarray | None = None

    def reset(self) -> None:
        self._previous = None

    def analyze(self, frame: Frame, motion_gate: bool) -> Evidence:
        current = frame_to_array(frame)
        reference = self.baseline.load()
        if reference.shape != current.shape:
            raise VisionError(
                "baseline resolution does not match the current frame; recapture the baseline"
            )
        room = changed_fraction(current, reference)
        chair = changed_fraction(crop(current, self.chair_zone), crop(reference, self.chair_zone))
        if self._previous is None:
            work = 0.0
        else:
            work = changed_fraction(crop(current, self.work_zone), crop(self._previous, self.work_zone))
        self._previous = current
        return Evidence(
            ts=frame.ts,
            room_change=room,
            chair_change=chair,
            work_motion=work,
            motion_gate=motion_gate,
            frame_seq=frame.seq,
        )
