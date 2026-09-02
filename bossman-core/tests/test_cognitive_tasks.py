"""Длинные задачи 10/10: journal, VERIFIED-требование, DAG, resume, идемпотентность."""
from __future__ import annotations

import pytest

from bossman.cognitive.storage import CognitiveStore
from bossman.cognitive.tasks import (
    Checkpointer,
    EnvSnapshot,
    IllegalTransition,
    JournalStep,
    ResumeRecovery,
    StepState,
    TaskJournal,
    VerificationRequired,
)


def _tj() -> tuple[TaskJournal, Checkpointer, CognitiveStore]:
    s = CognitiveStore(":memory:")
    j = TaskJournal(s)
    return j, Checkpointer(s, j), s


def test_no_verified_without_verifier():
    j, _, _ = _tj()
    j.add_step(JournalStep("t1", "s1"))
    j.transition("t1", "s1", StepState.READY)
    j.transition("t1", "s1", StepState.RUNNING)
    j.transition("t1", "s1", StepState.RECONCILING)
    with pytest.raises(VerificationRequired):
        j.transition("t1", "s1", StepState.VERIFIED)
    # прямой RUNNING → VERIFIED запрещён даже с verifier
    j2, _, _ = _tj()
    j2.add_step(JournalStep("t", "s"))
    j2.transition("t", "s", StepState.READY)
    j2.transition("t", "s", StepState.RUNNING)
    with pytest.raises(IllegalTransition):
        j2.transition("t", "s", StepState.VERIFIED,
                      verifier_id="v", verification="ok")


def test_dag_blocks_dependents_and_retry_reuses_effect():
    j, _, _ = _tj()
    j.add_step(JournalStep("t", "a"))
    j.add_step(JournalStep("t", "b", dependencies=["a"]))
    assert j.mark_ready("t") == ["a"]  # b заблокирована
    for st in (StepState.RUNNING, StepState.RECONCILING):
        j.transition("t", "a", st)
    j.transition("t", "a", StepState.VERIFIED, verifier_id="v", verification="tests green")
    assert j.mark_ready("t") == ["b"]
    j.transition("t", "b", StepState.RUNNING)
    eff = j.get_step("t", "b").effect_id
    assert eff  # idempotency key зафиксирован до внешнего вызова
    j.transition("t", "b", StepState.FAILED_RETRYABLE)
    j.retry_step("t", "b")
    assert j.get_step("t", "b").effect_id == eff  # DuplicateExternalEffects = 0


def test_checkpoint_resume_after_restart_no_blind_retry():
    j, cp, store = _tj()
    rec = ResumeRecovery(store, j, cp)
    j.add_step(JournalStep("t", "s1", input_hash="in1"))
    j.add_step(JournalStep("t", "s2", dependencies=["s1"], input_hash="in2"))
    j.mark_ready("t")
    j.transition("t", "s1", StepState.RUNNING)
    j.transition("t", "s1", StepState.RECONCILING)
    j.transition("t", "s1", StepState.VERIFIED, verifier_id="v", verification="ok")
    j.mark_ready("t")
    j.transition("t", "s2", StepState.RUNNING)  # падение процесса здесь (s2 уже READY после mark_ready)
    cp.write("t", run_id="r1", confirmed_results=["s1 ok"],
             last_verified_env=EnvSnapshot(git_sha="abc").__dict__)
    # restart: новый объект поверх того же store ( durable переживает рестарт)
    j2 = TaskJournal(store)
    rec2 = ResumeRecovery(store, j2, Checkpointer(store, j2))
    seen: list[str] = []

    def probe(step: JournalStep):
        seen.append(step.step_id)
        return False  # эффекта не было — безопасно в retry

    rep = rec2.recover("t", current_env=EnvSnapshot(git_sha="abc"), effect_probe=probe)
    assert seen == ["s2"]  # s1 (VERIFIED) не трогаем: LostVerifiedSteps = 0
    assert rep["lost_verified_steps"] == 0
    assert rep["resume_from"] == "s2"
    assert rep["resume_accuracy_ok"] is True


def test_env_change_triggers_revalidation():
    j, cp, store = _tj()
    rec = ResumeRecovery(store, j, cp)
    j.add_step(JournalStep("t", "s1"))
    cp.write("t", last_verified_env=EnvSnapshot(git_sha="aaa", browser_tab="t1").__dict__)
    rep = rec.recover("t", current_env=EnvSnapshot(git_sha="bbb", browser_tab="t1"),
                      effect_probe=lambda s: None)
    assert rep["need_revalidation"] is True
    assert "git_sha" in rep["env_changed"]


def test_cancel_branch_and_plan_versioning():
    j, _, _ = _tj()
    j.add_step(JournalStep("t", "a"))
    j.add_step(JournalStep("t", "b"))
    assert j.cancel_branch("t", ["b"]) == 1
    assert j.get_step("t", "b").state is StepState.CANCELLED
    j.add_dependency("t", "a", "x", new_plan_version=2)
    assert j.get_step("t", "a").plan_version == 2
