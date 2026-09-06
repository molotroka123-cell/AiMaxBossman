from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from bossman_v3.execution.telemetry import journal_record


def _step(status="DONE", finished=True):
    return SimpleNamespace(status=status, updated_at="2026-09-06T12:00:10+00:00", finished=finished,
                           signature_valid=lambda task_id: finished)


def test_verified_terminal_journal_becomes_passed_sample():
    j = SimpleNamespace(task_id="real-1", created_at="2026-09-06T12:00:00+00:00",
                        plan_digest="abc", steps=[_step(), _step()])
    r = journal_record(j, completed=True, context={"concurrency": 3, "retries": 1})
    assert r["status"] == "passed"
    assert r["verified"] is True
    assert r["duration_s"] == 10.0
    assert r["concurrency"] == 3
    assert r["retries"] == 1


def test_failed_journal_never_claims_verified_success():
    j = SimpleNamespace(task_id="real-2", created_at="2026-09-06T12:00:00+00:00",
                        plan_digest="def", steps=[_step("FAILED", False)])
    r = journal_record(j, completed=False)
    assert r["status"] == "failed"
    assert r["verified"] is False


# ----------------------------------------- автосбор на реальном прогоне цепочки

def _runner_case(tmp_path, *, boom_on=None):
    """Минимальная реальная цепочка на существующем V3-стеке: сэмпл обязан
    появиться сам, без ручной записи бенчмарка."""
    from datetime import timedelta

    from bossman_v3.computer_agent.agent import UniversalComputerAgent
    from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation,
                                      PolicyDecision, TypedAction, VerificationResult)
    from bossman_v3.execution import CompoundRunner, PlanStep
    from bossman_v3.memory import TaskJournal

    class _Policy:
        def authorize(self, action, context):
            return PolicyDecision(allowed=True)

    class _Approval:
        def request(self, action, policy, context):
            return ApprovalDecision(approved=True, approval_id="ap-1")

    class _Executor:
        def supports(self, action_type):
            return True

        def execute(self, action):
            sid = str(action.args.get("step_id"))
            if sid in (boom_on or set()):
                raise RuntimeError(f"исполнитель упал на {sid}")
            now = datetime.now(timezone.utc)
            return ExecutionReceipt(action_type=action.action_type, started_at=now,
                                    completed_at=now, effect_id=f"eff-{sid}")

    class _Observer:
        def observe_fresh(self, action, receipt):
            return Observation(observed_at=receipt.completed_at + timedelta(milliseconds=1),
                               source="fake", state={"step_id": action.args.get("step_id")})

    class _Verifier:
        def verify(self, action, receipt, observation):
            return VerificationResult(passed=True)

    plan = [PlanStep(step_id=sid, intent=sid,
                     action=TypedAction(action_type="proj.step", args={"step_id": sid}))
            for sid in ("s1", "s2")]
    journal = TaskJournal.start(task_id="telemetry-chain",
                                plan=[(s.step_id, s.intent) for s in plan], root=tmp_path)
    agent = UniversalComputerAgent(_Policy(), _Approval(), _Executor(), _Observer(), _Verifier())
    return CompoundRunner(agent, journal), plan


def _samples(root):
    path = root / "real_workloads.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_completed_run_records_a_verified_sample_without_being_asked(tmp_path):
    runner, plan = _runner_case(tmp_path)
    root = tmp_path / "benchmarks"

    assert runner.run(plan, {"real_workload_telemetry_root": str(root)}).completed is True

    samples = _samples(root)
    assert [s["task_id"] for s in samples] == ["telemetry-chain"]
    assert samples[0]["status"] == "passed"
    assert samples[0]["verified"] is True
    assert samples[0]["source"] == "task_journal"


def test_failed_run_is_recorded_too_so_failures_cannot_be_curated_out(tmp_path):
    runner, plan = _runner_case(tmp_path, boom_on={"s2"})
    root = tmp_path / "benchmarks"

    assert runner.run(plan, {"real_workload_telemetry_root": str(root)}).completed is False

    samples = _samples(root)
    assert len(samples) == 1
    assert samples[0]["status"] == "failed"
    assert samples[0]["verified"] is False


def test_replaying_the_same_outcome_does_not_inflate_the_corpus(tmp_path):
    runner, plan = _runner_case(tmp_path)
    root = tmp_path / "benchmarks"
    ctx = {"real_workload_telemetry_root": str(root)}

    runner.run(plan, ctx)
    runner.run(plan, ctx)

    assert len(_samples(root)) == 1


def test_broken_telemetry_never_fails_the_task(tmp_path, monkeypatch):
    runner, plan = _runner_case(tmp_path)
    from bossman_v3.execution import compound

    def _explode(*args, **kwargs):
        raise RuntimeError("бенчмарк-хранилище недоступно")

    monkeypatch.setattr(compound, "_append_real_workload_record", _explode)

    assert runner.run(plan, {"real_workload_telemetry_root": str(tmp_path / "b")}).completed is True


def test_telemetry_can_be_switched_off_for_a_run(tmp_path):
    runner, plan = _runner_case(tmp_path)
    root = tmp_path / "benchmarks"

    runner.run(plan, {"real_workload_telemetry_root": str(root),
                      "disable_real_workload_telemetry": True})

    assert _samples(root) == []
