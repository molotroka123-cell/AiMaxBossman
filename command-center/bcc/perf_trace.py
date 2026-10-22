"""Sanitized monotonic nanosecond span tracer for Bossman North Star 2x (`CP-PERF-01`).

Records stage-by-stage latency across the end-to-end Agent Orchestrator pipeline:
    mobile.request -> session.auth -> session.load -> model.catalog -> model.route ->
    skill.match -> preflight -> task.create -> queue.wait -> llm.load -> llm.prefill ->
    llm.decode -> tool.dispatch -> tool.run -> verify -> memory.retrieve ->
    receipt.persist -> ws.deliver

Security & Privacy Invariant:
    Never stores prompts, raw messages, secrets, API keys, tokens, file paths, or
    window titles. Only whitelisted scalar telemetry fields (`cache_hit`, `status`,
    `mode`, `worker_count`, `tool_count`, `skill_id`, `model_id`, `fast_path`,
    `turn_index`) are retained.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import statistics
import time
import uuid
from typing import Any, Iterator

REQUIRED_STAGE_SPANS: tuple[str, ...] = (
    "mobile.request",
    "session.auth",
    "session.load",
    "model.catalog",
    "model.route",
    "skill.match",
    "preflight",
    "task.create",
    "queue.wait",
    "llm.load",
    "llm.prefill",
    "llm.decode",
    "tool.dispatch",
    "tool.run",
    "verify",
    "memory.retrieve",
    "receipt.persist",
    "ws.deliver",
)

_ALLOWED_META_KEYS: frozenset[str] = frozenset({
    "cache_hit",
    "status",
    "mode",
    "worker_count",
    "tool_count",
    "skill_id",
    "model_id",
    "fast_path",
    "turn_index",
    "session_count",
    "compact",
    "lane",
})


def _sanitize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    if not meta:
        return {}
    clean: dict[str, Any] = {}
    for k, v in meta.items():
        if k not in _ALLOWED_META_KEYS:
            continue
        if isinstance(v, (bool, int, float)):
            clean[k] = v
        elif isinstance(v, str):
            # Cap length and reject path-like or secret-like strings
            if len(v) <= 64 and "\\" not in v and "/" not in v and "sk-" not in v.lower():
                clean[k] = v
    return clean


def _sanitize_id(raw_id: str | None) -> str:
    if not raw_id:
        return ""
    s = str(raw_id).strip()
    if len(s) > 40 or "\\" in s or "/" in s:
        return s[:16]
    return s


class SpanRecord:
    __slots__ = ("stage", "duration_ms", "meta")

    def __init__(self, stage: str, duration_ms: float, meta: dict[str, Any]) -> None:
        self.stage = stage
        self.duration_ms = duration_ms
        self.meta = meta

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "duration_ms": round(self.duration_ms, 4),
            "meta": self.meta,
        }


class ActiveTrace:
    __slots__ = (
        "trace_id",
        "route",
        "session_id",
        "mode",
        "started_ns",
        "duration_ms",
        "spans",
        "_tracer",
    )

    def __init__(
        self,
        tracer: "PerfTracer",
        route: str,
        session_id: str = "",
        mode: str = "",
    ) -> None:
        self.trace_id = f"ptrace-{uuid.uuid4().hex[:12]}"
        self.route = str(route)[:64]
        self.session_id = _sanitize_id(session_id)
        self.mode = _sanitize_id(mode)
        self.started_ns = time.perf_counter_ns()
        self.duration_ms = 0.0
        self.spans: list[SpanRecord] = []
        self._tracer = tracer

    @contextmanager
    def span(self, stage: str, **meta: Any) -> Iterator[None]:
        t0 = time.perf_counter_ns()
        try:
            yield
        finally:
            dt_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
            self.spans.append(SpanRecord(stage, dt_ms, _sanitize_meta(meta)))

    def record_span_ms(self, stage: str, duration_ms: float, **meta: Any) -> None:
        self.spans.append(SpanRecord(stage, float(duration_ms), _sanitize_meta(meta)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "route": self.route,
            "session_id": self.session_id,
            "mode": self.mode,
            "duration_ms": round(self.duration_ms, 4),
            "spans": [s.to_dict() for s in self.spans],
        }


class PerfTracer:
    """Bounded in-memory monotonic span tracer with zero secret/prompt leakage."""

    def __init__(self, max_traces: int = 200) -> None:
        self._max_traces = max(10, int(max_traces))
        self._traces: deque[ActiveTrace] = deque(maxlen=self._max_traces)

    @contextmanager
    def start_trace(
        self,
        *,
        route: str,
        session_id: str = "",
        mode: str = "",
    ) -> Iterator[ActiveTrace]:
        tr = ActiveTrace(self, route=route, session_id=session_id, mode=mode)
        try:
            yield tr
        finally:
            tr.duration_ms = (time.perf_counter_ns() - tr.started_ns) / 1_000_000.0
            self._traces.append(tr)

    def clear(self) -> None:
        self._traces.clear()

    def recent_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        cap = max(1, min(int(limit), self._max_traces))
        items = list(self._traces)[-cap:]
        items.reverse()
        return [t.to_dict() for t in items]

    def top_spans(self, limit: int = 10) -> list[dict[str, Any]]:
        by_stage: dict[str, list[float]] = {}
        total_span_ms = 0.0
        for tr in self._traces:
            for sp in tr.spans:
                # Exclude outer envelope mobile.request from inner stage share calculation
                if sp.stage == "mobile.request":
                    continue
                by_stage.setdefault(sp.stage, []).append(sp.duration_ms)
                total_span_ms += sp.duration_ms

        rows: list[dict[str, Any]] = []
        for stage, vals in by_stage.items():
            s = sorted(vals)
            n = len(s)
            p95_idx = min(n - 1, max(0, int(round(0.95 * (n - 1)))))
            stage_sum = sum(s)
            rows.append({
                "stage": stage,
                "count": n,
                "median_ms": round(float(statistics.median(s)), 4),
                "mean_ms": round(float(statistics.fmean(s)), 4),
                "p95_ms": round(float(s[p95_idx]), 4),
                "max_ms": round(float(s[-1]), 4),
                "total_ms": round(float(stage_sum), 4),
                "share_pct": round((stage_sum / total_span_ms * 100.0) if total_span_ms > 0 else 0.0, 2),
            })

        rows.sort(key=lambda r: (r["total_ms"], r["p95_ms"]), reverse=True)
        return rows[: max(1, int(limit))]


GLOBAL_PERF_TRACER = PerfTracer(max_traces=250)
