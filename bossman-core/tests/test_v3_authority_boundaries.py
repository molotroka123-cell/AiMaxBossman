"""V3 security boundaries: Self-Improvement Lab остаётся PROPOSAL-ONLY.

Лаборатория может ОЦЕНИВАТЬ и ПРЕДЛАГАТЬ, но никогда не мержит/пушит/деплоит,
не меняет политику и не выдаёт себе скоупы. Плюс — блокировка продвижения при
security-регрессии и при падении verified-success сверх продакшн-гейта.
"""
from __future__ import annotations

import inspect

import pytest

from bossman_v3.self_improvement.lab import BenchmarkResult, SelfImprovementLab


def _bench(**kw):
    base = dict(verified_success=0.90, quality=0.80, cost=1.0, latency=1.0, tokens=100.0,
                peak_ram=100.0, peak_vram=0.0, retries=1.0, security_failures=0)
    base.update(kw)
    return BenchmarkResult(**base)


def test_lab_exposes_no_merge_push_deploy_or_authority_surface():
    """У лаборатории нет и не должно быть методов, меняющих мир."""
    forbidden = {"merge", "push", "deploy", "promote", "apply", "commit",
                 "grant", "grant_scope", "set_policy", "install", "release"}
    public = {n for n, _ in inspect.getmembers(SelfImprovementLab, callable) if not n.startswith("_")}
    assert not (public & forbidden), f"authority-escalating surface exposed: {public & forbidden}"


def test_lab_source_performs_no_io_or_subprocess():
    """Proposal-only: ни подпроцессов, ни сети, ни записи на диск."""
    src = inspect.getsource(SelfImprovementLab)
    for bad in ("subprocess", "os.system", "requests", "httpx", "open(", "shutil", "git "):
        assert bad not in src, f"lab performs side-effecting call: {bad}"


def test_security_regression_blocks_promotion():
    lab = SelfImprovementLab()
    d = lab.evaluate(_bench(), _bench(security_failures=1, quality=0.99),
                     delta_verified_utility=10, delta_resource_cost=0, delta_complexity_cost=0)
    assert d.promotable is False
    assert any("security" in r for r in d.reasons)


def test_quality_degradation_beyond_gate_blocks_promotion():
    lab = SelfImprovementLab()
    d = lab.evaluate(_bench(verified_success=0.90), _bench(verified_success=0.80),
                     delta_verified_utility=10, delta_resource_cost=0, delta_complexity_cost=0)
    assert d.promotable is False
    assert any("verified success" in r for r in d.reasons)


def test_non_positive_roi_blocks_promotion():
    lab = SelfImprovementLab()
    d = lab.evaluate(_bench(), _bench(quality=0.85, cost=0.5),
                     delta_verified_utility=1.0, delta_resource_cost=2.0, delta_complexity_cost=0.0)
    assert d.promotable is False
    assert any("ROI" in r or "non-positive" in r for r in d.reasons)


def test_promotable_is_only_a_proposal_flag():
    """Даже 'promotable' — лишь ПРЕДЛОЖЕНИЕ: решение возвращается, мир не меняется."""
    lab = SelfImprovementLab()
    d = lab.evaluate(_bench(), _bench(quality=0.95, cost=0.5, latency=0.5, tokens=50.0,
                                      peak_ram=50.0, retries=0.0),
                     delta_verified_utility=5.0, delta_resource_cost=1.0, delta_complexity_cost=1.0)
    assert d.promotable is True and d.pareto_improvement is True
    # это чистые данные, а не действие
    assert not hasattr(d, "execute") and not hasattr(d, "apply")
