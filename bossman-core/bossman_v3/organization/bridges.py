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
from ..memory.journal import TaskJournal
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

    def journal(self, contract: DelegationContract, plan: list[PlanStep]) -> TaskJournal:
        jid = self.journal_id(contract)
        path = self.journal_root / f"{jid}.json"
        if path.exists():
            j = TaskJournal.load(task_id=jid, root=self.journal_root)
            if [s.step_id for s in j.steps] == [p.step_id for p in plan]:
                return j
        return TaskJournal.start(task_id=jid, plan=[(p.step_id, p.intent) for p in plan], root=self.journal_root)

    def execute(self, contract: DelegationContract, *, agent_id: str) -> WorkResult:
        plan = [step_from_dict(s) for s in contract.steps]
        if not plan:
            return WorkResult(contract.work_id, executed=False, produced_by=agent_id,
                              reason="contract carries no executable steps for the V3 chain")
        agent = self.agent_factory(agent_id, contract)
        journal = self.journal(contract, plan)
        fm = self.failure_memory_for(contract.department_id) if self.failure_memory_for else None
        already = {s.step_id for s in journal.finished()}
        t0 = time.monotonic()
        res = agent_run(agent, journal, plan, model=agent_id, failure_memory=fm)
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
              model: str, failure_memory: FailureMemory | None) -> CompoundResult:
    return CompoundRunner(agent, journal, model=model, failure_memory=failure_memory).run(plan)


def _evidence_from_step(journal_id: str, step: PlanStep, journal: TaskJournal) -> Evidence:
    js = next(s for s in journal.steps if s.step_id == step.step_id)
    expect = dict(step.action.args).get("expect")
    if isinstance(expect, Mapping) and expect.get("kind"):
        kind, ref = str(expect["kind"]), str(expect.get("target", step.step_id))
    else:
        kind, ref = "step", step.step_id
    receipt = dict(js.receipt or {})
    return Evidence(kind=kind, ref=ref, verified=True, source=f"journal:{journal_id}/{step.step_id}",
                    observed_at=js.updated_at, detail=json.dumps(receipt, ensure_ascii=False, sort_keys=True)[:300])


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
