"""Invalid evidence must not authorize a self-improvement promotion."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bossman-core"))
from bossman_v3.self_improvement.lab import BenchmarkResult, SelfImprovementLab


def metric(**kw):
    values = dict(verified_success=.9, quality=.9, cost=2., latency=1., tokens=100.,
                  peak_ram=100., peak_vram=0., retries=0., security_failures=0)
    values.update(kw)
    return BenchmarkResult(**values)


@pytest.mark.parametrize("overrides", [{"cost": -1}, {"quality": 2}, {"verified_success": float("nan")},
    {"cost": float("inf")}, {"security_failures": .5}, {"tokens": True}])
def test_invalid_candidate_cannot_promote(overrides):
    decision = SelfImprovementLab().evaluate(metric(), metric(**overrides),
        delta_verified_utility=5, delta_resource_cost=0, delta_complexity_cost=0)
    assert not decision.promotable


def test_nan_utility_and_existing_security_failures_cannot_promote():
    lab = SelfImprovementLab()
    assert not lab.evaluate(metric(), metric(cost=1), delta_verified_utility=float("nan"),
        delta_resource_cost=0, delta_complexity_cost=0).promotable
    assert not lab.evaluate(metric(security_failures=1), metric(cost=1, security_failures=1),
        delta_verified_utility=5, delta_resource_cost=0, delta_complexity_cost=0).promotable


def test_valid_cost_improvement_still_promotes():
    assert SelfImprovementLab().evaluate(metric(), metric(cost=1), delta_verified_utility=1,
        delta_resource_cost=0, delta_complexity_cost=0).promotable
