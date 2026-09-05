from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


@dataclass(slots=True)
class Evidence:
    name: str
    p_if_true: float
    p_if_false: float
    weight: float = 1.0
    note: str = ""


@dataclass(slots=True)
class Hypothesis:
    name: str
    prior: float
    support: float = 0.0
    confidence: float = 0.5
    reliability: float = 1.0


@dataclass(slots=True)
class ActionOption:
    name: str
    p_success: float
    reward: float
    p_failure: float
    loss: float
    cost: float
    information_gain: float = 0.0
    latency_cost: float = 0.0
    retry_penalty: float = 0.0
    risk: RiskLevel = RiskLevel.LOW
    metadata: dict[str, float | str] = field(default_factory=dict)


@dataclass(slots=True)
class ObservationOutcome:
    name: str
    probability: float
    posterior: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class BeliefState:
    probabilities: dict[str, float]

    def normalize(self) -> "BeliefState":
        total = sum(max(0.0, v) for v in self.probabilities.values())
        if total <= 0:
            n = max(1, len(self.probabilities))
            return BeliefState({k: 1.0 / n for k in self.probabilities})
        return BeliefState({k: max(0.0, v) / total for k, v in self.probabilities.items()})


class HardReasoningMath:
    """Mathematical controller primitives for Bossman V2 hard reasoning.

    This module is deliberately model-agnostic. It does not replace policy,
    approvals, perimeter checks, or deterministic verification. It provides
    scoring and stopping primitives the planner/router can call.
    """

    RISK_LOSS_MULTIPLIER = {
        RiskLevel.LOW: 0.25,
        RiskLevel.MEDIUM: 1.0,
        RiskLevel.HIGH: 2.5,
        RiskLevel.EXTREME: 6.0,
    }

    RISK_THRESHOLD_BONUS = {
        RiskLevel.LOW: 0.00,
        RiskLevel.MEDIUM: 0.10,
        RiskLevel.HIGH: 0.20,
        RiskLevel.EXTREME: 0.30,
    }

    @staticmethod
    def _clip_probability(x: float, eps: float = 1e-9) -> float:
        return min(max(x, eps), 1.0 - eps)

    @classmethod
    def sequential_probability_ratio(
        cls,
        evidences: Sequence[Evidence],
        alpha: float = 0.05,
        beta: float = 0.10,
    ) -> dict[str, float | str]:
        """Wald SPRT.

        Accept H1 if Lambda > A, reject if Lambda < B, continue otherwise.
        A = (1-beta)/alpha, B = beta/(1-alpha)
        """
        lr = 1.0
        log_lr = 0.0
        for e in evidences:
            p1 = cls._clip_probability(e.p_if_true)
            p0 = cls._clip_probability(e.p_if_false)
            term = (p1 / p0) ** max(0.0, e.weight)
            lr *= term
            log_lr += math.log(term)
        A = (1.0 - beta) / alpha
        B = beta / (1.0 - alpha)
        decision = "continue"
        if lr > A:
            decision = "accept_H1"
        elif lr < B:
            decision = "accept_H0"
        return {
            "likelihood_ratio": lr,
            "log_likelihood_ratio": log_lr,
            "accept_upper": A,
            "reject_lower": B,
            "decision": decision,
        }

    @staticmethod
    def value_of_information(current_utility: float, expected_utility_after: float, cost: float) -> float:
        return expected_utility_after - current_utility - cost

    @classmethod
    def expected_utility(cls, action: ActionOption) -> float:
        p_success = cls._clip_probability(action.p_success)
        p_failure = cls._clip_probability(action.p_failure)
        risk_mult = cls.RISK_LOSS_MULTIPLIER[action.risk]
        return (p_success * action.reward) - (p_failure * action.loss * risk_mult) - action.cost - action.latency_cost - action.retry_penalty

    @staticmethod
    def bayesian_model_selection(hypotheses: Sequence[Hypothesis], likelihoods: dict[str, float]) -> BeliefState:
        raw: dict[str, float] = {}
        for h in hypotheses:
            like = max(0.0, likelihoods.get(h.name, 0.0))
            raw[h.name] = like * max(0.0, h.prior)
        return BeliefState(raw).normalize()

    @staticmethod
    def entropy(probabilities: Iterable[float], base: float = 2.0) -> float:
        total = 0.0
        for p in probabilities:
            if p > 0:
                total -= p * (math.log(p) / math.log(base))
        return total

    @classmethod
    def expected_entropy_after_action(cls, outcomes: Sequence[ObservationOutcome]) -> float:
        total = 0.0
        for o in outcomes:
            posterior = BeliefState(o.posterior).normalize()
            total += max(0.0, o.probability) * cls.entropy(posterior.probabilities.values())
        return total

    @classmethod
    def entropy_reduction(cls, current_belief: BeliefState, outcomes: Sequence[ObservationOutcome]) -> float:
        current = cls.entropy(current_belief.normalize().probabilities.values())
        future = cls.expected_entropy_after_action(outcomes)
        return current - future

    @staticmethod
    def counterfactual_value(expected_alternative_outcome: float, observed_outcome: float) -> float:
        return expected_alternative_outcome - observed_outcome

    @staticmethod
    def weighted_consensus(hypotheses: Sequence[Hypothesis]) -> float:
        numerator = 0.0
        denominator = 0.0
        for h in hypotheses:
            weight = max(0.0, h.reliability)
            numerator += weight * max(0.0, h.confidence) * h.support
            denominator += weight
        return numerator / denominator if denominator > 0 else 0.0

    @staticmethod
    def ensemble_value(accuracy: float, diversity: float) -> float:
        return max(0.0, accuracy) * max(0.0, diversity)

    @staticmethod
    def regret(best_reward: float, actual_reward: float) -> float:
        return best_reward - actual_reward

    @staticmethod
    def cumulative_regret(pairs: Sequence[tuple[float, float]]) -> float:
        return sum(best - actual for best, actual in pairs)

    @staticmethod
    def ucb(mean_reward: float, total_rounds: int, arm_pulls: int, c: float = 1.4) -> float:
        if arm_pulls <= 0:
            return float("inf")
        return mean_reward + c * math.sqrt(math.log(max(1, total_rounds)) / arm_pulls)

    @staticmethod
    def sample_beta(alpha: float, beta: float) -> float:
        return random.betavariate(max(alpha, 1e-6), max(beta, 1e-6))

    @classmethod
    def thompson_score(cls, alpha: float, beta: float, reward_bias: float = 1.0, cost_penalty: float = 0.0) -> float:
        return cls.sample_beta(alpha, beta) * reward_bias - cost_penalty

    @staticmethod
    def difficulty_normalized_score(observed_success: float, expected_success: float) -> float:
        if expected_success <= 0:
            return 0.0
        return observed_success / expected_success

    @staticmethod
    def allocate_search_budget(total_budget: float, branch_scores: Sequence[float], gamma: float = 1.5) -> list[float]:
        if total_budget <= 0 or not branch_scores:
            return []
        powered = [max(0.0, s) ** gamma for s in branch_scores]
        denom = sum(powered)
        if denom <= 0:
            return [total_budget / len(branch_scores)] * len(branch_scores)
        return [total_budget * p / denom for p in powered]

    @staticmethod
    def dynamic_beam_width(uncertainty: float, complexity: float, k_min: int = 1, k_range: int = 4) -> int:
        u = min(max(uncertainty, 0.0), 1.0)
        c = min(max(complexity, 0.0), 1.0)
        return k_min + math.floor(u * c * k_range)

    @staticmethod
    def reasoning_temperature(confidence: float, novelty: float, t_min: float = 0.05, t_max: float = 0.8) -> float:
        conf = min(max(confidence, 0.0), 1.0)
        nov = min(max(novelty, 0.0), 1.0)
        return t_min + nov * (1.0 - conf) * (t_max - t_min)

    @classmethod
    def decision_score(
        cls,
        evidence: float,
        confidence: float,
        verifier: float,
        consistency: float,
        risk: float,
        contradictions: float,
        uncertainty: float,
        weights: dict[str, float] | None = None,
    ) -> float:
        w = {
            "evidence": 1.0,
            "confidence": 1.0,
            "verifier": 1.2,
            "consistency": 0.8,
            "risk": 1.0,
            "contradictions": 1.0,
            "uncertainty": 1.0,
        }
        if weights:
            w.update(weights)
        return (
            w["evidence"] * evidence
            + w["confidence"] * confidence
            + w["verifier"] * verifier
            + w["consistency"] * consistency
            - w["risk"] * risk
            - w["contradictions"] * contradictions
            - w["uncertainty"] * uncertainty
        )

    @classmethod
    def pass_threshold(cls, base_threshold: float, risk_level: RiskLevel, lambda_risk: float = 0.5) -> float:
        return base_threshold + lambda_risk * cls.RISK_THRESHOLD_BONUS[risk_level]

    @classmethod
    def should_pass(
        cls,
        score: float,
        base_threshold: float,
        risk_level: RiskLevel,
        requires_approval: bool = False,
        has_approval: bool = False,
    ) -> bool:
        if requires_approval and not has_approval:
            return False
        return score >= cls.pass_threshold(base_threshold, risk_level)


def choose_next_action(
    current_utility: float,
    actions: Sequence[ActionOption],
    expected_utilities_after_observe: dict[str, float],
) -> list[tuple[str, float, float]]:
    """Return actions sorted by VOI then EU.

    Tuple = (name, voi, eu)
    """
    ranked: list[tuple[str, float, float]] = []
    for action in actions:
        voi = HardReasoningMath.value_of_information(
            current_utility=current_utility,
            expected_utility_after=expected_utilities_after_observe.get(action.name, current_utility),
            cost=action.cost,
        )
        eu = HardReasoningMath.expected_utility(action)
        ranked.append((action.name, voi, eu))
    return sorted(ranked, key=lambda x: (x[1], x[2]), reverse=True)
