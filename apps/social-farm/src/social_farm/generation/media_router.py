"""Deterministic media-generation route selection.

The router is intentionally small and explainable.  It does not grant
permissions; it only ranks already-allowed execution strategies so Bossman can
choose between local generation, cheap cloud, browser generation, or deferral.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .higgsfield_browser_contracts import MediaKind


class GenerationRoute(str, Enum):
    LOCAL = "local"
    CHEAP_CLOUD = "cheap_cloud"
    HIGGSFIELD_BROWSER = "higgsfield_browser"
    DEFER = "defer"


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    route: GenerationRoute
    supported_kinds: frozenset[MediaKind]
    healthy: bool = True
    available: bool = True
    estimated_cost_usd: float = 0.0
    estimated_latency_s: float = 0.0
    quality_score: float = 0.5
    risk_score: float = 0.0
    required_memory_gb: float = 0.0
    browser_owner_auth_ready: bool = True


@dataclass(frozen=True, slots=True)
class RoutingContext:
    media_kind: MediaKind
    safe_available_memory_gb: float
    max_cost_usd: float
    max_latency_s: float | None = None
    min_quality_score: float = 0.0
    allow_browser: bool = True
    allow_cloud: bool = True
    allow_local: bool = True


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    selected: GenerationRoute
    score: float
    reason: str
    rejected: tuple[str, ...]


def rank_generation_routes(
    context: RoutingContext,
    candidates: Iterable[RouteCandidate],
) -> tuple[list[tuple[float, RouteCandidate]], list[str]]:
    """Отранжировать пригодные пути и назвать, почему отвергнуты остальные.

    Вынесено из `choose_generation_route` без единого изменения правил: выбор
    одного пути и построение цепочки запасных — это один и тот же расчёт, и
    два его экземпляра рано или поздно разошлись бы. Здесь он один.
    """
    scored: list[tuple[float, RouteCandidate]] = []
    rejected: list[str] = []

    for candidate in candidates:
        prefix = candidate.route.value
        if context.media_kind not in candidate.supported_kinds:
            rejected.append(f"{prefix}:unsupported_kind")
            continue
        if not candidate.available:
            rejected.append(f"{prefix}:unavailable")
            continue
        if not candidate.healthy:
            rejected.append(f"{prefix}:unhealthy")
            continue
        if candidate.estimated_cost_usd > context.max_cost_usd:
            rejected.append(f"{prefix}:cost")
            continue
        if context.max_latency_s is not None and candidate.estimated_latency_s > context.max_latency_s:
            rejected.append(f"{prefix}:latency")
            continue
        if candidate.quality_score < context.min_quality_score:
            rejected.append(f"{prefix}:quality")
            continue
        if candidate.required_memory_gb > context.safe_available_memory_gb:
            rejected.append(f"{prefix}:memory")
            continue
        if candidate.route is GenerationRoute.LOCAL and not context.allow_local:
            rejected.append(f"{prefix}:policy")
            continue
        if candidate.route is GenerationRoute.CHEAP_CLOUD and not context.allow_cloud:
            rejected.append(f"{prefix}:policy")
            continue
        if candidate.route is GenerationRoute.HIGGSFIELD_BROWSER:
            if not context.allow_browser:
                rejected.append(f"{prefix}:policy")
                continue
            if not candidate.browser_owner_auth_ready:
                rejected.append(f"{prefix}:owner_auth")
                continue

        # Higher is better.  Cost and latency are normalized gently so quality
        # dominates, while risk remains a strong penalty.
        cost_penalty = candidate.estimated_cost_usd / max(context.max_cost_usd, 0.01)
        latency_denominator = context.max_latency_s or max(candidate.estimated_latency_s, 1.0)
        latency_penalty = candidate.estimated_latency_s / max(latency_denominator, 1.0)
        score = (
            candidate.quality_score * 2.0
            - candidate.risk_score * 1.5
            - cost_penalty * 0.5
            - latency_penalty * 0.25
        )
        # Prefer local on a tie because it is owner-controlled and has no
        # provider/network dependency; browser comes before cloud when scores
        # are equal and owner auth is already available.
        tie_bonus = {
            GenerationRoute.LOCAL: 0.003,
            GenerationRoute.HIGGSFIELD_BROWSER: 0.002,
            GenerationRoute.CHEAP_CLOUD: 0.001,
            GenerationRoute.DEFER: 0.0,
        }[candidate.route]
        scored.append((score + tie_bonus, candidate))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored, rejected


def choose_generation_route(
    context: RoutingContext,
    candidates: Iterable[RouteCandidate],
) -> RoutingDecision:
    scored, rejected = rank_generation_routes(context, candidates)

    if not scored:
        return RoutingDecision(
            selected=GenerationRoute.DEFER,
            score=float("-inf"),
            reason="no eligible media generation route",
            rejected=tuple(rejected),
        )

    score, selected = scored[0]
    return RoutingDecision(
        selected=selected.route,
        score=score,
        reason=(
            f"selected {selected.route.value}: quality={selected.quality_score:.2f}, "
            f"cost=${selected.estimated_cost_usd:.4f}, latency={selected.estimated_latency_s:.1f}s, "
            f"risk={selected.risk_score:.2f}"
        ),
        rejected=tuple(rejected),
    )


__all__ = [
    "GenerationRoute",
    "RouteCandidate",
    "RoutingContext",
    "RoutingDecision",
    "choose_generation_route",
    "rank_generation_routes",
]
