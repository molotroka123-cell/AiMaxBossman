"""Мосты Organization Layer к соседним слоям (§18) — узкие порты.

Вниз (исполнение): `ExecutionBridge`. Штатная реализация — `V3ExecutionBridge`
поверх УЖЕ существующих CompoundRunner + TaskJournal + UniversalComputerAgent:
организация не исполняет и не верифицирует, она отдаёт контракт цепочке V3 и
читает обратно ЖУРНАЛ. Улика с verified=True рождается только из шага журнала
со статусом finished (чек исполнения И подтверждение) — ровно тот же инвариант,
что держат V2 и V3.

Вверх (миссии): Executive OS в репозитории ещё нет. Поставляемый в ZIP
`ExecutiveOSBridge` (execute_delegated) описывал обратное направление — что
Executive OS исполняет за организацию, — и в живой архитектуре не нужен:
исполняет V3/V2. Вместо него — `MissionReporter`: что организация ВОЗВРРАЩАЕТ
слою миссий (прогресс, подтверждённые результаты, блокеры, ресурсы, качество,
завершённость). Это единственная точка связи, и она пассивна.

Владелец: `HumanReviewPort` — куда уходит «нужно решение человека». Организация
никогда не одобряет сама.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ..computer_agent.agent import UniversalComputerAgent
from ..contracts import SideEffectClass, TypedAction
from ..execution.compound import CompoundResult, CompoundRunner, PlanStep
from ..memory.failure_memory import FailureMemory
from ..memory.journal import TaskJournal, journal_path, JournalIntegrityError, digest
from .contracts import DelegationContract
from .models import Evidence, Resources, WorkResult

WAITING_MARKERS = ("ApprovalDeniedError",)
# Причины остановки цепочки, при которых исполнитель НЕ дошёл до чека: политика,
# одобрение, устаревшее наблюдение, небезопасное/неподдерживаемое действие,
# падение исполнителя. Всё остальное — «чек есть, эффекта нет»: заявленный успех.
_NOT_A_CLAIM = ("PolicyDeniedError", "ApprovalDeniedError", "StaleObservationError",
                "UnsafeActionError", "UnsupportedActionError", "Error:", "guard ")


# ------------------------------------------------------------------- ports

class ExecutionBridge(Protocol):
    def execute(self, contract: DelegationContract, *, agent_id: str) -> WorkResult: ...


class HumanReviewPort(Protocol):
    def request(self, contract: DelegationContract, reason: str) -> str: ...


@dataclass(frozen=True)
class MissionStatus:
    """Что организация возвращает слою миссий (Executive OS / владельцу)."""
    mission_id: str
    state: str
    progress: float                              # доля контрактов COMPLETED
    completed: tuple[str, ...]
    verified_results: tuple[str, ...]            # work_id с verified=True
    blockers: tuple[dict[str, Any], ...]
    waiting_approval: tuple[str, ...]
    failed: tuple[str, ...]
    cost: dict[str, Any]
    quality: dict[str, Any]                      # false_success_attempts, review_vetoes
    done: bool

    def to_dict(self) -> dict[str, Any]:
        return {"mission_id": self.mission_id, "state": self.state, "progress": self.progress,
                "completed": list(self.completed), "verified_results": list(self.verified_results),
                "blockers": [dict(b) for b in self.blockers], "waiting_approval": list(self.waiting_approval),
                "failed": list(self.failed), "cost": dict(self.cost), "quality": dict(self.quality),
                "done": self.done}


class MissionReporter(Protocol):
    def report(self, status: MissionStatus) -> None: ...


class RecordingHumanReview:
    """Реализация по умолчанию: запоминает запросы, ничего не одобряет."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def request(self, contract: DelegationContract, reason: str) -> str:
        self.requests.append((contract.work_id, reason))
        return f"review-{len(self.requests)}"


class RecordingReporter:
    def __init__(self) -> None:
        self.statuses: list[MissionStatus] = []

    def report(self, status: MissionStatus) -> None:
        self.statuses.append(status)


# ------------------------------------------------------ plan (de)serialize

def step_to_dict(step: PlanStep) -> dict[str, Any]:
    a = step.action
    return {"step_id": step.step_id, "intent": step.intent, "required": step.required, "guard": step.guard,
            "action": {"action_type": a.action_type, "args": dict(a.args), "scopes": list(a.scopes),
                       "side_effect": a.side_effect.value, "idempotency_key": a.idempotency_key,
                       "source": a.source}}


def step_from_dict(raw: Mapping[str, Any]) -> PlanStep:
    a = dict(raw.get("action") or {})
    action = TypedAction(action_type=str(a["action_type"]), args=dict(a.get("args") or {}),
                         scopes=tuple(a.get("scopes") or ()),
                         side_effect=SideEffectClass(a.get("side_effect", "READ_ONLY")),
                         idempotency_key=a.get("idempotency_key"), source=str(a.get("source", "bossman_v3")))
    return PlanStep(step_id=str(raw["step_id"]), intent=str(raw.get("intent", "")), action=action,
                    required=bool(raw.get("required", True)), guard=str(raw.get("guard", "")))


# ------------------------------------------------------------ V3 bridge

AgentFactory = Callable[[str, DelegationContract], UniversalComputerAgent]
CostMeter = Callable[[DelegationContract, str, CompoundResult, float], Resources]


def _default_cost(contract: DelegationContract, agent_id: str, res: CompoundResult, elapsed_s: float) -> Resources:
    return Resources(compute_seconds=int(round(elapsed_s)))


class V3ExecutionBridge:
    """Контракт → цепочка V3 (CompoundRunner) → WorkResult с уликами из журнала.

    Журнал живёт в `journal_root/<mission>__<work>.json` и переживает рестарт:
    повторный `execute` того же контракта продолжает с первого незакрытого
    шага, а не переигрывает сделанное (V3.4)."""

    def __init__(self, *, agent_factory: AgentFactory, journal_root: str | Path,
                 failure_memory_for: Callable[[str], FailureMemory | None] | None = None,
                 cost_meter: CostMeter | None = None) -> None:
        self.agent_factory = agent_factory
        self.journal_root = Path(journal_root)
        self.failure_memory_for = failure_memory_for
        self.cost_meter = cost_meter or _default_cost

    @staticmethod
    def journal_id(contract: DelegationContract) -> str:
        return f"{contract.mission_id}__{contract.work_id}"

    def journal(self, contract: DelegationContract, plan: list[PlanStep]) -> TaskJournal | None:
        """Существующий журнал переиспользуется только для ТОГО ЖЕ плана; изменённый план под
        тем же id — None (владелец решает), а не «продолжить с чужими шагами»."""
        jid = self.journal_id(contract)
        path = journal_path(self.journal_root, jid)
        if path.exists():
            j = TaskJournal.load(task_id=jid, root=self.journal_root)
        else:
            j = TaskJournal.start(task_id=jid, plan=[(p.step_id, p.intent) for p in plan], root=self.journal_root)
        j.bind_plan([step_to_dict(p) for p in plan])
        prior = next((n.get("contract_digest") for n in j.notes if "contract_digest" in n), None)
        if prior is not None and prior != contract.digest():
            raise JournalIntegrityError("contract identity or policy changed; reconciliation required")
        binding = {"mission_id": contract.mission_id, "work_id": contract.work_id,
                   "contract_digest": contract.digest()}
        if j.execution_binding and j.execution_binding != binding:
            raise JournalIntegrityError("journal execution ownership changed")
        if not j.execution_binding:
            if any(s.finished for s in j.steps):
                raise JournalIntegrityError("legacy execution binding requires reconciliation")
            j.execution_binding = binding
            j._save()
        if prior is None:
            j.notes.append({"contract_digest": contract.digest()})
            j._save()
        return j

    def execute(self, contract: DelegationContract, *, agent_id: str, before_step=None,
                execution_guard=None) -> WorkResult:
        plan = [step_from_dict(s) for s in contract.steps]
        if not plan:
            return WorkResult(contract.work_id, executed=False, produced_by=agent_id,
                              reason="contract carries no executable steps for the V3 chain")
        journal = self.journal(contract, plan)
        if journal is None:
            return WorkResult(contract.work_id, executed=False, produced_by=agent_id,
                              reason="plan_mismatch: contract steps changed after the journal recorded finished steps",
                              metadata={"plan_mismatch": True, "ask_owner": True})
        agent = self.agent_factory(agent_id, contract)
        fm = self.failure_memory_for(contract.department_id) if self.failure_memory_for else None
        already = {s.step_id for s in journal.finished()}
        t0 = time.monotonic()
        dispatch = dict(contract.metadata.get("fleet_dispatch") or {})
        res = agent_run(agent, journal, plan, model=agent_id, failure_memory=fm,
                        context={"before_step": before_step, "execution_guard": execution_guard, "fence": dispatch.get("fence"), "node_id": dispatch.get("node_id", ""),
                                 "lease_id": dispatch.get("lease_id", ""), "run_id": self.journal_id(contract)})
        elapsed = time.monotonic() - t0

        evidence = [_evidence_from_step(journal.task_id, step, journal) for step in plan
                    if step.step_id in {s.step_id for s in journal.finished()}]
        executed_now = bool(res.executed) or bool(already)
        waiting = res.blocked_at is not None and any(m in res.reason for m in WAITING_MARKERS)
        # Исполнитель вернул чек, а свежее наблюдение эффект не подтвердило —
        # это и есть попытка ложного успеха; она учитывается обучением.
        claimed_effect = (res.blocked_at is not None and not res.completed
                          and not any(m in res.reason for m in _NOT_A_CLAIM))
        return WorkResult(
            work_id=contract.work_id, executed=executed_now, evidence=evidence,
            claims={"runner_completed": res.completed, "runner_reason": res.reason,
                    "claimed_effect": claimed_effect,
                    "executed_now": list(res.executed), "already_finished": sorted(already)},
            cost=self.cost_meter(contract, agent_id, res, elapsed), produced_by=agent_id,
            reason=res.reason, metadata={"journal": journal.task_id, "blocked_at": res.blocked_at,
                                         "not_run": list(res.not_run), "waiting_approval": waiting})


def agent_run(agent: UniversalComputerAgent, journal: TaskJournal, plan: list[PlanStep], *,
              model: str, failure_memory: FailureMemory | None,
              context: Mapping[str, Any] | None = None) -> CompoundResult:
    return CompoundRunner(agent, journal, model=model, failure_memory=failure_memory).run(plan, context)


def _evidence_from_step(journal_id: str, step: PlanStep, journal: TaskJournal) -> Evidence:
    js = next(s for s in journal.steps if s.step_id == step.step_id)
    if js.action_digest != digest(step_to_dict(step)):
        raise JournalIntegrityError("evidence action binding mismatch")
    expect = dict(step.action.args).get("expect")
    if isinstance(expect, Mapping) and expect.get("kind"):
        kind, ref = str(expect["kind"]), str(expect.get("target", step.step_id))
    else:
        kind, ref = "step", step.step_id
    receipt = dict(js.receipt or {})
    # EH-01: улика поднимается только из подписанного журналом шага и подписывается сама
    if not js.signature_valid(journal.task_id):
        raise RuntimeError(f"journal step {journal_id}/{step.step_id} is not signed — evidence refused")
    expected = dict(expect.get("expect") or {}) if isinstance(expect, Mapping) else {}
    if isinstance(expect, Mapping):
        expected.update({k: v for k, v in expect.items() if k not in ("kind", "target", "expect")})
    binding = {**js.execution_binding,
               "action_digest": js.action_digest, "attempt_id": js.attempt_id,
               "started_at": receipt.get("started_at", ""), "verified_expect": expected,
               "verification_passed": receipt.get("verification_passed", False),
               "observed_state": receipt.get("observed_state", {})}
    return Evidence.signed(kind, ref, source=f"journal:{journal_id}/{step.step_id}",
                           observed_at=receipt.get("observed_at", js.updated_at), binding=binding,
                           detail=json.dumps(receipt, ensure_ascii=False, sort_keys=True)[:300])


# ------------------------------------------------- company-plan adapter

def contracts_from_company_plan(plan, *, mission_id: str, department_of_role: Mapping[str, str] | None = None,
                                risk_of_kind: Mapping[str, str] | None = None) -> list[DelegationContract]:
    """bossman.company.CompanyPlan → контракты делегирования.

    Планировщик AI Company Mode уже умеет цель → отделы/роли/DAG; повторять его
    здесь незачем. Роль задачи → отдел берётся из плана (AgentRole.department),
    способность — `CompanyTask.action`, улики — `evidence_requirements`,
    зависимости — DAG, гейтуемые виды — HIGH-риск."""
    from .contracts import EvidenceRequirement
    from .models import RiskTier
    dept = {r.name: r.department for r in plan.roles}
    dept.update(department_of_role or {})
    risk_map = {"publish": "high", "spend": "high", "credentials": "high", "destructive": "high",
                "write": "medium", "read": "low"}
    risk_map.update(risk_of_kind or {})
    out: list[DelegationContract] = []
    for t in plan.ordered():
        out.append(DelegationContract(
            work_id=t.id, mission_id=mission_id, department_id=dept.get(t.role, "operations"),
            goal=t.title, required_capability=t.action,
            success_criteria=[f"{t.title}: evidence observed fresh"],
            evidence_required=[EvidenceRequirement(e.kind, e.target, dict(e.expect)) for e in t.evidence_requirements],
            budget=Resources(usd=float(t.estimated_cost)), risk=RiskTier(risk_map.get(t.kind, "medium")),
            dependencies=list(t.depends_on), side_effect=(t.kind != "read"),
            inputs=dict(t.params), metadata={"company_role": t.role, "company_kind": t.kind,
                                             "gated": [r.kind for r in t.requires_approval]}))
    return out
