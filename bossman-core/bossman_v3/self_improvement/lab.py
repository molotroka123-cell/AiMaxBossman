from __future__ import annotations
from dataclasses import dataclass, fields

@dataclass(frozen=True)
class BenchmarkResult:
    verified_success: float
    quality: float
    cost: float
    latency: float
    tokens: float
    peak_ram: float
    peak_vram: float
    retries: float
    security_failures: int

@dataclass(frozen=True)
class ExperimentDecision:
    promotable: bool
    pareto_improvement: bool
    net_value: float
    reasons: tuple[str,...]

LOWER_BETTER={"cost","latency","tokens","peak_ram","peak_vram","retries","security_failures"}
HIGHER_BETTER={"verified_success","quality"}

def dominates(a: BenchmarkResult, b: BenchmarkResult) -> bool:
    no_worse=True; better=False
    for name in HIGHER_BETTER:
        av,bv=getattr(a,name),getattr(b,name); no_worse &= av>=bv; better |= av>bv
    for name in LOWER_BETTER:
        av,bv=getattr(a,name),getattr(b,name); no_worse &= av<=bv; better |= av<bv
    return bool(no_worse and better)

class SelfImprovementLab:
    """Evaluates candidate architecture changes; never merges, pushes, or deploys them."""
    def evaluate(self, baseline: BenchmarkResult, candidate: BenchmarkResult, *, delta_verified_utility: float, delta_resource_cost: float, delta_complexity_cost: float, max_quality_drop: float=0.01) -> ExperimentDecision:
        reasons=[]
        if candidate.security_failures > baseline.security_failures:
            reasons.append("security failures increased")
        if baseline.verified_success-candidate.verified_success > max_quality_drop:
            reasons.append("verified success degraded by more than production gate")
        pareto=dominates(candidate,baseline)
        net=delta_verified_utility-delta_resource_cost-delta_complexity_cost
        if not pareto: reasons.append("candidate is not a strict Pareto improvement")
        if net <= 0: reasons.append("architecture ROI is non-positive")
        return ExperimentDecision(not reasons, pareto, net, tuple(reasons))

    @staticmethod
    def efficiency(quality: float, effective_cost: float) -> float:
        return quality/max(effective_cost,1e-12)

    @classmethod
    def architecture_amplification_factor(cls, bossman_quality: float, bossman_cost: float, direct_quality: float, direct_cost: float) -> float:
        return cls.efficiency(bossman_quality,bossman_cost)/max(cls.efficiency(direct_quality,direct_cost),1e-12)
