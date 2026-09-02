"""PASS3 Autonomy Trainer — shadow-only через Learning Guard; promotion gates."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bossman.learning_guard import autonomy_trainer as at
from bossman.learning_guard.holdout import SecretHoldout
from bossman.learning_guard.models import ABResult, RollbackInfo, SecuritySnapshot

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "autonomy_candidate.schema.json").read_text())


def _ep(i, **over):
    base = dict(task_id=f"t{i}", state_hash="s", action_type="click", semantic_anchor="button:Save",
                fresh_observation=True, verified_success=True, planner_principal="agent:planner#r1",
                verifier_principal="tool:verifier#r2", environment_fingerprint="env-a", model_version="qwen-14b@1")
    base.update(over)
    return at.Episode(**base)


def _cand(kind="context", **over):
    base = dict(candidate_id="c1", kind=kind, scope={"task_class": "fix", "risky": False},
                hypothesis="slice helps", rollback_ref="cfg@v1")
    base.update(over)
    return at.AutonomyCandidate(**base)


def test_flag_off_records_nothing(monkeypatch):
    monkeypatch.delenv(at.FLAG, raising=False)
    assert at.record_candidate(_cand()) is None
    monkeypatch.setenv(at.FLAG, "1")
    d = at.record_candidate(_cand())
    assert set(SCHEMA["required"]) <= set(d) and d["status"] == "CANDIDATE"


@pytest.mark.parametrize("bad, why", [
    (dict(semantic_anchor=""), "semantic anchor"),
    (dict(self_reported_only=True), "self-reported"),
    (dict(fresh_observation=False), "self-reported"),
    (dict(stale_session=True), "stale"),
    (dict(contains_hidden_cot=True), "chain-of-thought"),
    (dict(verifier_principal="agent:planner#r1"), "not independent"),
    (dict(verifier_independence_class="same_model"), "independence class"),
])
def test_forbidden_learning_sources_rejected(bad, why):
    assert why in at.episode_rejection(_ep(1, **bad))
    assert at.episode_rejection(_ep(1)) == ""


def test_holdout_episode_quarantines():
    hold = SecretHoldout.seal(["t2"])
    c = at.evaluate_candidate(_cand(), [_ep(1), _ep(2), _ep(3)], holdout=hold, baseline_success=0.5)
    assert c.status == "QUARANTINED" and c.holdout_touched is True


def test_insufficient_samples_is_not_promotion_and_risky_needs_more():
    c = at.evaluate_candidate(_cand(), [_ep(1), _ep(2)])
    assert c.status == "CANDIDATE" and any("INSUFFICIENT_EVIDENCE" in r for r in c.reasons)
    ok = at.evaluate_candidate(_cand(), [_ep(1), _ep(2), _ep(3)], baseline_success=0.9)
    assert ok.status == "SHADOW" and ok.sample_count == 3 and ok.independently_verified
    risky = at.evaluate_candidate(_cand(kind="route"), [_ep(i) for i in range(5)])
    assert risky.status == "CANDIDATE" and any("< 10" in r for r in risky.reasons)
    risky_ok = at.evaluate_candidate(_cand(kind="route"), [_ep(i) for i in range(10)], baseline_success=0.5)
    assert risky_ok.status == "SHADOW"


def test_scope_must_be_explicit_and_success_non_inferior():
    mixed = at.evaluate_candidate(_cand(), [_ep(1), _ep(2), _ep(3, environment_fingerprint="env-b")], baseline_success=0.5)
    assert mixed.status == "CANDIDATE" and any("environment" in r for r in mixed.reasons)
    inferior = at.evaluate_candidate(_cand(), [_ep(1), _ep(2), _ep(3), _ep(4, verified_success=False)],
                                     baseline_success=0.9)
    assert inferior.status == "CANDIDATE" and any("inferior" in r for r in inferior.reasons)
    assert at.evaluate_candidate(_cand(), [_ep(1), _ep(2, false_success=True), _ep(3)]).status == "REJECTED"
    assert at.evaluate_candidate(_cand(), [_ep(1), _ep(2, security_regression=True), _ep(3)]).status == "QUARANTINED"


def test_promotion_goes_through_learning_guard_and_owner():
    shadow = at.evaluate_candidate(_cand(), [_ep(1), _ep(2), _ep(3)], baseline_success=0.5)
    ab = [ABResult(task_id=f"t{i}", task_class="fix", raw_verified=False, guarded_verified=True) for i in range(20)]
    snap = SecuritySnapshot()
    rb = RollbackInfo(prev_stage="SHADOW", prev_ref="cfg@v0")
    assert "rollback not tested" in at.promote_candidate(shadow, ab, security_before=snap, security_after=snap,
                                                         shadow_runs=20, owner_approved=True, rollback_tested=False,
                                                         rollback=rb).reasons
    no_owner = at.promote_candidate(shadow, ab, security_before=snap, security_after=snap, shadow_runs=20,
                                    owner_approved=False, rollback_tested=True, rollback=rb)
    assert no_owner.status == "SHADOW" and any("owner" in r for r in no_owner.reasons)
    regressed = at.promote_candidate(shadow, ab, security_before=snap, security_after=SecuritySnapshot(leaks=1),
                                     shadow_runs=20, owner_approved=True, rollback_tested=True, rollback=rb)
    assert regressed.status == "QUARANTINED"
    promoted = at.promote_candidate(shadow, ab, security_before=snap, security_after=snap, shadow_runs=20,
                                    owner_approved=True, rollback_tested=True, rollback=rb)
    assert promoted.status == "PROMOTED"
    assert at.rollback_candidate(promoted, "drift").status == "ROLLED_BACK"
    assert at.promote_candidate(_cand(), ab, security_before=snap, security_after=snap, shadow_runs=20,
                                owner_approved=True, rollback_tested=True, rollback=rb).status == "CANDIDATE"


def test_shadow_requires_baseline_and_verified_successes():
    """Audit P0: без измеренного baseline и без успешных verified-эпизодов SHADOW невозможен."""
    no_baseline = at.evaluate_candidate(_cand(), [_ep(1), _ep(2), _ep(3)])
    assert no_baseline.status == "CANDIDATE" and any("baseline" in r for r in no_baseline.reasons)
    all_failed = at.evaluate_candidate(_cand(), [_ep(i, verified_success=False) for i in range(1, 4)],
                                       baseline_success=0.0)
    assert all_failed.status == "CANDIDATE" and any("successful" in r for r in all_failed.reasons)
    ok = at.evaluate_candidate(_cand(), [_ep(1), _ep(2), _ep(3)], baseline_success=0.5)
    assert ok.status == "SHADOW"
