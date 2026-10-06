"""authored_by_lane memapps: behavioural test for bossman.learning_guard.service (the one-call promotion guard).

No mocks of the unit under test: real SecretHoldout, real evaluate_ab / advance gates.
"""
from __future__ import annotations

import pytest

from bossman.learning_guard import service  # noqa: F401  (dotted: bossman.learning_guard.service)
from bossman.learning_guard import ABResult, Candidate, PromotionStage, SecuritySnapshot
from bossman.learning_guard.holdout import HoldoutViolation, SecretHoldout
from bossman.learning_guard.promotion import MIN_SHADOW_RUNS

CLEAN = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0)
BREACHED = SecuritySnapshot(leaks=5, bypasses=2, containment_rate=0.2)


@pytest.fixture(autouse=True)
def _reset_holdout():
    service.set_holdout(None)
    yield
    service.set_holdout(None)


def _rows(n=20, raw=True, guarded=True):
    return [ABResult(f"t{i}", "code", raw, guarded) for i in range(n)]


def test_holdout_is_noop_by_default():
    assert service.get_holdout() is None
    service.reject_if_holdout("anything")  # must not raise


def test_holdout_rejects_sealed_ids_and_passes_others():
    h = SecretHoldout.seal(["secret-1", "secret-2"])
    service.set_holdout(h)
    assert service.get_holdout() is h
    with pytest.raises(HoldoutViolation):
        service.reject_if_holdout("secret-1")
    service.reject_if_holdout("public-1")
    service.set_holdout(None)
    service.reject_if_holdout("secret-1")  # cleared -> no-op again


def test_guard_promotion_reaches_verified_with_clean_security_pair():
    c = Candidate("skill", "s-ok", stage=PromotionStage.SHADOW)
    moved, verdict = service.guard_promotion(c, _rows(), security_before=CLEAN, security_after=CLEAN,
                                             shadow_runs=MIN_SHADOW_RUNS)
    assert verdict.passing
    assert moved.stage is PromotionStage.VERIFIED


def test_guard_promotion_blocks_degraded_guarded_arm():
    results = _rows(20, raw=True, guarded=True)[:10] + [ABResult(f"d{i}", "code", True, False) for i in range(10)]
    c = Candidate("skill", "s-bad", stage=PromotionStage.SHADOW)
    moved, verdict = service.guard_promotion(c, results, security_before=CLEAN, security_after=CLEAN,
                                             shadow_runs=MIN_SHADOW_RUNS)
    assert not verdict.passing
    assert verdict.degradation_pp > 1.0
    assert moved.stage is PromotionStage.SHADOW


def test_guard_promotion_blocks_single_episode_promotion():
    c = Candidate("skill", "s-one", stage=PromotionStage.CANDIDATE)
    moved, verdict = service.guard_promotion(c, _rows(1))
    assert not verdict.enough_episodes
    assert moved.stage is PromotionStage.CANDIDATE


def test_guard_promotion_fails_closed_without_security_evidence_and_never_owner_promotes():
    c = Candidate("skill", "s-nosec", stage=PromotionStage.SHADOW)
    moved, _ = service.guard_promotion(c, _rows(), shadow_runs=MIN_SHADOW_RUNS)
    assert moved.stage is not PromotionStage.VERIFIED
    assert moved.stage is not PromotionStage.OWNER_PROMOTED
