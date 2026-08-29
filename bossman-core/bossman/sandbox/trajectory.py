"""Stage 8 — Trajectory Recorder (ядро).

Пишет метаданные жизненного цикла песочницы: состояния, вызовы инструментов,
shell-действия, allow/deny сети, approvals, ресурсные события, артефакты, сбои,
результаты тестов. Секреты НЕ логируются — каждое событие проходит через
obs.redact (Bearer/api_key/token/… → «REDACTED») и вычистку по ключам.
Хранилище — append-only JSONL под workspace (не в git); никакого второго стора.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .. import obs

# Категории событий траектории.
TRAJECTORY_KINDS = frozenset({
    "lifecycle", "tool_call", "shell", "network", "approval",
    "resource", "artifact", "failure", "test_result",
})


class TrajectoryRecorder:
    def __init__(self, sandbox_id: str, sink_path: str | Path | None = None) -> None:
        self.sandbox_id = sandbox_id
        self.sink_path = Path(sink_path) if sink_path else None
        if self.sink_path:
            self.sink_path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict[str, Any]] = []

    def record(self, kind: str, **data: Any) -> dict[str, Any]:
        if kind not in TRAJECTORY_KINDS:
            raise ValueError(f"unknown trajectory kind: {kind}")
        # Вычистка секретов по значению И по имени ключа перед записью.
        safe = obs.redact_obj(data)
        event = {
            "ts": time.time(),
            "sandbox_id": self.sandbox_id,
            "kind": kind,
            **safe,
        }
        self.events.append(event)
        if self.sink_path:
            with self.sink_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event

    # Удобные обёртки под обязательные категории.
    def lifecycle(self, state: str, note: str = "") -> None:
        self.record("lifecycle", state=state, note=note)

    def network(self, host: str, allowed: bool, reason: str) -> None:
        self.record("network", host=host, allowed=allowed, reason=reason)

    def resource(self, action: str, lease_id: str | None = None) -> None:
        self.record("resource", action=action, lease_id=lease_id)

    def failure(self, where: str, error: str) -> None:
        self.record("failure", where=where, error=error)

    def artifact(self, rel_path: str, sha256: str, quarantined: bool) -> None:
        self.record("artifact", rel_path=rel_path, sha256=sha256, quarantined=quarantined)
