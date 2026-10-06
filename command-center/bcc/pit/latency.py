"""Small latency aggregates for Jeff status: no text, no identities, bounded memory."""
from __future__ import annotations

import math
import threading
from collections import deque


class LatencyStats:
    """Rolling window of millisecond samples with nearest-rank percentiles."""

    def __init__(self, maxlen: int = 200):
        self._values: deque[int] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.count = 0
        self.failures = 0
        self.last_ms: int | None = None

    def add(self, ms: float, *, ok: bool = True) -> None:
        with self._lock:
            self.count += 1
            if not ok:
                self.failures += 1
                return
            self.last_ms = max(0, int(ms))
            self._values.append(self.last_ms)

    def percentile(self, q: float) -> int | None:
        with self._lock:
            values = sorted(self._values)
        if not values:
            return None
        return values[min(len(values) - 1, max(0, math.ceil(q * len(values)) - 1))]

    def snapshot(self) -> dict:
        return {"count": self.count, "failures": self.failures, "last_ms": self.last_ms,
                "p50_ms": self.percentile(0.5), "p95_ms": self.percentile(0.95)}


class ReplyMetrics:
    """Jeff reply latency: time to first visible text and total, per route kind."""

    def __init__(self):
        self.ttft = {"local": LatencyStats(), "remote": LatencyStats()}
        self.total = {"local": LatencyStats(), "remote": LatencyStats()}
        self.streamed = 0
        self.replies = 0

    def record(self, route: str, *, ttft_ms: float, total_ms: float, streamed: bool) -> None:
        kind = "local" if route == "local" else "remote"
        self.ttft[kind].add(ttft_ms)
        self.total[kind].add(total_ms)
        self.replies += 1
        self.streamed += 1 if streamed else 0

    def snapshot(self) -> dict:
        return {"replies": self.replies, "streamed": self.streamed,
                **{kind: {"ttft": self.ttft[kind].snapshot(), "total": self.total[kind].snapshot()}
                   for kind in ("local", "remote")}}
