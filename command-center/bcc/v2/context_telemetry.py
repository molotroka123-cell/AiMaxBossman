"""Context/Model телеметрия (spec Part M) — метрики компиляции контекста и
состояния working state через EventBus. Именами метрик соответствуют spec §58.

Телеметрия никогда не роняет работу вызывающего кода: bus=None — честный no-op
(False), но если emit бросил исключение — оно пробрасывается дальше (ничего
не глотаем молча).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class ContextCompileTelemetry:
    """Метрики компиляции контекста (spec §58): budget/candidates/selected/
    dropped/mandatory/estimated/density/dedup/scope/temporal/latency/model."""
    task_id: str = ""
    version: str = "v2"
    token_budget: int = 0
    candidates: int = 0
    selected: int = 0
    dropped: int = 0
    mandatory_count: int = 0
    estimated_tokens: int = 0
    density: float = 0.0
    dedup_drops: int = 0
    scope_denials: int = 0
    temporal_drops: int = 0
    latency_ms: float = 0.0
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


async def emit_compile_telemetry(bus: Any, t: ContextCompileTelemetry) -> bool:
    """bus.emit("llm_arch.v2.context_compile", **t.to_dict()).
    bus=None → False (честный no-op). Исключения emit НЕ глотаются."""
    if bus is None:
        return False
    await bus.emit("llm_arch.v2.context_compile", **t.to_dict())
    return True


@dataclass(slots=True)
class WorkingStateTelemetry:
    checkpoints: int = 0
    conflicts: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


async def emit_working_state_telemetry(bus: Any, t: WorkingStateTelemetry) -> bool:
    """bus.emit("llm_arch.v2.working_state", **t.to_dict()). bus=None → False."""
    if bus is None:
        return False
    await bus.emit("llm_arch.v2.working_state", **t.to_dict())
    return True


@dataclass(slots=True)
class TaskGraphTelemetry:
    nodes: int = 0
    ready_nodes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
