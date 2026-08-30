"""
trace_recorder.py — Self-Learning Orchestrator Layer 1
Records full execution traces (prompts, actions, results, cost, latency, errors)
into the traces table for downstream eval/improvement pipeline.
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import json

try:
    from ..db import get_db
except ImportError:
    get_db = None  # allow standalone import/testing


@dataclass
class TraceEvent:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = ""
    run_id: str = ""
    step_index: int = 0
    event_type: str = ""        # step | tool_call | tool_result | final | error
    prompt_snapshot: str = ""
    action: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[Any] = None
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class TraceRecorder:
    """
    Drop-in recorder. Attach to engine hooks:

        recorder = TraceRecorder(agent_id="my-agent", run_id=run_id)
        engine.on_step  = recorder.record_step
        engine.on_tool  = recorder.record_tool
        engine.on_final = recorder.record_final
        engine.on_error = recorder.record_error
    """

    def __init__(self, agent_id: str, run_id: str, flush: bool = True):
        self.agent_id = agent_id
        self.run_id = run_id
        self.flush = flush
        self._step = 0
        self._buffer: list[TraceEvent] = []

    # ------------------------------------------------------------------ hooks

    def record_step(
        self,
        prompt: str,
        action: str,
        latency_ms: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: float = 0.0,
        meta: dict | None = None,
    ) -> TraceEvent:
        ev = TraceEvent(
            agent_id=self.agent_id,
            run_id=self.run_id,
            step_index=self._step,
            event_type="step",
            prompt_snapshot=prompt[:4096],  # cap size
            action=action,
            latency_ms=latency_ms,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=cost,
            metadata=meta or {},
        )
        self._step += 1
        self._buffer.append(ev)
        if self.flush:
            self._write(ev)
        return ev

    def record_tool(
        self,
        tool_name: str,
        tool_args: dict,
        tool_result: Any,
        latency_ms: float = 0.0,
        error: str | None = None,
    ) -> TraceEvent:
        ev = TraceEvent(
            agent_id=self.agent_id,
            run_id=self.run_id,
            step_index=self._step,
            event_type="tool_call" if not error else "error",
            action=f"tool:{tool_name}",
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=str(tool_result)[:2048],
            latency_ms=latency_ms,
            error=error,
        )
        self._buffer.append(ev)
        if self.flush:
            self._write(ev)
        return ev

    def record_final(
        self,
        result: Any,
        success: bool = True,
        score: float | None = None,
    ) -> TraceEvent:
        ev = TraceEvent(
            agent_id=self.agent_id,
            run_id=self.run_id,
            step_index=self._step,
            event_type="final",
            action="final",
            tool_result=str(result)[:4096],
            metadata={"success": success, "score": score},
        )
        self._buffer.append(ev)
        if self.flush:
            self._write(ev)
        return ev

    def record_error(self, error: str, context: dict | None = None) -> TraceEvent:
        ev = TraceEvent(
            agent_id=self.agent_id,
            run_id=self.run_id,
            step_index=self._step,
            event_type="error",
            action="error",
            error=error,
            metadata=context or {},
        )
        self._buffer.append(ev)
        if self.flush:
            self._write(ev)
        return ev

    # ------------------------------------------------------------------ I/O

    def _write(self, ev: TraceEvent) -> None:
        if get_db is None:
            return
        try:
            db = get_db()
            db.execute(
                """
                INSERT INTO traces (
                    trace_id, agent_id, run_id, step_index, event_type,
                    prompt_snapshot, action, tool_name, tool_args,
                    tool_result, latency_ms, input_tokens, output_tokens,
                    cost_usd, error, metadata, ts
                ) VALUES (
                    :trace_id, :agent_id, :run_id, :step_index, :event_type,
                    :prompt_snapshot, :action, :tool_name, :tool_args,
                    :tool_result, :latency_ms, :input_tokens, :output_tokens,
                    :cost_usd, :error, :metadata, :ts
                )
                """,
                {
                    **asdict(ev),
                    "tool_args": json.dumps(ev.tool_args) if ev.tool_args else None,
                    "metadata": json.dumps(ev.metadata),
                },
            )
            db.commit()
        except Exception as exc:
            # Non-fatal: never crash the agent over trace write
            import logging
            logging.getLogger(__name__).warning("TraceRecorder write failed: %s", exc)

    def flush_buffer(self) -> list[TraceEvent]:
        """Return buffered events (useful for tests / offline mode)."""
        return list(self._buffer)
