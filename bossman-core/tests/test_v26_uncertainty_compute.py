"""V2.6 Phase 2 — Uncertainty Engine (модуль A) + Adaptive Compute (модуль B).

Всё детерминировано, без LLM/сети. Матрица из раздела 31 V2.6: high-evidence /
missing evidence / contradiction / stale / verifier failure / high-risk /
model-confidence manipulation; fast path / escalation / EVR stop.
"""
from __future__ import annotations

import pytest

from bossman import compute_budget as cb
from bossman import uncertainty as unc
from bossman.signals import DecisionSignals, derive_signals


# ---------------- Uncertainty Engine ----------------

def test_high_evidence_task_scores_zero():
    s = unc.estimate()
    assert s.score == 0.0 and s.reasons == ()


def test_each_component_raises_score():
    base = unc.estimate().score
    for kw in ("evidence_gap", "contradiction", "verifier_failure", "staleness",
               "tool_uncertainty", "risk", "failure_history"):
        s = unc.estimate(**{kw: 1.0})
        assert s.score > base, kw
        assert any(kw in r for r in s.reasons), kw


def test_full_uncertainty_is_normalized_to_one():
    s = unc.estimate(evidence_gap=1, contradiction=1, verifier_failure=1,
                     staleness=1, tool_uncertainty=1, risk=1, failure_history=1)
    assert s.score == pytest.approx(1.0)


def test_inputs_are_clamped():
    assert unc.estimate(evidence_gap=99).score == unc.estimate(evidence_gap=1.0).score
    assert unc.estimate(contradiction=-5).score == 0.0


def test_model_confidence_cannot_lower_uncertainty():
    """Манипуляция: модель «звучит уверенно» — системный score НЕ падает."""
    s = unc.estimate(evidence_gap=0.8, contradiction=0.6)
    assert unc.apply_model_confidence(s, 1.0).score == s.score
    assert unc.apply_model_confidence(s, 0.99).score == s.score


def test_low_model_confidence_raises_uncertainty():
    s = unc.estimate(evidence_gap=0.2)
    bumped = unc.apply_model_confidence(s, 0.1)
    assert bumped.score > s.score
    assert any("self-confidence" in r for r in bumped.reasons)


def test_signal_carries_task_class_and_evidence_refs():
    s = unc.estimate(evidence_gap=0.5, task_class="coding",
                     evidence_refs=("chunk:42",))
    assert s.task_class == "coding" and s.evidence_refs == ("chunk:42",)


# ---------------- Adaptive Compute ----------------

def test_trivial_task_selects_c0_fast():
    sig = derive_signals("посчитай 2+2")
    level, reasons = cb.select_level(sig)
    assert level is cb.ComputeLevel.C0_FAST
    assert any("тривиально" in r for r in reasons)


def test_high_risk_selects_c4_max_verification():
    sig = DecisionSignals(risk=0.9)
    level, _ = cb.select_level(sig)
    assert level is cb.ComputeLevel.C4_MAX_VERIFICATION


def test_high_uncertainty_selects_c3():
    sig = DecisionSignals(uncertainty=0.8)
    assert cb.select_level(sig)[0] is cb.ComputeLevel.C3_MULTI_CANDIDATE


def test_medium_complexity_selects_c2_default_c1():
    assert cb.select_level(DecisionSignals(task_complexity=0.7))[0] is cb.ComputeLevel.C2_DEEP
    assert cb.select_level(DecisionSignals(task_complexity=0.4))[0] is cb.ComputeLevel.C1_NORMAL


def test_exhausted_budget_downgrades_except_high_risk():
    starving = DecisionSignals(task_complexity=0.9, resource_budget=0.05)
    assert cb.select_level(starving)[0] is cb.ComputeLevel.C1_NORMAL
    risky = DecisionSignals(risk=0.9, resource_budget=0.05)
    assert cb.select_level(risky)[0] is cb.ComputeLevel.C4_MAX_VERIFICATION


def test_evr_stop_and_continue():
    positive = cb.evr(0.8, delta_success=0.5, token_cost=0.1)
    negative = cb.evr(0.1, delta_success=0.1, token_cost=0.5, latency_cost=0.2)
    assert cb.should_continue_reasoning(positive)
    assert not cb.should_continue_reasoning(negative)


def test_voi_skips_useless_optional_action():
    useless = cb.voi(expected_uncertainty_after=0.5, uncertainty_now=0.5, cost=0.1)
    useful = cb.voi(expected_uncertainty_after=0.1, uncertainty_now=0.6, cost=0.1)
    assert cb.may_skip("retrieve_more_context", useless)
    assert not cb.may_skip("retrieve_more_context", useful)


def test_mandatory_security_verification_never_skipped():
    """Security hard gate не оптимизируется экономикой — даже при VOI<=0."""
    assert not cb.may_skip("security_verification", -1.0)
    assert not cb.may_skip("egress_guard", -1.0)
    assert not cb.may_skip("approval", 0.0)


def test_controller_overhead_is_negligible():
    import time
    sig = derive_signals("исследуй рынок и создай отчёт")
    t0 = time.perf_counter()
    for _ in range(1000):
        cb.select_level(sig)
        unc.estimate(risk=0.3, failure_history=0.2)
    assert (time.perf_counter() - t0) < 0.5, "1000 итераций контроллера < 0.5s"


# ---------------- runner wiring (за флагом) ----------------

@pytest.mark.asyncio
async def test_select_compute_disabled_returns_none(monkeypatch):
    from bossman.config import settings
    from bossman.runner import _select_compute
    monkeypatch.setattr(settings, "adaptive_compute", False, raising=False)
    level, reasons = await _select_compute({"id": 1, "text": "посчитай 2+2"})
    assert level is None and reasons == ()
