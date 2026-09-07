"""V7 deterministic strategy generation and shadow utility routing."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .mission_ir import MissionIR
from .world_state import Fact


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    path: str
    p_success: float
    goal_value: float
    latency_cost: float = 0.0
    money_cost: float = 0.0
    risk_penalty: float = 0.0
    resource_pressure: float = 0.0
    uncertainty_penalty: float = 0.0
    required_permissions: Sequence[str] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def utility(self) -> float:
        return (self.p_success * self.goal_value - self.latency_cost - self.money_cost
                - self.risk_penalty - self.resource_pressure - self.uncertainty_penalty)


@dataclass(frozen=True)
class ShadowDecision:
    selected: Strategy | None
    ranked: tuple[Strategy, ...]
    reason: str
    shadow_only: bool = True


def generate_strategies(mission: MissionIR, world_state: Sequence[Fact]) -> tuple[Strategy, ...]:
    """Generate conservative deterministic candidates; models may add candidates later."""
    uncertainty = sum(1 for f in world_state if f.valid_until_epoch_s is None)
    penalty = min(20.0, uncertainty * 1.5)
    candidates = [
        Strategy("deterministic-tool", "tool", .90, 100, latency_cost=5, risk_penalty=5,
                 uncertainty_penalty=penalty),
        Strategy("small-model-tools", "small_model", .78, 100, latency_cost=8, money_cost=1,
                 risk_penalty=8, uncertainty_penalty=penalty),
        Strategy("large-model-tools", "large_model", .88, 100, latency_cost=18, money_cost=6,
                 risk_penalty=7, uncertainty_penalty=penalty),
        Strategy("human-escalation", "human", .98, 100, latency_cost=35, risk_penalty=2,
                 uncertainty_penalty=0),
    ]
    return tuple(candidates)


def shadow_route(mission: MissionIR, strategies: Sequence[Strategy]) -> ShadowDecision:
    """Rank candidates without changing production routing or bypassing policy gates."""
    ready, errors = mission.validate_execution_readiness()
    if not ready:
        return ShadowDecision(None, tuple(), "; ".join(errors))

    allowed = set(mission.permissions)
    eligible = [s for s in strategies if set(s.required_permissions).issubset(allowed)]
    ranked = tuple(sorted(eligible, key=lambda s: (-s.utility, s.strategy_id)))
    if not ranked:
        return ShadowDecision(None, tuple(), "no policy-eligible strategy candidates")
    return ShadowDecision(ranked[0], ranked, "shadow recommendation only; production router unchanged")
