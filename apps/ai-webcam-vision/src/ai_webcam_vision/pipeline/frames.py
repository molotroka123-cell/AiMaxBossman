"""A frame buffer that cannot grow without bound.

Video pipelines die of unbounded queues: the producer is a network, the
consumer is CPU-bound, and the difference accumulates in RAM. Here the buffer
has a hard capacity in frames *and* a hard capacity in bytes; the oldest frame
is dropped and counted rather than allowed to accumulate.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .. transport.base import Frame


@dataclass
class QueueStats:
    capacity: int
    size: int
    bytes_retained: int
    max_bytes: int
    accepted: int
    dropped_oldest: int
    high_water_mark: int

    def to_dict(self) -> dict:
        return {
            "capacity": self.capacity,
            "size": self.size,
            "bytes_retained": self.bytes_retained,
            "max_bytes": self.max_bytes,
            "accepted": self.accepted,
            "dropped_oldest": self.dropped_oldest,
            "high_water_mark": self.high_water_mark,
        }


class BoundedFrameQueue:
    """Drop-oldest bounded queue. ``put`` never blocks and never grows."""

    def __init__(self, capacity: int, max_bytes: int | None = None) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._items: deque[Frame] = deque()
        self._capacity = capacity
        self._max_bytes = max_bytes if max_bytes is not None else capacity * 8 * 1024 * 1024
        self._bytes = 0
        self.accepted = 0
        self.dropped_oldest = 0
        self.high_water_mark = 0

    def __len__(self) -> int:
        return len(self._items)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def bytes_retained(self) -> int:
        return self._bytes

    def put(self, frame: Frame) -> int:
        """Insert ``frame``; returns how many frames were dropped to fit it."""
        dropped = 0
        self._items.append(frame)
        self._bytes += frame.nbytes
        self.accepted += 1
        while len(self._items) > self._capacity or self._bytes > self._max_bytes:
            if len(self._items) == 1:
                # One frame larger than the byte budget: keep it, it is the
                # only thing we have, but never keep two of them.
                break
            evicted = self._items.popleft()
            self._bytes -= evicted.nbytes
            self.dropped_oldest += 1
            dropped += 1
        self.high_water_mark = max(self.high_water_mark, len(self._items))
        return dropped

    def get(self) -> Frame | None:
        if not self._items:
            return None
        frame = self._items.popleft()
        self._bytes -= frame.nbytes
        return frame

    def latest(self) -> Frame | None:
        """Take the newest frame and discard the rest: for live analysis the
        stale frames are worthless, and keeping them is how memory grows."""
        if not self._items:
            return None
        frame = self._items.pop()
        self.clear()
        self._bytes = 0
        return frame

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def stats(self) -> QueueStats:
        return QueueStats(
            capacity=self._capacity,
            size=len(self._items),
            bytes_retained=self._bytes,
            max_bytes=self._max_bytes,
            accepted=self.accepted,
            dropped_oldest=self.dropped_oldest,
            high_water_mark=self.high_water_mark,
        )
