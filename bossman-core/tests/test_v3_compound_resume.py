"""V3.4 Compound Action Resume — killer case, written as the specification.

Цепочка владельца: «открой проект → исправь баг → запусти тест → если зелёный,
закоммить → push». Требование ровно одно и оно жёсткое: Bossman проходит
цепочку сам, а после рестарта продолжает С ПЕРВОГО НЕЗАВЕРШЁННОГО шага, не
переигрывая сделанное.

Здесь ничего не изобретается заново: один шаг исполняет уже существующий
UniversalComputerAgent (policy → approval → execute → observe → verify), а
durable-состояние держит TaskJournal из V3.1. Проверяется именно склейка —
то, чего не было.

Инвариант V2 перенесён без ослабления: обязательный шаг, который не прошёл
верификацию, останавливает цепочку, и родитель НЕ становится выполненным.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation,
                                  PolicyDecision, TypedAction, VerificationResult)
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.memory import FailureMemory, TaskJournal


class _Policy:
    def authorize(self, action, context):
        return PolicyDecision(allowed=True)


class _Approval:
    def request(self, action, policy, context):
        return ApprovalDecision(approved=True, approval_id="ap-1")


class _Executor:
    """Считает, что реально исполнялось. Повторное исполнение уже сделанного
    шага после рестарта — это ровно то, что тесты обязаны ловить."""

    def __init__(self, *, fail_on: set[str] | None = None, boom_on: set[str] | None = None):
        self.seen: list[str] = []
        self.fail_on = fail_on or set()      # исполнится, но верификация не пройдёт
        self.boom_on = boom_on or set()      # исполнитель падает (как убитый процесс)

    def supports(self, action_type: str) -> bool:
        return True

    def execute(self, action: TypedAction) -> ExecutionReceipt:
        sid = str(action.args.get("step_id"))
        if sid in self.boom_on:
            raise RuntimeError(f"исполнитель упал на {sid}")
        self.seen.append(sid)
        now = datetime.now(timezone.utc)
        return ExecutionReceipt(action_type=action.action_type, started_at=now,
                                completed_at=now, effect_id=f"eff-{sid}")


class _Observer:
    def observe_fresh(self, action, receipt) -> Observation:
        return Observation(observed_at=receipt.completed_at + timedelta(milliseconds=1),
                           source="fake", state={"step_id": action.args.get("step_id")})


class _Verifier:
    def __init__(self, fail_on: set[str] | None = None):
        self.fail_on = fail_on or set()

    def verify(self, action, receipt, observation) -> VerificationResult:
        sid = str(action.args.get("step_id"))
        return VerificationResult(passed=sid not in self.fail_on,
                                  reason="" if sid not in self.fail_on else f"{sid} не подтверждён")


def _agent(executor: _Executor, *, verify_fail: set[str] | None = None) -> UniversalComputerAgent:
    return UniversalComputerAgent(_Policy(), _Approval(), executor, _Observer(),
                                  _Verifier(verify_fail))


def _plan() -> list[PlanStep]:
    def step(sid, intent, **kw):
        return PlanStep(step_id=sid, intent=intent,
                        action=TypedAction(action_type="proj.step", args={"step_id": sid}), **kw)
    return [
        step("s1", "открыть проект"),
        step("s2", "исправить баг"),
        step("s3", "запустить тесты"),
        step("s4", "закоммитить", guard="s3"),      # только если тесты зелёные
        step("s5", "запушить", guard="s4"),
    ]


def _journal(tmp_path) -> TaskJournal:
    return TaskJournal.start(task_id="chain", plan=[(s.step_id, s.intent) for s in _plan()],
                             root=tmp_path)


# ------------------------------------------------------------- happy chain

def test_full_chain_runs_end_to_end(tmp_path):
    ex = _Executor()
    res = CompoundRunner(_agent(ex), _journal(tmp_path)).run(_plan())

    assert res.completed is True
    assert ex.seen == ["s1", "s2", "s3", "s4", "s5"]
    assert res.blocked_at is None


# ----------------------------------------------------------------- resume

def test_restart_resumes_from_the_first_unfinished_step(tmp_path):
    """Исполнитель «умирает» на s3. После рестарта — новый журнал с диска,
    новый исполнитель — цепочка обязана продолжиться с s3, а не с s1."""
    dying = _Executor(boom_on={"s3"})
    first = CompoundRunner(_agent(dying), _journal(tmp_path)).run(_plan())
    assert first.completed is False
    assert dying.seen == ["s1", "s2"]

    revived_journal = TaskJournal.load(task_id="chain", root=tmp_path)
    healthy = _Executor()
    second = CompoundRunner(_agent(healthy), revived_journal).run(_plan())

    assert healthy.seen == ["s3", "s4", "s5"], "переигрались уже сделанные шаги"
    assert second.completed is True


def test_resume_uses_a_different_model_without_replaying_work(tmp_path):
    dying = _Executor(boom_on={"s2"})
    CompoundRunner(_agent(dying), _journal(tmp_path), model="glm-local").run(_plan())

    healthy = _Executor()
    res = CompoundRunner(_agent(healthy), TaskJournal.load(task_id="chain", root=tmp_path),
                         model="claude").run(_plan())

    assert "s1" not in healthy.seen
    assert res.completed is True


def test_a_finished_chain_does_nothing_on_re_run(tmp_path):
    """Идемпотентность: повторный запуск завершённой цепочки не должен
    выполнить ни одного внешнего действия заново."""
    j = _journal(tmp_path)
    CompoundRunner(_agent(_Executor()), j).run(_plan())

    again = _Executor()
    res = CompoundRunner(_agent(again), TaskJournal.load(task_id="chain", root=tmp_path)).run(_plan())
    assert again.seen == []
    assert res.completed is True


# -------------------------------------------------- required failure blocks

def test_unverified_required_step_blocks_the_parent(tmp_path):
    """Инвариант V2 в цепочке: шаг исполнился, но верификация не прошла —
    родитель не выполнен, и последующие шаги не запускаются."""
    ex = _Executor()
    res = CompoundRunner(_agent(ex, verify_fail={"s3"}), _journal(tmp_path)).run(_plan())

    assert res.completed is False
    assert res.blocked_at == "s3"
    assert "s4" not in ex.seen and "s5" not in ex.seen


def test_guarded_step_is_skipped_when_its_guard_did_not_pass(tmp_path):
    ex = _Executor()
    runner = CompoundRunner(_agent(ex, verify_fail={"s3"}), _journal(tmp_path))
    res = runner.run(_plan())
    assert "s4" in res.not_run and "s5" in res.not_run


def test_failed_step_is_remembered_so_it_is_not_retried_blindly(tmp_path):
    fm = FailureMemory(root=tmp_path)
    ex = _Executor()
    CompoundRunner(_agent(ex, verify_fail={"s3"}), _journal(tmp_path), failure_memory=fm).run(_plan())

    remembered = fm.query("s3")
    assert remembered, "провал шага не попал в память провалов"
    assert "s3" in str(remembered[0])


# ----------------------------------------------------------- the killer case

def test_the_killer_case_evening_to_morning(tmp_path):
    """Вечером цепочка дошла до тестов и процесс умер. Утром — другая модель,
    новый исполнитель, тот же журнал с диска: продолжает с s3, не переигрывает
    открытие проекта и правку бага, доводит до push."""
    evening_exec = _Executor(boom_on={"s3"})
    fm = FailureMemory(root=tmp_path)
    evening = CompoundRunner(_agent(evening_exec), _journal(tmp_path),
                             model="glm-local", failure_memory=fm).run(_plan())
    assert evening.completed is False
    assert evening_exec.seen == ["s1", "s2"]

    morning_exec = _Executor()
    morning = CompoundRunner(_agent(morning_exec),
                             TaskJournal.load(task_id="chain", root=tmp_path),
                             model="claude", failure_memory=fm).run(_plan())

    assert morning_exec.seen == ["s3", "s4", "s5"]
    assert morning.completed is True
    assert morning.executed == ["s3", "s4", "s5"]
