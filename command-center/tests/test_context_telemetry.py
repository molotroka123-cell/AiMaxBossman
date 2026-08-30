"""Tests: context/model telemetry (spec Part M, §58)."""
from __future__ import annotations

import asyncio

import pytest

from bcc.v2.context_telemetry import (
    ContextCompileTelemetry,
    TaskGraphTelemetry,
    WorkingStateTelemetry,
    emit_compile_telemetry,
    emit_working_state_telemetry,
)


class FakeBus:
    def __init__(self) -> None:
        self.emits: list[tuple[str, dict]] = []

    async def emit(self, kind: str, /, **data: object) -> dict:
        self.emits.append((kind, data))
        return {"kind": kind, **data}


class ExplodingBus:
    async def emit(self, kind: str, /, **data: object) -> dict:
        raise RuntimeError("bus down")


def test_context_compile_to_dict_keys():
    t = ContextCompileTelemetry(task_id="t1", token_budget=8000, candidates=42,
                                selected=9, dropped=33, mandatory_count=3,
                                estimated_tokens=7600, density=0.82,
                                dedup_drops=4, scope_denials=1, temporal_drops=2,
                                latency_ms=12.5, model="qwen2.5-coder")
    d = t.to_dict()
    assert set(d) == {"task_id", "version", "token_budget", "candidates",
                      "selected", "dropped", "mandatory_count",
                      "estimated_tokens", "density", "dedup_drops",
                      "scope_denials", "temporal_drops", "latency_ms", "model"}
    assert d["version"] == "v2" and d["selected"] == 9


def test_working_state_and_task_graph_to_dict():
    ws = WorkingStateTelemetry(checkpoints=3, conflicts=1).to_dict()
    assert ws == {"checkpoints": 3, "conflicts": 1}
    tg = TaskGraphTelemetry(nodes=5, ready_nodes=2).to_dict()
    assert tg == {"nodes": 5, "ready_nodes": 2}


def test_emit_compile_telemetry_with_fake_bus():
    bus = FakeBus()
    t = ContextCompileTelemetry(task_id="t9", selected=3, dropped=5, model="m1")
    ok = asyncio.run(emit_compile_telemetry(bus, t))
    assert ok is True
    assert len(bus.emits) == 1
    kind, data = bus.emits[0]
    assert kind == "llm_arch.v2.context_compile"
    assert data["task_id"] == "t9"
    assert data["selected"] == 3 and data["dropped"] == 5
    assert data["model"] == "m1" and data["version"] == "v2"


def test_emit_working_state_telemetry_with_fake_bus():
    bus = FakeBus()
    ok = asyncio.run(emit_working_state_telemetry(
        bus, WorkingStateTelemetry(checkpoints=2, conflicts=1)))
    assert ok is True
    kind, data = bus.emits[0]
    assert kind == "llm_arch.v2.working_state"
    assert data == {"checkpoints": 2, "conflicts": 1}


def test_emit_with_none_bus_is_honest_noop():
    assert asyncio.run(emit_compile_telemetry(None, ContextCompileTelemetry())) is False
    assert asyncio.run(emit_working_state_telemetry(None, WorkingStateTelemetry())) is False


def test_emit_raises_propagate_not_swallowed():
    with pytest.raises(RuntimeError):
        asyncio.run(emit_compile_telemetry(ExplodingBus(), ContextCompileTelemetry()))


def test_emit_working_state_raises_propagate():
    with pytest.raises(RuntimeError):
        asyncio.run(emit_working_state_telemetry(ExplodingBus(), WorkingStateTelemetry()))
