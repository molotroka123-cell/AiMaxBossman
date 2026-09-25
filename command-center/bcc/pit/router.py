from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PrivacyClass(StrEnum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    LOCAL_ONLY = "local_only"


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    id: str
    provider: str
    capabilities: frozenset[str]
    local: bool = False
    available: bool = True
    zero_cost: bool = False
    paid: bool = False
    predicted_quality: float = 0.5
    latency_score: float = 0.5
    privacy_risk: float = 0.5


@dataclass(frozen=True, slots=True)
class RouteRequest:
    intent: str
    required_capabilities: frozenset[str] = field(default_factory=lambda: frozenset({"chat"}))
    privacy: PrivacyClass = PrivacyClass.PERSONAL
    needs_web: bool = False
    max_cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class RouteDecision:
    selected_model: str
    provider: str
    fallback_chain: tuple[str, ...]
    needs_web: bool
    reason_code: str
    score: float
    requires_owner_approval: bool


class NoEligibleRoute(RuntimeError):
    pass


def _eligible(req: RouteRequest, endpoint: ModelEndpoint, *, allow_paid: bool) -> bool:
    if not endpoint.available:
        return False
    if not req.required_capabilities.issubset(endpoint.capabilities):
        return False
    if req.privacy == PrivacyClass.LOCAL_ONLY and not endpoint.local:
        return False
    if endpoint.paid and not allow_paid:
        return False
    if req.max_cost_usd <= 0 and endpoint.paid:
        return False
    return True


def _score(endpoint: ModelEndpoint, *, local_bonus: float) -> float:
    cost_penalty = 0.0 if endpoint.zero_cost or endpoint.local else (0.7 if endpoint.paid else 0.25)
    return (
        1.7 * endpoint.predicted_quality
        - 0.55 * endpoint.latency_score
        - 0.8 * cost_penalty
        - 0.9 * endpoint.privacy_risk
        + (local_bonus if endpoint.local else 0.0)
    )


def choose_route(
    req: RouteRequest,
    endpoints: list[ModelEndpoint],
    *,
    allow_paid: bool = False,
    local_bonus: float = 0.30,
) -> RouteDecision:
    """Choose one model without changing Bossman's authority.

    Laptop mode simply marks local endpoints unavailable. Later AI Max mode can
    enable them without changing Telegram/persona code.
    """
    eligible = [e for e in endpoints if _eligible(req, e, allow_paid=allow_paid)]
    if not eligible:
        raise NoEligibleRoute("no provider satisfies capability/privacy/cost policy")

    ranked = sorted(eligible, key=lambda e: (_score(e, local_bonus=local_bonus), e.id), reverse=True)
    winner = ranked[0]
    reason = "LOCAL_FIRST" if winner.local else ("REMOTE_ZERO_COST" if winner.zero_cost else "REMOTE_POLICY_ALLOWED")
    return RouteDecision(
        selected_model=winner.id,
        provider=winner.provider,
        fallback_chain=tuple(e.id for e in ranked[1:]),
        needs_web=req.needs_web,
        reason_code=reason,
        score=round(_score(winner, local_bonus=local_bonus), 6),
        requires_owner_approval=bool(winner.paid),
    )
