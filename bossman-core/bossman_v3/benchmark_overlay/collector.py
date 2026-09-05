from __future__ import annotations

from collections import defaultdict

from .models import BenchmarkEvent


class BenchmarkCollector:
    """Пассивный коллектор: копит события, никогда не трогает состояние миссии."""

    def __init__(self) -> None:
        self._events: list[BenchmarkEvent] = []

    def record(self, event: BenchmarkEvent) -> None:
        self._events.append(event)

    def extend(self, events) -> None:
        for e in events:
            self.record(e)

    def by_mission(self) -> dict[str, list[BenchmarkEvent]]:
        out: dict[str, list[BenchmarkEvent]] = defaultdict(list)
        for e in self._events:
            out[e.mission_id].append(e)
        return dict(out)

    def all(self) -> list[BenchmarkEvent]:
        return list(self._events)
