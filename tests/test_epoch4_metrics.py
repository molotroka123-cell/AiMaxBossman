"""Synthetic arithmetic fixtures; no measured Bossman speedup is claimed."""
from dataclasses import replace

import pytest

from bossman_shared.epoch4_metrics import Dataset, Measurement, evaluate


def datasets():
    manifest = {f"pair-{i}": f"family-{i // 40}" for i in range(120)}
    config = tuple((k, v) for k, v in {"hardware": "same-fixture-host", "platform": "linux",
        "models": "same-model", "permissions": "same-grants", "resource_envelope": "fixed",
        "workload": "fixed-manifest", "execution_mode": "serial"}.items())
    rows = tuple(Measurement(k, f, 12, 12, True, 3, 1, 0, f"fixture/{k}") for k, f in manifest.items())
    base = Dataset("a" * 40, False, config, rows)
    candidate = Dataset("b" * 40, False, config, tuple(replace(r, elapsed_seconds=3, cost_usd=3,
                                                           avoidable_interventions=0) for r in rows))
    return manifest, base, candidate


def test_numeric_targets_are_not_certification_and_approvals_remain_counted():
    m, b, c = datasets()
    result = evaluate(m, b, c, bootstrap_samples=200)
    assert result["verdict"] == "MET"
    assert result["certified"] is False and result["source_trust"].startswith("UNVERIFIED")
    assert result["ratios"]["throughput"] == 4
    assert result["ratios"]["cost_per_verified"] == .25
    assert result["candidate"]["mandatory_approvals"] == 120
    assert result == evaluate(m, b, c, bootstrap_samples=200)


@pytest.mark.parametrize("change", [
    lambda c: replace(c, records=c.records[:-1]),
    lambda c: replace(c, records=c.records + c.records[:1]),
    lambda c: replace(c, dirty_tree=True),
    lambda c: replace(c, commit_sha="unknown"),
    lambda c: replace(c, configuration=c.configuration[:-1]),
    lambda c: replace(c, configuration=tuple((k, "other" if k == "models" else v) for k, v in c.configuration)),
    lambda c: replace(c, records=(replace(c.records[0], cost_usd=None),) + c.records[1:]),
    lambda c: replace(c, records=(replace(c.records[0], cost_usd=float("nan")),) + c.records[1:]),
    lambda c: replace(c, records=(replace(c.records[0], elapsed_seconds=-1),) + c.records[1:]),
    lambda c: replace(c, records=(replace(c.records[0], evidence_ref=""),) + c.records[1:]),
    lambda c: replace(c, records=(replace(c.records[0], avoidable_interventions=True),) + c.records[1:]),
])
def test_missing_or_incomparable_measurements_cannot_claim_target(change):
    m, b, c = datasets()
    assert evaluate(m, b, change(c), bootstrap_samples=200)["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_failed_attempts_remain_in_cost_and_time():
    m, b, c = datasets()
    c = replace(c, records=tuple(replace(r, verified_result=False, cost_usd=100, elapsed_seconds=100)
                                  if i < 40 else r for i, r in enumerate(c.records)))
    result = evaluate(m, b, c, bootstrap_samples=200)
    assert result["verdict"] == "NOT_MET"
    assert result["candidate"]["cost_usd"] == 4240
    assert result["candidate"]["verified"] == 80
    assert not result["gates"]["success_noninferior"]


def test_sparse_samples_and_zero_cost_are_not_infinite_improvement():
    m, b, c = datasets()
    short = dict(list(m.items())[:40])
    assert evaluate(short, replace(b, records=b.records[:40]), replace(c, records=c.records[:40]))["verdict"] == "INSUFFICIENT_EVIDENCE"
    b = replace(b, records=tuple(replace(r, cost_usd=0) for r in b.records))
    assert evaluate(m, b, c)["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_zero_interventions_are_preserved_without_division_by_zero():
    m, b, c = datasets()
    b = replace(b, records=tuple(replace(r, avoidable_interventions=0) for r in b.records))
    result = evaluate(m, b, c, bootstrap_samples=200)
    assert result["verdict"] == "MET" and result["ratios"]["avoidable_interventions"] is None
    c = replace(c, records=(replace(c.records[0], avoidable_interventions=1),) + c.records[1:])
    assert evaluate(m, b, c, bootstrap_samples=200)["verdict"] == "NOT_MET"


def test_reported_unsafe_event_prevents_even_numeric_target_claim():
    m, b, c = datasets()
    c = replace(c, records=(replace(c.records[0], unsafe_events=1),) + c.records[1:])
    assert evaluate(m, b, c, bootstrap_samples=200)["verdict"] == "NOT_MET"


def test_extreme_measurements_fail_without_uncaught_arithmetic_error():
    m, b, c = datasets()
    b = replace(b, records=tuple(replace(r, cost_usd=5e-324) for r in b.records))
    c = replace(c, records=tuple(replace(r, cost_usd=1e300) for r in c.records))
    assert evaluate(m, b, c, bootstrap_samples=200)["verdict"] == "INSUFFICIENT_EVIDENCE"
