"""Autonomous cost/quality/latency/energy route selection for Bossman 1.5.

This layer chooses among already-authorized candidates. It never changes
never/ask/allowed policy and never creates provider accounts. Unknown cloud
price can be configured fail-closed, matching the Cost Governor principle.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from bossman.resource_brain import ResourceBrain, ResourceSnapshot


@dataclass(frozen=True)
class RouteCandidate:
    id: str
    quality_lcb: float
    cost_usd: float | None
    latency_ms: float | None
    energy_wh: float | None
    local: bool
    ram_estimate: int = 0
    health: str = "healthy"
    tools_ok: bool = True


@dataclass(frozen=True)
class RoutePolicy:
    min_quality_lcb: float = 0.70
    max_cost_usd: float | None = None
    max_latency_ms: float | None = None
    unknown_price_block: bool = True
    prefer_local_bonus: float = 0.04
    quality_weight: float = 0.62
    cost_weight: float = 0.16
    latency_weight: float = 0.12
    energy_weight: float = 0.10


@dataclass(frozen=True)
class RouteDecision:
    candidate_id: str | None
    reason: str
    score: float | None
    considered: tuple[dict[str, Any], ...]


class AutonomousResourceManager:
    def __init__(self, brain: ResourceBrain | None = None):
        self.brain = brain or ResourceBrain()

    @staticmethod
    def _finite_or_none(value: float | None) -> bool:
        return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)
                                 and isfinite(value) and value >= 0)

    @staticmethod
    def _norm(value: float | None, lo: float, hi: float, *, unknown: float = 1.0) -> float:
        if value is None:
            return unknown
        if hi <= lo:
            return 0.0
        return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))

    def choose(self, candidates: list[RouteCandidate], policy: RoutePolicy,
               *, snapshot: ResourceSnapshot | None = None) -> RouteDecision:
        if not 0 <= policy.min_quality_lcb <= 1:
            raise ValueError("min_quality_lcb")
        valid = []
        considered = []
        for c in candidates:
            reasons = []
            if not c.id or not 0 <= c.quality_lcb <= 1:
                reasons.append("invalid_quality")
            if not all(self._finite_or_none(v) for v in (c.cost_usd, c.latency_ms, c.energy_wh)):
                reasons.append("invalid_metric")
            if c.health != "healthy":
                reasons.append("unhealthy")
            if not c.tools_ok:
                reasons.append("tools")
            if c.quality_lcb < policy.min_quality_lcb:
                reasons.append("quality")
            if c.cost_usd is None and policy.unknown_price_block and not c.local:
                reasons.append("unknown_price")
            if policy.max_cost_usd is not None and c.cost_usd is not None and c.cost_usd > policy.max_cost_usd:
                reasons.append("cost")
            if policy.max_latency_ms is not None and c.latency_ms is not None and c.latency_ms > policy.max_latency_ms:
                reasons.append("latency")
            if snapshot is not None and c.ram_estimate > snapshot.unified_available:
                reasons.append("ram")
            row = {"id": c.id, "eligible": not reasons, "reasons": reasons}
            considered.append(row)
            if not reasons:
                valid.append(c)
        if not valid:
            return RouteDecision(None, "NO_ELIGIBLE_ROUTE", None, tuple(considered))

        costs = [float(c.cost_usd or 0.0) for c in valid]
        lats = [float(c.latency_ms or 0.0) for c in valid if c.latency_ms is not None] or [0.0]
        energies = [float(c.energy_wh or 0.0) for c in valid if c.energy_wh is not None] or [0.0]
        cmin, cmax = min(costs), max(costs)
        lmin, lmax = min(lats), max(lats)
        emin, emax = min(energies), max(energies)

        ranked = []
        for c in valid:
            cost_pen = self._norm(float(c.cost_usd or 0.0), cmin, cmax)
            lat_pen = self._norm(c.latency_ms, lmin, lmax, unknown=0.5)
            energy_pen = self._norm(c.energy_wh, emin, emax, unknown=0.5)
            score = (
                policy.quality_weight * c.quality_lcb
                - policy.cost_weight * cost_pen
                - policy.latency_weight * lat_pen
                - policy.energy_weight * energy_pen
                + (policy.prefer_local_bonus if c.local else 0.0)
            )
            ranked.append((round(score, 8), c.id))
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return RouteDecision(ranked[0][1], "CHEAPEST_CAPABLE_MULTI_OBJECTIVE", ranked[0][0],
                             tuple(considered))
