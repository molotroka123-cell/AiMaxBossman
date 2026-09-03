"""In-process event bus. Bound to 127.0.0.1 app. Nothing leaves the machine."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Any


Handler = Callable[[str, dict], None]


class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._all: list[Handler] = []

    def on(self, topic: str, fn: Handler) -> None:
        self._subs[topic].append(fn)

    def on_any(self, fn: Handler) -> None:
        self._all.append(fn)

    def emit(self, topic: str, payload: dict | None = None) -> dict:
        data: dict[str, Any] = dict(payload or {})
        for fn in list(self._all):
            fn(topic, data)
        for fn in list(self._subs.get(topic, ())):
            fn(topic, data)
        return data
