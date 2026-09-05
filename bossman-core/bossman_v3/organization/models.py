"""Organization Layer — типизированные модели (V3).

Организация отвечает на вопрос КТО делает работу. ЧТО делать — миссия сверху
(Executive OS / владелец), КАК и ЧЕМ ДОКАЗАНО — V3-ядро и замороженный V2.
Поэтому здесь нет ни исполнения, ни верификации: только отделы, роли, агенты,
ресурсы и формы, в которых доказательства нижних слоёв поднимаются наверх.

Отличия от drop-in ZIP (зафиксированы намеренно):
  * отделы — ДАННЫЕ, не Enum: новый отдел добавляется записью в реестр, а не
    правкой ядра (мандат §6);
  * роли — известный набор строк + свои: роль есть метаданные маршрутизации и
    политики, а не полномочие (та же модель, что у bossman.company);
  * доказательство (`Evidence`) несёт `verified`, которое ставит ТОЛЬКО нижний
    слой (журнал V3 / верификация V2); самоотчёт исполнителя — это `claims`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

# ---------------------------------------------------------------- roles

LEAD, EXECUTOR, REVIEWER, QA, RESEARCHER, RISK, AUDITOR = (
    "lead", "executor", "reviewer", "qa", "researcher", "risk", "auditor")
KNOWN_ROLES = frozenset({LEAD, EXECUTOR, REVIEWER, QA, RESEARCHER, RISK, AUDITOR})
# Роли, которые по смыслу ПРОВЕРЯЮТ чужую работу: их носитель не может быть
# тем же участником, что произвёл проверяемое (независимое ревью, §13).
VERIFYING_ROLES = frozenset({REVIEWER, QA, RISK, AUDITOR})

# ---------------------------------------------------------- model tiers

# Лестница эскалации (§9). Порядок — это и есть политика: дешёвое сначала.
TIER_LADDER = ("deterministic", "local_small", "local_strong", "cheap_cloud", "frontier")
TIER_RANK = {t: i for i, t in enumerate(TIER_LADDER)}
CLOUD_TIERS = frozenset({"cheap_cloud", "frontier"})


class RiskTier(str, Enum):
    LOW = "low"            # один исполнитель
    MEDIUM = "medium"      # исполнитель + независимый проверяющий
    HIGH = "high"          # lead + исполнитель + независимый reviewer/risk


class TaskState(str, Enum):
    PLANNED = "planned"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


class MissionState(str, Enum):
    RECEIVED = "received"
    ACTIVE = "active"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


# ---------------------------------------------------------- resources

@dataclass(frozen=True)
class Resources:
    """Многомерный ресурс казначейства (§15). Нули — «не ограничено/не
    израсходовано» для лимита и расхода соответственно; `fits` сравнивает
    только измерения, у которых лимит задан."""
    usd: float = 0.0
    tokens: int = 0
    compute_seconds: int = 0
    wall_seconds: int = 0
    concurrency: int = 0
    # Hybrid Treasury (Fleet): физический ресурс локального железа стоит 0 $,
    # но не 0 — GPU-секунды, резерв unified/GPU-памяти и сетевой трафик учитываются.
    gpu_seconds: int = 0
    gpu_memory_gb: float = 0.0
    network_bytes: int = 0

    _FIELDS = ("usd", "tokens", "compute_seconds", "wall_seconds", "concurrency",
               "gpu_seconds", "gpu_memory_gb", "network_bytes")

    def __add__(self, other: "Resources") -> "Resources":
        return Resources(**{f: getattr(self, f) + getattr(other, f) for f in self._FIELDS})

    def __sub__(self, other: "Resources") -> "Resources":
        return Resources(**{f: max(0, getattr(self, f) - getattr(other, f)) for f in self._FIELDS})

    def fits(self, used: "Resources") -> tuple[bool, str]:
        for name in self._FIELDS:
            limit = getattr(self, name)
            if limit and getattr(used, name) > limit:
                return False, f"{name}: {getattr(used, name)} > {limit}"
        return True, "fits"

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self._FIELDS}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "Resources":
        raw = dict(raw or {})
        return cls(usd=float(raw.get("usd", 0.0)), tokens=int(raw.get("tokens", 0)),
                   compute_seconds=int(raw.get("compute_seconds", 0)), wall_seconds=int(raw.get("wall_seconds", 0)),
                   concurrency=int(raw.get("concurrency", 0)), gpu_seconds=int(raw.get("gpu_seconds", 0)),
                   gpu_memory_gb=float(raw.get("gpu_memory_gb", 0.0)), network_bytes=int(raw.get("network_bytes", 0)))


# -------------------------------------------------------- departments

@dataclass
class Department:
    """Отдел как данные (§6). Ключ — `department_id`; всё остальное — описание,
    политика и бюджет, которые владелец меняет без правки ядра."""
    department_id: str
    purpose: str = ""
    capabilities: set[str] = field(default_factory=set)
    budget: Resources = field(default_factory=lambda: Resources(usd=10.0, tokens=250_000,
                                                                 compute_seconds=36_000))
    max_parallel: int = 4
    require_reviewer: bool = False          # ревью даже для LOW-риска
    require_risk_review: bool = False       # RISK-роль для HIGH-риска обязательна
    allowed_exports: set[str] = field(default_factory=lambda: {"summary", "verified_fact",
                                                                "artifact_ref"})
    memory_scope: str = ""                  # по умолчанию department:<id>
    status: str = "active"                  # active | paused
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.department_id:
            raise ValueError("department_id required")
        if not self.memory_scope:
            self.memory_scope = f"department:{self.department_id}"

    def to_dict(self) -> dict[str, Any]:
        return {"department_id": self.department_id, "purpose": self.purpose,
                "capabilities": sorted(self.capabilities), "budget": self.budget.to_dict(),
                "max_parallel": self.max_parallel, "require_reviewer": self.require_reviewer,
                "require_risk_review": self.require_risk_review,
                "allowed_exports": sorted(self.allowed_exports), "memory_scope": self.memory_scope,
                "status": self.status, "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Department":
        return cls(department_id=str(raw["department_id"]), purpose=str(raw.get("purpose", "")),
                   capabilities=set(raw.get("capabilities") or ()),
                   budget=Resources.from_dict(raw.get("budget")),
                   max_parallel=int(raw.get("max_parallel", 4)),
                   require_reviewer=bool(raw.get("require_reviewer", False)),
                   require_risk_review=bool(raw.get("require_risk_review", False)),
                   allowed_exports=set(raw.get("allowed_exports") or ()),
                   memory_scope=str(raw.get("memory_scope", "")), status=str(raw.get("status", "active")),
                   metadata=dict(raw.get("metadata") or {}))


# -------------------------------------------------------------- agents

@dataclass
class AgentProfile:
    """Работник рынка способностей (§9). `principal` — типизированная
    идентичность (как в bossman.deep_fix), по ней проверяется независимость
    ревьюера от производителя; `model` — чтобы одна модель под двумя именами
    не считалась двумя независимыми участниками."""
    agent_id: str
    department_id: str
    roles: set[str]
    capabilities: set[str]
    tier: str = "local_small"
    model: str = ""
    principal: str = ""
    enabled: bool = True
    current_load: int = 0
    max_load: int = 2
    latency_ms: float = 0.0
    cost_per_call_usd: float = 0.0
    context_tokens: int = 8192
    risk_clearance: RiskTier = RiskTier.MEDIUM   # максимальный риск, к которому допущен
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tier not in TIER_RANK:
            raise ValueError(f"unknown tier {self.tier!r}; expected one of {TIER_LADDER}")
        if not self.principal:
            self.principal = f"agent:{self.agent_id}"
        if isinstance(self.risk_clearance, str) and not isinstance(self.risk_clearance, RiskTier):
            self.risk_clearance = RiskTier(self.risk_clearance)

    @property
    def is_cloud(self) -> bool:
        return self.tier in CLOUD_TIERS

    def to_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "department_id": self.department_id,
                "roles": sorted(self.roles), "capabilities": sorted(self.capabilities),
                "tier": self.tier, "model": self.model, "principal": self.principal,
                "enabled": self.enabled, "current_load": self.current_load, "max_load": self.max_load,
                "latency_ms": self.latency_ms, "cost_per_call_usd": self.cost_per_call_usd,
                "context_tokens": self.context_tokens, "risk_clearance": self.risk_clearance.value,
                "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AgentProfile":
        return cls(agent_id=str(raw["agent_id"]), department_id=str(raw["department_id"]),
                   roles=set(raw.get("roles") or ()), capabilities=set(raw.get("capabilities") or ()),
                   tier=str(raw.get("tier", "local_small")), model=str(raw.get("model", "")),
                   principal=str(raw.get("principal", "")), enabled=bool(raw.get("enabled", True)),
                   current_load=int(raw.get("current_load", 0)), max_load=int(raw.get("max_load", 2)),
                   latency_ms=float(raw.get("latency_ms", 0.0)),
                   cost_per_call_usd=float(raw.get("cost_per_call_usd", 0.0)),
                   context_tokens=int(raw.get("context_tokens", 8192)),
                   risk_clearance=RiskTier(raw.get("risk_clearance", "medium")),
                   metadata=dict(raw.get("metadata") or {}))


# ------------------------------------------------------------ evidence

@dataclass(frozen=True)
class Evidence:
    """Улика, поднятая из нижнего слоя. `verified=True` допустимо ТОЛЬКО когда
    источник — журнал V3 (чек исполнения + свежая верификация) или верификация
    V2. Текст исполнителя сюда не попадает как verified — см. contracts."""
    kind: str
    ref: str
    verified: bool
    source: str = ""            # journal:<task_id>/<step_id> | bcc.v2.verification | ...
    observed_at: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "verified": self.verified, "source": self.source,
                "observed_at": self.observed_at, "detail": self.detail}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Evidence":
        return cls(str(raw["kind"]), str(raw.get("ref", "")), bool(raw.get("verified", False)),
                   str(raw.get("source", "")), str(raw.get("observed_at", "")), str(raw.get("detail", "")))


@dataclass
class WorkResult:
    """Исход делегирования. `claims` — что ЗАЯВИЛ исполнитель (не доказательство);
    `evidence` — что ПОДНЯЛ нижний слой. `success` ставит контракт после
    валидации, не исполнитель."""
    work_id: str
    executed: bool                       # нижний слой что-то реально исполнил
    evidence: list[Evidence] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)
    cost: Resources = field(default_factory=Resources)
    success: bool = False
    reason: str = ""
    produced_by: str = ""                # agent_id исполнителя
    reviewed_by: str = ""                # agent_id независимого проверяющего
    contract_errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.success and bool(self.evidence) and all(e.verified for e in self.evidence)

    def to_dict(self) -> dict[str, Any]:
        return {"work_id": self.work_id, "executed": self.executed,
                "evidence": [e.to_dict() for e in self.evidence], "claims": dict(self.claims),
                "cost": self.cost.to_dict(), "success": self.success, "reason": self.reason,
                "produced_by": self.produced_by, "reviewed_by": self.reviewed_by,
                "contract_errors": list(self.contract_errors), "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WorkResult":
        return cls(work_id=str(raw["work_id"]), executed=bool(raw.get("executed", False)),
                   evidence=[Evidence.from_dict(e) for e in raw.get("evidence") or []],
                   claims=dict(raw.get("claims") or {}), cost=Resources.from_dict(raw.get("cost")),
                   success=bool(raw.get("success", False)), reason=str(raw.get("reason", "")),
                   produced_by=str(raw.get("produced_by", "")), reviewed_by=str(raw.get("reviewed_by", "")),
                   contract_errors=list(raw.get("contract_errors") or []),
                   metadata=dict(raw.get("metadata") or {}))


# ------------------------------------------------------------- reviews

@dataclass(frozen=True)
class ReviewVerdict:
    """Вердикт независимого проверяющего (§13). Он может только ОПРОТЕСТОВАТЬ
    результат; сделать неподтверждённый результат подтверждённым он не может —
    verified остаётся свойством улик нижнего слоя."""
    reviewer_id: str
    approved: bool
    reason: str = ""
    independent: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"reviewer_id": self.reviewer_id, "approved": self.approved, "reason": self.reason,
                "independent": self.independent}
