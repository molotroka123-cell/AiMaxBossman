"""V3.4 Compound Action Resume: цепочка шагов поверх уже готовых частей.

Ничего из цикла одного действия здесь не пишется заново. policy → approval →
execute → observe → verify уже реализован в bossman_v3/computer_agent
(UniversalComputerAgent), durable-состояние — в bossman_v3/memory (TaskJournal).
Недостающим был только сам оркестратор цепочки, и он делает ровно четыре вещи:

  1. идёт от ПЕРВОГО НЕЗАВЕРШЁННОГО шага журнала, а не от начала плана —
     поэтому рестарт не переигрывает сделанное;
  2. пишет исход каждого шага в журнал сразу (чек + подтверждение), так что
     смерть процесса на любом шаге не теряет предыдущие;
  3. держит инвариант V2: обязательный шаг без подтверждения останавливает
     цепочку, и родитель НЕ становится выполненным;
  4. уважает guard'ы («закоммить, только если тесты зелёные»): шаг с
     непройденным guard'ом не запускается.

Про исполнителя тут ничего не известно намеренно — он приходит как
ExecutorPort. Привязка к V2 будет отдельным адаптером, который водит
замороженный V2 через его существующий API; сам V2 при этом не меняется.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..computer_agent.agent import (ApprovalDeniedError, PolicyDeniedError,
                                    StaleObservationError, UniversalComputerAgent,
                                    UnsafeActionError, UnsupportedActionError)
from ..contracts import TypedAction
from ..memory.failure_memory import FailureMemory
from ..memory.journal import TaskJournal

_EXPECTED = (PolicyDeniedError, ApprovalDeniedError, StaleObservationError,
             UnsafeActionError, UnsupportedActionError)


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    intent: str
    action: TypedAction
    required: bool = True
    guard: str = ""          # step_id, который обязан быть подтверждён


@dataclass(frozen=True)
class CompoundResult:
    completed: bool
    executed: list[str] = field(default_factory=list)      # исполнено В ЭТОМ прогоне
    not_run: list[str] = field(default_factory=list)       # не запускались
    blocked_at: str | None = None
    reason: str = ""


class CompoundRunner:
    def __init__(self, agent: UniversalComputerAgent, journal: TaskJournal, *,
                 model: str = "", failure_memory: FailureMemory | None = None):
        self.agent = agent
        self.journal = journal
        self.model = model
        self.failure_memory = failure_memory

    def _guard_passed(self, step: PlanStep) -> bool:
        if not step.guard:
            return True
        return any(s.step_id == step.guard and s.finished for s in self.journal.steps)

    def _remember_failure(self, step: PlanStep, reason: str) -> None:
        if self.failure_memory is None:
            return
        self.failure_memory.record({"signature": step.step_id, "approach": step.intent,
                                    "error": reason, "by": self.model})

    def _action_receipt(self, step: PlanStep, outcome, context: Mapping[str, Any] | None) -> dict:
        """Канонический ActionReceipt (TRUTH-003 §2): заявление исполнителя + независимое
        наблюдение + fencing_token из контекста (флот/движок). Подпись — на уровне
        закрытого шага журнала; SIGNED_RECEIPT ≠ VERIFIED_SIDE_EFFECT остаётся в силе."""
        import bossman._shared  # noqa: F401
        from bossman_shared.action_receipt import ActionReceipt
        ctx = dict(context or {})
        r = outcome.receipt
        obs = outcome.observation
        src = str(getattr(obs, "source", "") or "")
        observation_type = "post_state" if src in ("bcc.v2.verification", "fs", "fake") or str(obs.state.get("status", ""))             else "tool_result_only"
        if not obs.state:
            observation_type = "tool_result_only"
        fence = ctx.get("fence")
        rec = ActionReceipt.from_v3(
            task_id=self.journal.task_id, step_id=step.step_id, action_type=step.action.action_type,
            effect_type=getattr(step.action.side_effect, "value", str(step.action.side_effect)), args=step.action.args,
            started_at=r.started_at if r else None, finished_at=r.completed_at if r else None,
            observed_at=obs.observed_at, executor_status="executed",
            observation_type=observation_type, observation_ref=src,
            verification_status="VERIFIED" if outcome.verification.passed else "FAILED",
            verification_reason=outcome.verification.reason or "",
            idempotency_key=step.action.idempotency_key or f"{self.journal.task_id}/{step.step_id}",
            fencing_token=int(fence) if fence is not None else None, run_id=str(ctx.get("run_id", "")),
            executor_metadata={"effect_id": outcome.effect_id, "approval_id": outcome.approval_id,
                               "model": self.model, "node_id": ctx.get("node_id", "")})
        return rec.to_dict()

    def run(self, plan: Sequence[PlanStep], context: Mapping[str, Any] | None = None) -> CompoundResult:
        done_ids = {s.step_id for s in self.journal.finished_signed()}
        executed: list[str] = []
        not_run: list[str] = []

        for step in plan:
            if step.step_id in done_ids:
                continue                      # уже сделано — не переигрываем

            if not self._guard_passed(step):
                not_run.append(step.step_id)
                if step.required:
                    return CompoundResult(False, executed, not_run, step.step_id,
                                          f"guard {step.guard!r} не подтверждён")
                continue

            try:
                outcome = self.agent.run(step.action, dict(context or {}))
            except _EXPECTED as exc:
                reason = f"{type(exc).__name__}: {exc}"
                self.journal.fail(step.step_id, error=reason, by=self.model)
                self._remember_failure(step, reason)
                not_run.extend(s.step_id for s in plan
                               if s.step_id not in done_ids and s.step_id != step.step_id
                               and s.step_id not in executed)
                return CompoundResult(False, executed, not_run, step.step_id, reason)
            except Exception as exc:          # исполнитель умер — шаг не закрыт
                reason = f"{type(exc).__name__}: {exc}"
                self.journal.fail(step.step_id, error=reason, by=self.model)
                self._remember_failure(step, reason)
                return CompoundResult(False, executed, not_run, step.step_id, reason)

            if not outcome.verification.passed:
                reason = outcome.verification.reason or "верификация не пройдена"
                self.journal.fail(step.step_id, error=reason, by=self.model)
                self._remember_failure(step, reason)
                if step.required:
                    not_run.extend(s.step_id for s in plan
                                   if s.step_id != step.step_id and s.step_id not in done_ids
                                   and s.step_id not in executed)
                    return CompoundResult(False, executed, not_run, step.step_id, reason)
                not_run.append(step.step_id)
                continue

            self.journal.record(step.step_id, verified=True, by=self.model,
                                receipt=self._action_receipt(step, outcome, context))
            executed.append(step.step_id)

        remaining_required = [s for s in plan
                              if s.required and s.step_id not in {x.step_id
                                                                  for x in self.journal.finished_signed()}]
        if remaining_required:
            first = remaining_required[0]
            return CompoundResult(False, executed, not_run, first.step_id,
                                  "обязательный шаг не завершён")
        return CompoundResult(True, executed, not_run, None, "")
