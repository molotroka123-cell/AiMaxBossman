"""Audit 2026-09-28: one known task repeated N times is one piece of evidence.

No threshold changes: MIN_EPISODES, min_samples, MIN_SHADOW_RUNS are untouched.
What changed is what counts toward them (unique tasks), that a 0/0 A/B is not
"no degradation", and that the single-use ledger keys on the measurement only.
"""
from __future__ import annotations

from dataclasses import replace

from bossman.learning_guard.ab import evaluate_ab
from bossman.learning_guard.autonomy_trainer import (AutonomyCandidate, Episode, corpus_fingerprint,
                                                     evaluate_candidate, promote_candidate)
from bossman.learning_guard.evidence_ledger import EvidenceLedger, evidence_key
from bossman.learning_guard.models import ABResult, RollbackInfo, SecuritySnapshot

SCOPE = {"task_class": "fix", "environment": "win", "model_version": "m1"}


def _ep(task_id: str, ok: bool = True) -> Episode:
    return Episode(task_id=task_id, state_hash="h", action_type="fix", semantic_anchor="x", fresh_observation=True,
                   verified_success=ok, planner_principal="student", verifier_principal="pytest",
                   environment_fingerprint="win", model_version="m1")


def _cand(cid: str = "c1") -> AutonomyCandidate:
    return AutonomyCandidate(candidate_id=cid, kind="skill", scope=dict(SCOPE), hypothesis="h", rollback_ref=f"{cid}@v1")


def _ab(task_id: str, raw: bool, guarded: bool) -> ABResult:
    return ABResult(task_id=task_id, task_class="fix", raw_verified=raw, guarded_verified=guarded)


def test_one_task_repeated_three_times_is_one_episode():
    ev = evaluate_candidate(_cand(), [_ep("known-1")] * 3, baseline_success=0.0)
    assert ev.status == "CANDIDATE" and ev.sample_count == 1
    assert any("INSUFFICIENT_EVIDENCE" in r for r in ev.reasons)
    assert any("repeated episode" in r for r in ev.reasons)
    distinct = evaluate_candidate(_cand(), [_ep(f"t{i}") for i in range(3)], baseline_success=0.0)
    assert distinct.status == "SHADOW" and distinct.sample_count == 3          # the gate itself is unchanged


def test_one_ab_row_repeated_twenty_times_is_not_twenty_episodes():
    verdict = evaluate_ab([_ab("t1", True, True)] * 20)
    assert verdict.episodes == 1 and not verdict.passing
    assert evaluate_ab([_ab(f"t{i}", True, True) for i in range(20)]).passing


def test_duplicate_rows_of_a_task_merge_conservatively():
    rows = [_ab(f"t{i}", False, True) for i in range(20)] + [_ab("t0", True, False)]
    verdict = evaluate_ab(rows)
    assert verdict.episodes == 20 and verdict.guarded_success == 0.95 and verdict.raw_success == 0.05


def test_all_fail_ab_is_not_no_degradation():
    verdict = evaluate_ab([_ab(f"t{i}", False, False) for i in range(20)])
    assert not verdict.passing and any("0/0" in r for r in verdict.reasons)
    assert evaluate_ab([_ab(f"t{i}", False, i % 2 == 0) for i in range(20)]).passing   # real improvement still passes


def test_ledger_key_ignores_non_evidence_fields_and_duplicates():
    rows = [_ab(f"t{i}", True, True) for i in range(20)]
    snap = SecuritySnapshot(scope_ref="r")
    base = evidence_key(rows, snap, snap, 20)
    assert evidence_key(rows, snap, snap, 21) == base
    assert evidence_key([replace(r, raw_tokens=99, bossman_self_score=0.9) for r in rows], snap, snap, 20) == base
    assert evidence_key(rows + rows[:1], snap, snap, 20) == base
    assert evidence_key(rows[:-1] + [_ab("t19", True, False)], snap, snap, 20) != base


def test_the_same_measurement_cannot_be_respent_by_restating_non_evidence_fields():
    ev = evaluate_candidate(_cand(), [_ep(f"t{i}") for i in range(3)], baseline_success=0.0)
    ref = corpus_fingerprint(ev.scope)
    snap = SecuritySnapshot(scope_ref=ref)
    rows = [_ab(f"t{i}", True, True) for i in range(20)]
    ledger = EvidenceLedger()
    first = promote_candidate(ev, rows, security_before=snap, security_after=snap, shadow_runs=20,
                              owner_approved=True, rollback_tested=True, rollback=RollbackInfo("SHADOW", "c1@v0"),
                              evidence_ledger=ledger)
    assert first.status == "PROMOTED"
    other = replace(ev, candidate_id="c2", rollback_ref="c2@v1")
    second = promote_candidate(other, [replace(r, raw_tokens=1) for r in rows], security_before=snap,
                               security_after=snap, shadow_runs=21, owner_approved=True, rollback_tested=True,
                               rollback=RollbackInfo("SHADOW", "c2@v0"), evidence_ledger=ledger)
    assert second.status != "PROMOTED" and "already spent" in " ".join(second.reasons)
