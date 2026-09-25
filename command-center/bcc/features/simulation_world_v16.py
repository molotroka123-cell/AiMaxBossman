"""Typed scenario aggregation. Simulations are distributions, never facts."""
from __future__ import annotations
from dataclasses import dataclass
from statistics import median
from math import isfinite


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    probability_weight: float
    metrics: dict[str,float]
    evidence_class: str = "SIMULATED"

    def __post_init__(self):
        if not self.scenario_id or not isfinite(self.probability_weight) or self.probability_weight < 0:
            raise ValueError("invalid scenario weight")
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not isfinite(v) for v in self.metrics.values()):
            raise ValueError("scenario metrics must be finite numbers")


def summarize(scenarios: list[Scenario], metric: str) -> dict:
    xs=[s for s in scenarios if metric in s.metrics and s.probability_weight>0]
    if not xs:
        return {"status":"NO_SCENARIOS"}
    total=sum(s.probability_weight for s in xs)
    weighted=sum(s.metrics[metric]*s.probability_weight for s in xs)/total
    values=sorted(s.metrics[metric] for s in xs)
    return {"status":"SIMULATED","metric":metric,"weighted_mean":weighted,
            "median":median(values),"min":values[0],"max":values[-1],"n":len(xs)}
