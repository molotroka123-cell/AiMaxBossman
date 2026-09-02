"""Learning Quality Guard / Anti-Degradation Layer — все 10 требований."""
import pytest

import bossman.learning_guard as lg
from bossman.learning_guard import (ABResult, Candidate, PromotionStage,
                                     RollbackInfo, SecretHoldout, SecuritySnapshot)


def _mk(n, task_class, raw_ok, guarded_ok, self_score=None):
    return [ABResult(f"{task_class}-{i}", task_class, raw_ok, guarded_ok,
                     bossman_self_score=self_score) for i in range(n)]


# req.1 — same-model Raw vs Model+Bossman A/B: агрегируется по verified-полям
def test_ab_aggregates_same_model_raw_vs_guarded():
    res = _mk(10, "code", True, True) + _mk(10, "code", False, True)
    v = lg.evaluate_ab(res)
    assert v.episodes == 20
    assert v.raw_success == 0.5 and v.guarded_success == 1.0
    assert v.degradation_pp <= 0  # guarded лучше


# req.3 — degradation ≤ 1pp
def test_degradation_gate_blocks_over_1pp():
    # raw 100% verified, guarded теряет 5% → -5pp деградация
    res = _mk(19, "code", True, True) + _mk(1, "code", True, False)
    v = lg.evaluate_ab(res)
    assert v.degradation_pp == pytest.approx(5.0)
    assert not v.passing and any("degradation" in r for r in v.reasons)


def test_small_degradation_within_1pp_passes():
    # 200 задач, guarded теряет ровно 1 (0.5pp) → в пределах 1pp
    res = _mk(199, "code", True, True) + _mk(1, "code", True, False)
    v = lg.evaluate_ab(res)
    assert v.degradation_pp == pytest.approx(0.5) and v.passing


# req.4 — IntelligenceRetention ≥ 0.99
def test_retention_gate():
    res = _mk(90, "code", True, True) + _mk(10, "code", True, False)
    v = lg.evaluate_ab(res)                        # retention 0.9
    assert v.intelligence_retention == pytest.approx(0.9)
    assert not v.passing and any("retention" in r for r in v.reasons)


# req.5 — запрет single-episode promotion
def test_single_episode_never_passes():
    v = lg.evaluate_ab(_mk(1, "code", True, True))
    assert not v.passing and not v.enough_episodes
    assert any("insufficient episodes" in r for r in v.reasons)


# req.6 — Bossman self-score ≠ evidence: self_score не влияет на вердикт
def test_self_score_is_not_evidence():
    low = lg.evaluate_ab(_mk(30, "code", True, True, self_score=0.01))
    high = lg.evaluate_ab(_mk(30, "code", True, True, self_score=0.99))
    assert low.passing == high.passing == True
    assert low.guarded_success == high.guarded_success   # self-score не сдвинул метрику


# req.9 — per-task-class regression gate: класс не должен просесть, даже если среднее ок
def test_per_class_regression_blocks_even_if_overall_ok():
    good = _mk(180, "code", True, True)              # code без деградации
    bad = _mk(20, "web", True, False)                # web полностью просел
    v = lg.evaluate_ab(good + bad)
    assert not v.per_class_ok and not v.passing
    assert any("class web" in r for r in v.reasons)


# req.2 — secret holdout недоступен learning/memory/skills
def test_secret_holdout_rejects_and_cannot_be_enumerated():
    h = SecretHoldout.seal(["t-secret-1", "t-secret-2"])
    assert h.is_holdout("t-secret-1") and not h.is_holdout("t-open")
    with pytest.raises(lg.HoldoutViolation):
        h.reject_if_holdout("t-secret-2")
    # нельзя перечислить holdout (нет .list()/.ids()) — train-around невозможен
    assert not hasattr(h, "list") and not hasattr(h, "ids")
    assert h.filter_learnable(["t-open", "t-secret-1"]) == ["t-open"]


# req.7 — конвейер candidate→validation→shadow→verified→owner, автопромоушена нет
def test_promotion_pipeline_requires_each_stage_and_owner():
    ok = lg.evaluate_ab(_mk(40, "code", True, True))
    c = Candidate(kind="skill", ref="s1")
    c = lg.advance(c, ab=ok)                          # CANDIDATE→VALIDATION
    assert c.stage is PromotionStage.VALIDATION
    c = lg.advance(c, ab=ok)                          # →SHADOW
    assert c.stage is PromotionStage.SHADOW
    snap = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0)
    c = lg.advance(c, ab=ok, security_before=snap, security_after=snap,
                   shadow_runs=5)                    # мало shadow-прогонов
    assert c.stage is PromotionStage.SHADOW
    # AUDIT-ONLY-001/F4: SHADOW→VERIFIED — единственный переход, дающий право на
    # OWNER_PROMOTED, поэтому без ПОЛНОЙ пары security-снимков он fail-closed.
    blocked = lg.advance(c, ab=ok, shadow_runs=20)
    assert blocked.stage is PromotionStage.SHADOW and blocked.reasons
    half = lg.advance(c, ab=ok, security_before=snap, shadow_runs=20)
    assert half.stage is PromotionStage.SHADOW and half.reasons
    c = lg.advance(c, ab=ok, security_before=snap, security_after=snap,
                   shadow_runs=20)                   # →VERIFIED (с доказательством)
    assert c.stage is PromotionStage.VERIFIED and c.security_proven
    # без owner — не продвигается
    assert lg.promote(c, owner_approved=False,
                      rollback=RollbackInfo("verified", "s0")).stage is PromotionStage.VERIFIED
    done = lg.promote(c, owner_approved=True, rollback=RollbackInfo("verified", "s0"))
    assert done.stage is PromotionStage.OWNER_PROMOTED and done.rollback is not None


def test_failing_ab_does_not_advance():
    bad = lg.evaluate_ab(_mk(2, "code", True, False))   # мало эпизодов + деградация
    c = lg.advance(Candidate("memory", "m1"), ab=bad)
    assert c.stage is PromotionStage.CANDIDATE and c.reasons


# security hard gates неоптимизируемы ради score
def test_security_regression_blocks_even_with_passing_ab():
    ok = lg.evaluate_ab(_mk(40, "code", True, True))
    before = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0)
    after = SecuritySnapshot(leaks=1, bypasses=0, containment_rate=1.0)   # утечка появилась
    with pytest.raises(lg.SecurityRegression):
        lg.advance(Candidate("config", "c1"), ab=ok,
                   security_before=before, security_after=after)


# req.10 — rollback-метаданные обязательны при promotion
def test_promotion_carries_rollback_metadata():
    ok = lg.evaluate_ab(_mk(40, "code", True, True))
    # AUDIT-ONLY-001/F4: стадия VERIFIED — метка, а не доказательство; OWNER_PROMOTED
    # дополнительно требует записанного security-доказательства (`security_proven`,
    # выставляется только в advance() после сравнения полной пары снимков).
    unproven = Candidate("skill", "s2", stage=PromotionStage.VERIFIED)
    rb = RollbackInfo(prev_stage="verified", prev_ref="s1", reason="baseline")
    refused = lg.promote(unproven, owner_approved=True, rollback=rb)
    assert refused.stage is PromotionStage.VERIFIED and refused.rollback is None
    assert any("security" in r for r in refused.reasons)
    c = Candidate("skill", "s2", stage=PromotionStage.VERIFIED, security_proven=True)
    done = lg.promote(c, owner_approved=True, rollback=rb)
    assert done.rollback == rb


# req.8 — context raw-evidence fallback
def test_context_fallback_to_raw_when_retention_drops():
    assert lg.context_fallback_to_raw(0.95) is True     # guarded хуже → raw
    assert lg.context_fallback_to_raw(0.999) is False   # guarded держит → оставить


# ---- тонкий адаптер service: composite gate + process-level holdout ----

def test_service_guard_promotion_composite(monkeypatch):
    ok = _mk(40, "code", True, True)
    c = Candidate("skill", "s3")
    moved, verdict = lg.guard_promotion(c, ok)
    assert verdict.passing and moved.stage is PromotionStage.VALIDATION


def test_service_holdout_is_optional_and_noop_by_default(monkeypatch):
    lg.set_holdout(None)
    lg.reject_if_holdout("anything")           # no-op, не бросает
    lg.set_holdout(SecretHoldout.seal(["hid-1"]))
    with pytest.raises(lg.HoldoutViolation):
        lg.reject_if_holdout("hid-1")
    lg.reject_if_holdout("open-task")          # не holdout — ок
    lg.set_holdout(None)                        # cleanup


def test_service_guard_promotion_blocks_on_security_regression():
    ok = _mk(40, "code", True, True)
    before = SecuritySnapshot(containment_rate=1.0)
    after = SecuritySnapshot(containment_rate=0.9)   # containment просел
    with pytest.raises(lg.SecurityRegression):
        lg.guard_promotion(Candidate("config", "c2"), ok,
                           security_before=before, security_after=after)
