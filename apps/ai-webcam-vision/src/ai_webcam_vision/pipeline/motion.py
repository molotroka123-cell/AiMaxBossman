"""Motion gate: the wake signal that decides the sampling rate."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class MotionState:
    active: bool
    source: str
    seconds_remaining: float
    triggers: int

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "source": self.source,
            "seconds_remaining": round(self.seconds_remaining, 2),
            "triggers": self.triggers,
        }


class MotionGate:
    """Holds the "sample often" window open for ``hold`` seconds.

    A vendor-neutral webhook (ONVIF bridge, NVR, edge script) calls
    :meth:`trigger`. New motion extends the window rather than restarting it
    from zero, which is what "motion continues" actually means.
    """

    def __init__(self, hold: float, clock: Callable[[], float] | None = None) -> None:
        self.hold = float(hold)
        self._clock = clock or time.monotonic
        self._until: float | None = None
        self._source = "none"
        self.triggers = 0

    def trigger(self, source: str = "webhook") -> None:
        self._until = self._clock() + self.hold
        self._source = (source or "webhook")[:80]
        self.triggers += 1

    def active(self) -> bool:
        return self._until is not None and self._clock() < self._until

    def reset(self) -> None:
        self._until = None
        self._source = "none"

    def state(self) -> MotionState:
        remaining = 0.0
        if self._until is not None:
            remaining = max(0.0, self._until - self._clock())
        return MotionState(self.active(), self._source, remaining, self.triggers)
