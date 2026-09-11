"""V7 Adaptive Reality OS - Deterministic Strategy Engine.

Generates and scores strategies based on Mission IR constraints and World State Graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from v7_mission_ir import ConstraintType, MissionIR
from v7_world_state_graph import WorldStateGraph


@dataclass
class StrategyStep:
    action_type: str
    target: str
    params: Dict[str, Any] = field(default_factory=dict)
    expected_effect: str = ""


@dataclass
class Strategy:
    id: str
    name: str
    steps: List[StrategyStep]
    estimated_latency_ms: float
    estimated_cost_usd: float
    risk_score: float  # 0.0 (safe) to 1.0 (hazardous)
    requires_external_network: bool = False
    utility_score: float = 0.0


class StrategyEngine:
    """Deterministic strategy generation and constraint filtering."""

    def __init__(self, world_state: WorldStateGraph):
        self.world_state = world_state

    def generate_candidate_strategies(self, mission: MissionIR) -> List[Strategy]:
        strategies: List[Strategy] = []

        # Local deterministic candidate (Default safe baseline)
        strategies.append(
            Strategy(
                id=f"{mission.id}-strat-local",
                name="LocalDeterministicExecution",
                steps=[
                    StrategyStep(
                        action_type="inspect_state",
                        target=mission.objectives[0].id if mission.objectives else "root",
                        expected_effect="read_current_state",
                    ),
                    StrategyStep(
                        action_type="local_transform",
                        target="workspace",
                        expected_effect=mission.objectives[0].target_state if mission.objectives else "done",
                    ),
                ],
                estimated_latency_ms=120.0,
                estimated_cost_usd=0.0,
                risk_score=0.1,
                requires_external_network=False,
            )
        )

        # Agentic multi-tool candidate
        strategies.append(
            Strategy(
                id=f"{mission.id}-strat-agentic",
                name="AgenticModelExecution",
                steps=[
                    StrategyStep(
                        action_type="plan_and_tool_call",
                        target="model_gateway",
                        expected_effect="full_mission_fulfillment",
                    )
                ],
                estimated_latency_ms=1800.0,
                estimated_cost_usd=0.04,
                risk_score=0.35,
                requires_external_network=True,
            )
        )

        return self.filter_and_score(strategies, mission)

    def filter_and_score(self, strategies: List[Strategy], mission: MissionIR) -> List[Strategy]:
        admissible = []
        for strat in strategies:
            # Check budgets
            if strat.estimated_cost_usd > mission.budget.max_cost_usd:
                continue
            if strat.requires_external_network and mission.budget.disallow_cloud_fallback:
                continue

            # Utility scoring: utility = target_fulfillment / (cost + latency_weight + risk)
            latency_penalty = strat.estimated_latency_ms / 1000.0 * 0.1
            cost_penalty = strat.estimated_cost_usd * 10.0
            risk_penalty = strat.risk_score * 2.0
            utility = 10.0 - (latency_penalty + cost_penalty + risk_penalty)
            strat.utility_score = max(0.0, round(utility, 3))
            admissible.append(strat)

        # Sort descending by utility
        admissible.sort(key=lambda s: s.utility_score, reverse=True)
        return admissible
