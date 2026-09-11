"""V7 Adaptive Reality OS - Shadow Utility Router.

Operates in shadow mode against legacy dispatcher to evaluate expected-utility decisions without production disruption.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from v7_mission_ir import MissionIR
from v7_strategy_engine import Strategy, StrategyEngine


@dataclass
class ShadowRoutingLog:
    mission_id: str
    legacy_choice: str
    shadow_utility_choice: str
    utility_score: float
    discrepancy: bool


class ShadowUtilityRouter:
    """Evaluates utility-based routing in shadow mode."""

    def __init__(self, strategy_engine: StrategyEngine):
        self.strategy_engine = strategy_engine
        self.logs: List[ShadowRoutingLog] = []

    def route_shadow(self, mission: MissionIR, legacy_choice: str) -> Strategy:
        candidates = self.strategy_engine.generate_candidate_strategies(mission)
        if not candidates:
            raise RuntimeError(f"No admissible strategies found for mission {mission.id}")

        best_strategy = candidates[0]
        log = ShadowRoutingLog(
            mission_id=mission.id,
            legacy_choice=legacy_choice,
            shadow_utility_choice=best_strategy.name,
            utility_score=best_strategy.utility_score,
            discrepancy=(legacy_choice != best_strategy.name),
        )
        self.logs.append(log)
        return best_strategy
