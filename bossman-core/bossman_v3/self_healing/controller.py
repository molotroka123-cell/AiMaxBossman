from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any
from bossman_v3.contracts import FailureMemoryPort

class ErrorClass(str, Enum):
    TRANSIENT="transient"
    STALE_STATE="stale_state"
    PERMISSION="permission"
    POLICY_DENIED="policy_denied"
    SCHEMA="schema"
    TOOL_UNAVAILABLE="tool_unavailable"
    ENVIRONMENT_CHANGED="environment_changed"
    VERIFIER_FAILED="verifier_failed"
    UNKNOWN="unknown"

class RecoveryAction(str, Enum):
    RETRY="RETRY"
    REPLAN="REPLAN"
    SWITCH_TOOL="SWITCH_TOOL"
    SWITCH_MODEL="SWITCH_MODEL"
    ABORT="ABORT"

@dataclass(frozen=True)
class RecoveryStrategy:
    name: str
    action: RecoveryAction
    base_p_fix: float
    retry_cost: float
    risk: float

@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    strategy: str | None
    retry_value: float
    reason: str

class SelfHealingController:
    def __init__(self, failure_memory: FailureMemoryPort, *, max_attempts: int = 3):
        self.failure_memory = failure_memory
        self.max_attempts = max_attempts

    @staticmethod
    def retry_value(p_fix: float, value_success: float, retry_cost: float, risk: float) -> float:
        return p_fix * value_success - retry_cost - risk

    def _condition_p_fix(self, signature: str, strategy: RecoveryStrategy) -> float:
        history = self.failure_memory.query(signature, limit=20)
        same = [h for h in history if h.get("strategy") == strategy.name]
        if not same:
            return max(0.0, min(1.0, strategy.base_p_fix))
        successes = sum(bool(h.get("fixed")) for h in same)
        # Conservative Beta(1,1) posterior mean, capped by declared base rate.
        empirical = (1 + successes) / (2 + len(same))
        return min(strategy.base_p_fix, empirical)

    def decide(self, *, signature: str, strategies: list[RecoveryStrategy], value_success: float,
               attempted: list[str] | None = None) -> RecoveryDecision:
        attempted = attempted or []
        if len(attempted) >= self.max_attempts:
            return RecoveryDecision(RecoveryAction.ABORT, None, 0.0, "max recovery attempts reached")
        candidates=[]
        for strategy in strategies:
            if strategy.name in attempted:
                continue  # never repeat same disproven fix blindly
            p = self._condition_p_fix(signature, strategy)
            rv = self.retry_value(p, value_success, strategy.retry_cost, strategy.risk)
            candidates.append((rv, strategy))
        if not candidates:
            return RecoveryDecision(RecoveryAction.ABORT, None, 0.0, "no unused recovery strategy")
        rv, best = max(candidates, key=lambda x: x[0])
        if rv <= 0:
            fallback = RecoveryAction.REPLAN if any(s.action == RecoveryAction.REPLAN for _,s in candidates) else RecoveryAction.ABORT
            return RecoveryDecision(fallback, None, rv, "retry economics are non-positive")
        return RecoveryDecision(best.action, best.name, rv, "positive expected recovery value")
