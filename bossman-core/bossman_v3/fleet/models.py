"""Fleet OS — типы. Организация выбирает КТО, флот выбирает ГДЕ.

Ключевое разделение состояний (мандат §8): PLACED ≠ DISPATCHED ≠ EXECUTED ≠
VERIFIED. Размещение, аренда, heartbeat, текст узла — не доказательства
исполнения. Доказательство даёт только нижний слой (журнал V3 / верификация
V2), и флот лишь записывает ссылку на него.

Узел описывается ДАННЫМИ (§10): Ryzen AI Max+ 395 / 128 GB — узел №1, а не
архитектура; ничего из его характеристик здесь не захардкожено.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

# --------------------------------------------------------------- privacy

PRIVATE, LOCAL_ONLY, INTERNAL, PUBLIC = "private", "local_only", "internal", "public"
PRIVACY_LEVELS = (PRIVATE, LOCAL_ONLY, INTERNAL, PUBLIC)
# Классы доверия узлов. Приватная работа идёт только на trusted_local.
TRUSTED_LOCAL, TRUSTED_REMOTE, CLOUD = "trusted_local", "trusted_remote", "cloud"
TRUST_CLASSES = (TRUSTED_LOCAL, TRUSTED_REMOTE, CLOUD)


class NodeStatus(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"        # heartbeat есть, но узел сообщает о проблеме/перегрузе
    DRAINING = "draining"        # новых размещений нет; текущая работа завершается
    OFFLINE = "offline"


# ------------------------------------------------------------------ node

@dataclass
class NodeState:
    node_id: str
    hostname: str = ""
    os_name: str = ""
    ram_gb: float = 0.0
    gpu_memory_gb: float = 0.0            # для unified memory — общий пул
    gpu_name: str = ""
    unified_memory: bool = False
    capabilities: set[str] = field(default_factory=set)
    pools: set[str] = field(default_factory=set)          # private-local | coding | vision-large | ...
    models: set[str] = field(default_factory=set)         # доступные модели
    warm_models: set[str] = field(default_factory=set)    # уже загружены
    privacy_level: str = PRIVATE                          # максимально чувствительная работа, которую узел вправе принять
    trust_class: str = TRUSTED_LOCAL
    failure_domain: str = ""                              # машина/стойка/сеть/облако
    labels: dict[str, str] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.ONLINE
    load: float = 0.0                                     # 0..1
    ram_used_gb: float = 0.0
    gpu_memory_used_gb: float = 0.0
    max_concurrency: int = 2
    active_work: int = 0
    last_heartbeat_ts: float = 0.0
    registered_ts: float = 0.0
    artifacts: set[str] = field(default_factory=set)      # sha256 локально доступных артефактов

    def __post_init__(self) -> None:
        if self.privacy_level not in PRIVACY_LEVELS:
            raise ValueError(f"unknown privacy level {self.privacy_level!r}")
        if self.trust_class not in TRUST_CLASSES:
            raise ValueError(f"unknown trust class {self.trust_class!r}")
        if isinstance(self.status, str) and not isinstance(self.status, NodeStatus):
            self.status = NodeStatus(self.status)

    @property
    def ram_free_gb(self) -> float:
        return max(0.0, self.ram_gb - self.ram_used_gb)

    @property
    def gpu_free_gb(self) -> float:
        return max(0.0, self.gpu_memory_gb - self.gpu_memory_used_gb)

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "hostname": self.hostname, "os_name": self.os_name,
                "ram_gb": self.ram_gb, "gpu_memory_gb": self.gpu_memory_gb, "gpu_name": self.gpu_name,
                "unified_memory": self.unified_memory, "capabilities": sorted(self.capabilities),
                "pools": sorted(self.pools), "models": sorted(self.models), "warm_models": sorted(self.warm_models),
                "privacy_level": self.privacy_level, "trust_class": self.trust_class,
                "failure_domain": self.failure_domain, "labels": dict(self.labels), "status": self.status.value,
                "load": self.load, "ram_used_gb": self.ram_used_gb, "gpu_memory_used_gb": self.gpu_memory_used_gb,
                "max_concurrency": self.max_concurrency, "active_work": self.active_work,
                "last_heartbeat_ts": self.last_heartbeat_ts, "registered_ts": self.registered_ts,
                "artifacts": sorted(self.artifacts)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NodeState":
        raw = dict(raw)
        for key in ("capabilities", "pools", "models", "warm_models", "artifacts"):
            raw[key] = set(raw.get(key) or ())
        raw["labels"] = dict(raw.get("labels") or {})
        raw["status"] = NodeStatus(raw.get("status", "online"))
        return cls(**raw)


@dataclass(frozen=True)
class Heartbeat:
    node_id: str
    timestamp: float
    load: float = 0.0
    ram_used_gb: float = 0.0
    gpu_memory_used_gb: float = 0.0
    warm_models: tuple[str, ...] | None = None
    active_work: int | None = None
    status: NodeStatus = NodeStatus.ONLINE


# ---------------------------------------------------------- requirement

@dataclass(frozen=True)
class PlacementRequirement:
    """Что нужно работе от МЕСТА исполнения. Приходит из контракта делегирования
    (`DelegationContract.placement` + `privacy`), не из выбора модели."""
    capabilities: tuple[str, ...] = ()
    pools: tuple[str, ...] = ()
    min_ram_gb: float = 0.0
    min_gpu_memory_gb: float = 0.0
    required_models: tuple[str, ...] = ()
    allowed_os: tuple[str, ...] = ()
    privacy: str = PRIVATE
    contains_secrets: bool = False
    artifacts: tuple[str, ...] = ()            # sha256 нужных артефактов
    artifact_bytes: int = 0                    # суммарный объём (для оценки переноса)
    max_load: float = 0.9
    anti_affinity_domains: tuple[str, ...] = ()   # failure-domain'ы, которых избегать
    prefer_node: str = ""

    @classmethod
    def from_contract(cls, contract) -> "PlacementRequirement":
        p = dict(getattr(contract, "placement", {}) or {})
        caps = tuple(p.get("capabilities") or (contract.required_capability,))
        return cls(capabilities=caps, pools=tuple(p.get("pools") or ()),
                   min_ram_gb=float(p.get("min_ram_gb", 0.0)), min_gpu_memory_gb=float(p.get("min_gpu_memory_gb", 0.0)),
                   required_models=tuple(p.get("required_models") or ()), allowed_os=tuple(p.get("allowed_os") or ()),
                   privacy=str(getattr(contract, "privacy", PRIVATE) or PRIVATE),
                   contains_secrets=bool(p.get("contains_secrets", False)),
                   artifacts=tuple(p.get("artifacts") or ()), artifact_bytes=int(p.get("artifact_bytes", 0)),
                   max_load=float(p.get("max_load", 0.9)),
                   anti_affinity_domains=tuple(p.get("anti_affinity_domains") or ()),
                   prefer_node=str(p.get("prefer_node", "")))


# ---------------------------------------------------------------- lease

@dataclass(frozen=True)
class Lease:
    lease_id: str
    node_id: str
    work_id: str
    resource_class: str
    exclusive: bool
    acquired_ts: float
    expires_ts: float
    fence: int                       # монотонный fencing token по (node, resource_class)

    def alive(self, now: float) -> bool:
        return self.expires_ts > now

    def to_dict(self) -> dict[str, Any]:
        return {"lease_id": self.lease_id, "node_id": self.node_id, "work_id": self.work_id,
                "resource_class": self.resource_class, "exclusive": self.exclusive,
                "acquired_ts": self.acquired_ts, "expires_ts": self.expires_ts, "fence": self.fence}


# ---------------------------------------------------- flight state machine

class FlightState(str, Enum):
    PLANNED = "PLANNED"
    QUEUED = "QUEUED"
    PLACED = "PLACED"
    LEASED = "LEASED"
    DISPATCHED = "DISPATCHED"
    EXECUTING = "EXECUTING"
    OBSERVED = "OBSERVED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NODE_LOST = "NODE_LOST"


# Разрешённые переходы. PLACED → VERIFIED невозможен по построению: между ними
# обязаны быть DISPATCHED/EXECUTING/OBSERVED/VERIFYING.
LEGAL_TRANSITIONS: dict[FlightState, frozenset[FlightState]] = {
    FlightState.PLANNED: frozenset({FlightState.QUEUED, FlightState.PLACED, FlightState.BLOCKED, FlightState.CANCELLED}),
    FlightState.QUEUED: frozenset({FlightState.PLACED, FlightState.BLOCKED, FlightState.CANCELLED, FlightState.FAILED}),
    FlightState.PLACED: frozenset({FlightState.LEASED, FlightState.BLOCKED, FlightState.CANCELLED, FlightState.QUEUED}),
    FlightState.LEASED: frozenset({FlightState.DISPATCHED, FlightState.BLOCKED, FlightState.CANCELLED, FlightState.NODE_LOST}),
    FlightState.DISPATCHED: frozenset({FlightState.EXECUTING, FlightState.NODE_LOST, FlightState.FAILED, FlightState.BLOCKED}),
    FlightState.EXECUTING: frozenset({FlightState.OBSERVED, FlightState.NODE_LOST, FlightState.FAILED, FlightState.BLOCKED}),
    FlightState.OBSERVED: frozenset({FlightState.VERIFYING, FlightState.FAILED, FlightState.BLOCKED}),
    FlightState.VERIFYING: frozenset({FlightState.VERIFIED, FlightState.FAILED, FlightState.BLOCKED}),
    FlightState.VERIFIED: frozenset(),
    FlightState.BLOCKED: frozenset({FlightState.QUEUED, FlightState.PLACED, FlightState.CANCELLED, FlightState.FAILED}),
    FlightState.FAILED: frozenset({FlightState.QUEUED}),           # только явный requeue
    FlightState.CANCELLED: frozenset(),
    FlightState.NODE_LOST: frozenset({FlightState.QUEUED, FlightState.BLOCKED, FlightState.FAILED}),
}


class IllegalTransition(RuntimeError):
    pass


@dataclass
class FlightRecord:
    """Durable запись о полёте одной единицы работы через флот."""
    work_id: str
    mission_id: str
    state: FlightState = FlightState.PLANNED
    node_id: str = ""
    lease_id: str = ""
    fence: int = 0
    attempt: int = 0
    evidence_refs: list[str] = field(default_factory=list)   # ссылки на улики нижнего слоя
    verified_steps: list[str] = field(default_factory=list)
    reason: str = ""
    updated_ts: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"work_id": self.work_id, "mission_id": self.mission_id, "state": self.state.value,
                "node_id": self.node_id, "lease_id": self.lease_id, "fence": self.fence, "attempt": self.attempt,
                "evidence_refs": list(self.evidence_refs), "verified_steps": list(self.verified_steps),
                "reason": self.reason, "updated_ts": self.updated_ts, "history": list(self.history)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FlightRecord":
        raw = dict(raw)
        raw["state"] = FlightState(raw.get("state", "PLANNED"))
        return cls(**raw)


def mutation_key(mission_id: str, work_id: str, step_id: str, action: Mapping[str, Any] | None = None) -> str:
    """MutationIdempotencyKey: одна и та же мутация под одним ключом; повтор
    VERIFIED под этим ключом — попытка дубликата, и она отклоняется."""
    raw = json.dumps([mission_id, work_id, step_id, dict(action or {})], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------------- placement

@dataclass(frozen=True)
class NodeExplanation:
    node_id: str
    eligible: bool
    score: float
    reasons: tuple[str, ...]          # почему выбран / почему отклонён — детерминированные коды

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "eligible": self.eligible, "score": round(self.score, 3),
                "reasons": list(self.reasons)}


@dataclass(frozen=True)
class Placement:
    work_id: str
    status: str                       # PLACED | CAPABILITY_UNAVAILABLE | BLOCKED | ADMISSION_REJECTED
    node_id: str | None
    reason: str
    lease: Lease | None = None
    explanations: tuple[NodeExplanation, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "PLACED" and self.node_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {"work_id": self.work_id, "status": self.status, "node_id": self.node_id, "reason": self.reason,
                "lease": self.lease.to_dict() if self.lease else None,
                "explanations": [e.to_dict() for e in self.explanations]}


# ------------------------------------------------------------- failures

class FailureClass(str, Enum):
    NEVER_RETRY = "NEVER_RETRY"            # permission denied, unsafe action, invalid input
    HUMAN_REQUIRED = "HUMAN_REQUIRED"      # approval required
    REROUTE = "REROUTE"                    # node offline / lost — безопасно перенести, если шаг безопасен
    BACKOFF = "BACKOFF"                    # timeout / temporary
    VERIFICATION = "VERIFICATION"          # исполнилось, эффект не подтверждён — не инфраструктура


def classify_failure(reason: str) -> FailureClass:
    r = (reason or "").lower()
    if any(k in r for k in ("policydenied", "permission", "unsafeaction", "unsupportedaction", "invalid input", "hard_deny")):
        return FailureClass.NEVER_RETRY
    if any(k in r for k in ("approvaldenied", "approval", "ожидает решения", "подтверждени")):
        return FailureClass.HUMAN_REQUIRED
    if any(k in r for k in ("node lost", "node offline", "node_lost", "transport", "connection", "heartbeat")):
        return FailureClass.REROUTE
    if any(k in r for k in ("timeout", "timed out", "temporar")):
        return FailureClass.BACKOFF
    return FailureClass.VERIFICATION


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0

    def delay_for(self, attempt: int) -> float:
        if attempt <= 0:
            return 0.0
        return min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1)))


# ---------------------------------------------------------------- events

class FleetEventType(str, Enum):
    NODE_REGISTERED = "NODE_REGISTERED"
    NODE_HEARTBEAT = "NODE_HEARTBEAT"
    NODE_DEGRADED = "NODE_DEGRADED"
    NODE_DRAINING = "NODE_DRAINING"
    NODE_OFFLINE = "NODE_OFFLINE"
    TASK_QUEUED = "TASK_QUEUED"
    TASK_CLAIMED = "TASK_CLAIMED"
    TASK_PLACED = "TASK_PLACED"
    TASK_REJECTED = "TASK_REJECTED"
    TASK_DISPATCHED = "TASK_DISPATCHED"
    TASK_OBSERVED = "TASK_OBSERVED"
    TASK_VERIFIED = "TASK_VERIFIED"
    TASK_FAILED = "TASK_FAILED"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_NODE_LOST = "TASK_NODE_LOST"
    TASK_DEAD_LETTERED = "TASK_DEAD_LETTERED"
    LEASE_ACQUIRED = "LEASE_ACQUIRED"
    LEASE_RENEWED = "LEASE_RENEWED"
    LEASE_RELEASED = "LEASE_RELEASED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_RECLAIMED = "LEASE_RECLAIMED"
    DUPLICATE_PREVENTED = "DUPLICATE_PREVENTED"


@dataclass(frozen=True)
class CredentialGrant:
    """Авторизация, не секрет: флот хранит КТО/ГДЕ/ЗАЧЕМ/ДО КОГДА, а значение
    секрета остаётся у доверенного хранилища (V2 Vault) и внедряется рантаймом."""
    grant_id: str
    secret_id: str
    node_id: str
    capability: str
    scope: str                   # mission:<id> | department:<id> | organization
    expires_ts: float
    granted_by: str              # human:<id> | policy:<name> — не модель
    revoked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"grant_id": self.grant_id, "secret_id": self.secret_id, "node_id": self.node_id,
                "capability": self.capability, "scope": self.scope, "expires_ts": self.expires_ts,
                "granted_by": self.granted_by, "revoked": self.revoked}


@dataclass(frozen=True)
class ArtifactDescriptor:
    artifact_id: str
    sha256: str
    size_bytes: int
    media_type: str = "application/octet-stream"
    privacy: str = PRIVATE
