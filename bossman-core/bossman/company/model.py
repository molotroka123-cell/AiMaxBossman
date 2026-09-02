"""Типизированное ядро AI Company Mode.

Frozen-датаклассы там, где это план/требование (неизменяемы после
планирования); изменяемы только состояние прогона (`CompanyRunState`) и
результаты.

Инвариант полномочий: `AgentRole` намеренно НЕ имеет полей вроде
`can_spend`/`can_publish`. Полномочие — свойство задачи
(`CompanyTask.requires_approval`) и решения внешнего `approval_gate`, а не
названия роли.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

FLAG = "AI_COMPANY_MODE_ENABLED"

# Виды действий, которые ВСЕГДА требуют внешнего одобрения.
GATED_KINDS = ("spend", "publish", "credentials", "destructive")

# Состояния задачи в прогоне.
TASK_STATES = ("PENDING", "RUNNING", "DONE", "FAILED", "DENIED", "BUDGET_EXCEEDED", "SKIPPED")
# Статусы верификации — зеркало bcc.v2.verification.Status.
VERIFICATION_STATUSES = ("VERIFIED", "FAILED", "UNVERIFIED")


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


class CompanyModeDisabled(RuntimeError):
    """AI_COMPANY_MODE_ENABLED выключен и прогон не объявлен синтетическим."""


class BudgetExceeded(RuntimeError):
    """Оценка стоимости задачи не помещается в бюджетный конверт."""


def _freeze(m: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(m or {}))


@dataclass(frozen=True, slots=True)
class ObjectiveConstraint:
    kind: str            # budget | deadline | scope | policy
    value: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class KPI:
    name: str
    description: str = ""
    direction: str = "up"          # up | down
    target: float | None = None
    unit: str = ""

    def improved(self, before: float | None, after: float | None) -> bool:
        if before is None or after is None:
            return False
        return after > before if self.direction == "up" else after < before

    def met(self, value: float | None) -> bool | None:
        """None — цель не задана или значение не наблюдалось."""
        if self.target is None or value is None:
            return None
        return value >= self.target if self.direction == "up" else value <= self.target


@dataclass(frozen=True, slots=True)
class CompanyObjective:
    id: str
    title: str
    domain: str                                   # ключ таблицы правил планировщика
    description: str = ""
    kpis: tuple[KPI, ...] = ()
    constraints: tuple[ObjectiveConstraint, ...] = ()


@dataclass(frozen=True, slots=True)
class Department:
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class AgentRole:
    """Специалист. Название роли — только маршрутизация и описание
    компетенции; полномочий (тратить/публиковать/секреты/удалять) роль не
    несёт и не может нести — таких полей у неё нет."""
    name: str
    department: str
    capabilities: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True, slots=True)
class Workstream:
    id: str
    name: str
    department: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class TaskDependency:
    upstream: str                 # id задачи, которая должна завершиться DONE
    kind: str = "hard"            # hard — без DONE upstream задача пропускается


@dataclass(frozen=True, slots=True)
class ApprovalRequirement:
    kind: str                     # spend | publish | credentials | destructive
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in GATED_KINDS:
            raise ValueError(f"unknown approval kind {self.kind!r}; expected one of {GATED_KINDS}")


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """Что верификатор должен наблюдать СВЕЖИМ чтением (зеркало ExpectedState)."""
    kind: str                     # site | file | db | browser | ...
    target: str
    expect: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "expect", _freeze(self.expect))


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approved: bool
    approver: str = ""            # кто решил (human:<id> | policy:<name>); не модель
    reason: str = ""


@dataclass(frozen=True, slots=True)
class BudgetEnvelope:
    max_total_cost: float
    max_task_cost: float | None = None
    max_tasks: int | None = None
    currency: str = "credits"

    def allows(self, task_cost: float, spent: float, executed: int) -> tuple[bool, str]:
        if task_cost < 0:
            return False, "negative cost estimate"
        if self.max_task_cost is not None and task_cost > self.max_task_cost:
            return False, f"task estimate {task_cost} > max_task_cost {self.max_task_cost}"
        if spent + task_cost > self.max_total_cost:
            return False, f"spent {spent} + {task_cost} > max_total_cost {self.max_total_cost}"
        if self.max_tasks is not None and executed >= self.max_tasks:
            return False, f"executed {executed} >= max_tasks {self.max_tasks}"
        return True, "within envelope"


@dataclass(frozen=True, slots=True)
class CompanyTask:
    id: str
    workstream_id: str
    title: str
    action: str                   # capability-имя, не свободный текст (как CompiledStep.action)
    role: str                     # AgentRole.name — маршрутизация, не полномочие
    kind: str = "read"            # read | write | publish | spend | ...
    dependencies: tuple[TaskDependency, ...] = ()
    requires_approval: tuple[ApprovalRequirement, ...] = ()
    evidence_requirements: tuple[EvidenceRequirement, ...] = ()
    estimated_cost: float = 0.0
    params: Mapping[str, Any] = field(default_factory=dict)
    max_attempts: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", _freeze(self.params))

    @property
    def depends_on(self) -> tuple[str, ...]:
        return tuple(d.upstream for d in self.dependencies)

    @property
    def gated(self) -> bool:
        return bool(self.requires_approval)


@dataclass(frozen=True, slots=True)
class CompanyPlan:
    objective: CompanyObjective
    budget: BudgetEnvelope
    departments: tuple[Department, ...] = ()
    roles: tuple[AgentRole, ...] = ()
    workstreams: tuple[Workstream, ...] = ()
    tasks: tuple[CompanyTask, ...] = ()

    def by_id(self) -> dict[str, CompanyTask]:
        out: dict[str, CompanyTask] = {}
        for t in self.tasks:
            if t.id in out:
                raise ValueError(f"duplicate task id {t.id!r}")
            out[t.id] = t
        return out

    def ordered(self) -> list[CompanyTask]:
        """Топологический порядок (Kahn), как CompiledTask.ordered;
        ValueError при цикле или ссылке на неизвестную задачу."""
        by_id = self.by_id()
        indeg = {tid: 0 for tid in by_id}
        for t in self.tasks:
            for d in t.depends_on:
                if d not in by_id:
                    raise ValueError(f"task {t.id!r} depends on unknown {d!r}")
                if d == t.id:
                    raise ValueError(f"task {t.id!r} depends on itself")
                indeg[t.id] += 1
        ready = [tid for tid, n in indeg.items() if n == 0]
        out: list[CompanyTask] = []
        while ready:
            tid = ready.pop(0)
            out.append(by_id[tid])
            for t in self.tasks:
                if tid in t.depends_on:
                    indeg[t.id] -= 1
                    if indeg[t.id] == 0:
                        ready.append(t.id)
        if len(out) != len(self.tasks):
            raise ValueError("cycle in company task DAG")
        return out

    def validate(self) -> None:
        """Целостность плана: DAG ациклический, роли/потоки существуют, гейтуемые
        виды задач объявляют требование одобрения (иначе их нельзя выполнять)."""
        roles = {r.name for r in self.roles}
        streams = {w.id for w in self.workstreams}
        for t in self.tasks:
            if t.role not in roles:
                raise ValueError(f"task {t.id!r} assigned to unknown role {t.role!r}")
            if t.workstream_id not in streams:
                raise ValueError(f"task {t.id!r} in unknown workstream {t.workstream_id!r}")
            if t.kind in GATED_KINDS and not t.requires_approval:
                raise ValueError(f"task {t.id!r} of kind {t.kind!r} must declare requires_approval")
        self.ordered()

    def dag(self) -> dict[str, tuple[str, ...]]:
        return {t.id: t.depends_on for t in self.tasks}


@dataclass(frozen=True, slots=True)
class WorkResult:
    """Что заявил исполнитель. Это САМООТЧЁТ — не доказательство."""
    task_id: str
    ok: bool
    summary: str = ""
    cost: float = 0.0
    claims: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "claims", _freeze(self.claims))


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """Зеркало bcc.v2.verification.VerificationResult: статус + свежие
    наблюдения. Порождается внедрённым verifier, не рантаймом."""
    status: str                                    # VERIFIED | FAILED | UNVERIFIED
    reason: str = ""
    evidence: tuple[str, ...] = ()
    observed: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in VERIFICATION_STATUSES:
            raise ValueError(f"bad verification status {self.status!r}")
        object.__setattr__(self, "observed", _freeze(self.observed))

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"


@dataclass(slots=True)
class TaskOutcome:
    task_id: str
    state: str = "PENDING"
    attempts: int = 0
    result: WorkResult | None = None
    approval: ApprovalDecision | None = None
    verification: VerificationOutcome | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.state not in TASK_STATES:
            raise ValueError(f"bad task state {self.state!r}")

    def set_state(self, state: str, reason: str = "") -> None:
        if state not in TASK_STATES:
            raise ValueError(f"bad task state {state!r}")
        self.state = state
        if reason:
            self.reason = reason

    @property
    def evidence(self) -> tuple[str, ...]:
        return self.verification.evidence if self.verification else ()


@dataclass(slots=True)
class CompanyRunState:
    outcomes: dict[str, TaskOutcome] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    spent: float = 0.0
    executed: int = 0
    rounds: int = 0
    kpi_before: dict[str, float] = field(default_factory=dict)
    kpi_after: dict[str, float] = field(default_factory=dict)

    def outcome(self, task_id: str) -> TaskOutcome:
        return self.outcomes.setdefault(task_id, TaskOutcome(task_id=task_id))

    def states(self) -> dict[str, str]:
        return {tid: o.state for tid, o in self.outcomes.items()}


@dataclass(frozen=True, slots=True)
class CompanyReport:
    objective_id: str
    objective_title: str
    status: str                                    # VERIFIED | FAILED | UNVERIFIED
    completion: str                                # COMPLETE | PARTIAL
    dag: Mapping[str, tuple[str, ...]]
    assignments: Mapping[str, str]                 # task_id → role
    task_states: Mapping[str, str]
    kpi_before: Mapping[str, float]
    kpi_after: Mapping[str, float]
    kpi_summary: tuple[Mapping[str, Any], ...]
    outcomes: tuple[TaskOutcome, ...]
    denied: tuple[str, ...]
    budget: Mapping[str, Any]
    rounds: int
    trace: tuple[Mapping[str, Any], ...]
    learning_records: tuple[Mapping[str, Any], ...]

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"

    def evidence(self) -> dict[str, tuple[str, ...]]:
        return {o.task_id: o.evidence for o in self.outcomes}

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id, "objective_title": self.objective_title,
            "status": self.status, "completion": self.completion,
            "dag": {k: list(v) for k, v in self.dag.items()},
            "assignments": dict(self.assignments), "task_states": dict(self.task_states),
            "kpi_before": dict(self.kpi_before), "kpi_after": dict(self.kpi_after),
            "kpi_summary": [dict(s) for s in self.kpi_summary],
            "denied": list(self.denied), "budget": dict(self.budget), "rounds": self.rounds,
            "evidence": {k: list(v) for k, v in self.evidence().items()},
            "learning_records": [dict(r) for r in self.learning_records],
        }
