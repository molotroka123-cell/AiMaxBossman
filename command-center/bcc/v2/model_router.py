from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class ModelCandidate:
    id: int | str
    alias: str
    online: bool = True
    local: bool = True
    context_window: int = 8192
    capabilities: set[str] = field(default_factory=set)
    verified_capabilities: set[str] = field(default_factory=set)
    price_in: float = 0.0      # USD / 1M
    price_out: float = 0.0
    latency_ms: float | None = None
    gen_tps: float | None = None
    memory_mb: float | None = None
    queue_depth: int = 0
    success_rate: float | None = None
    role_scores: dict[str, float] = field(default_factory=dict)

@dataclass(slots=True)
class RouteRequest:
    task_type: str
    requires: set[str] = field(default_factory=set)
    min_context: int = 0
    cloud_allowed: bool = True
    max_price_out: float | None = None
    available_memory_mb: float | None = None
    prefer_local: bool = True

@dataclass(slots=True)
class RouteDecision:
    model: ModelCandidate | None
    score: float
    reasons: list[str]
    rejected: dict[str, list[str]]

def route(req: RouteRequest, models: list[ModelCandidate]) -> RouteDecision:
    rejected: dict[str, list[str]] = {}
    scored: list[tuple[float, ModelCandidate, list[str]]] = []

    for m in models:
        bad: list[str] = []
        if not m.online:
            bad.append("unhealthy/offline")
        if not req.cloud_allowed and not m.local:
            bad.append("cloud disabled")
        if req.min_context and m.context_window < req.min_context:
            bad.append(f"context {m.context_window} < {req.min_context}")
        missing = req.requires - (m.verified_capabilities or m.capabilities)
        if missing:
            bad.append("missing capabilities: " + ", ".join(sorted(missing)))
        if req.max_price_out is not None and m.price_out > req.max_price_out:
            bad.append(f"output price {m.price_out} > {req.max_price_out}")
        if (req.available_memory_mb is not None and m.local and m.memory_mb is not None
                and m.memory_mb > req.available_memory_mb):
            bad.append(f"memory {m.memory_mb:.0f}MB > available {req.available_memory_mb:.0f}MB")
        if bad:
            rejected[m.alias] = bad
            continue

        score = 50.0
        why: list[str] = []
        if req.prefer_local and m.local:
            score += 14
            why.append("+ local/free preference")
        role = float(m.role_scores.get(req.task_type, 0.0))
        score += role * 20
        if role:
            why.append(f"+ role score {role:.2f}")
        if m.success_rate is not None:
            score += (m.success_rate - 0.5) * 20
            why.append(f"+ historical success {m.success_rate:.0%}")
        if m.gen_tps:
            speed_bonus = min(m.gen_tps / 10.0, 10.0)
            score += speed_bonus
            why.append(f"+ speed {m.gen_tps:.1f} tok/s")
        if m.latency_ms:
            score -= min(m.latency_ms / 1000.0, 8.0)
        score -= min(m.queue_depth * 2.0, 12.0)
        if not m.local:
            # Small cost penalty; role/quality can still win.
            score -= min((m.price_in + m.price_out) / 2.0, 20.0)
            why.append(f"- cloud cost ${m.price_in:.3f}/${m.price_out:.3f} per 1M")
        scored.append((score, m, why))

    if not scored:
        return RouteDecision(None, float("-inf"), [], rejected)
    scored.sort(key=lambda x: x[0], reverse=True)
    score, model, reasons = scored[0]
    return RouteDecision(model, round(score, 3), reasons, rejected)
