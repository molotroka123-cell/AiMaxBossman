"""Stage 8 — AI Lab Sandbox: доменные модели и конечный автомат жизненного цикла.

Здесь только типы и правила переходов — без запуска процессов/контейнеров.
Правило «Sandbox OFF = OFF» и «fail closed» проходят сквозь весь пакет: этот
модуль задаёт словарь состояний и ЕДИНСТВЕННЫЙ разрешённый граф переходов;
любая другая смена состояния — InvalidTransition (ошибка кода, а не рантайма).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------
# Конечный автомат жизненного цикла песочницы
# --------------------------------------------------------------------------

class SandboxState(str, Enum):
    DISABLED = "DISABLED"        # фича выключена — ничего не поднято
    REQUESTED = "REQUESTED"      # заявка создана, ещё не допущена
    ADMITTED = "ADMITTED"        # прошла политику + resource admission
    PREPARING = "PREPARING"      # рантайм готовит среду (workspace, mounts)
    READY = "READY"              # среда готова, задача ещё не запущена
    RUNNING = "RUNNING"          # исполнение идёт
    PAUSED = "PAUSED"            # временно приостановлена (можно возобновить)
    FROZEN = "FROZEN"            # заморожена для расследования/форка (не возобновляется в исходную)
    COMPLETED = "COMPLETED"      # успешно завершена
    FAILED = "FAILED"            # завершилась ошибкой
    DESTROYING = "DESTROYING"    # идёт снос среды
    DESTROYED = "DESTROYED"      # среда снесена, ресурсы освобождены


# Терминальные состояния — из них нет исходящих переходов, кроме уже пройденных.
TERMINAL_STATES: frozenset[SandboxState] = frozenset({SandboxState.DESTROYED})

# Единственный разрешённый граф переходов. Всё, чего здесь нет, — запрещено.
_TRANSITIONS: dict[SandboxState, frozenset[SandboxState]] = {
    SandboxState.DISABLED: frozenset({SandboxState.REQUESTED}),
    SandboxState.REQUESTED: frozenset({SandboxState.ADMITTED, SandboxState.FAILED, SandboxState.DESTROYING}),
    SandboxState.ADMITTED: frozenset({SandboxState.PREPARING, SandboxState.FAILED, SandboxState.DESTROYING}),
    SandboxState.PREPARING: frozenset({SandboxState.READY, SandboxState.FAILED, SandboxState.DESTROYING}),
    SandboxState.READY: frozenset({SandboxState.RUNNING, SandboxState.FROZEN, SandboxState.FAILED, SandboxState.DESTROYING}),
    SandboxState.RUNNING: frozenset({SandboxState.PAUSED, SandboxState.FROZEN, SandboxState.COMPLETED,
                                     SandboxState.FAILED, SandboxState.DESTROYING}),
    SandboxState.PAUSED: frozenset({SandboxState.RUNNING, SandboxState.FROZEN, SandboxState.FAILED,
                                    SandboxState.DESTROYING}),
    SandboxState.FROZEN: frozenset({SandboxState.DESTROYING, SandboxState.FAILED}),
    SandboxState.COMPLETED: frozenset({SandboxState.DESTROYING}),
    SandboxState.FAILED: frozenset({SandboxState.DESTROYING}),
    SandboxState.DESTROYING: frozenset({SandboxState.DESTROYED, SandboxState.FAILED}),
    SandboxState.DESTROYED: frozenset(),
}


def can_transition(src: SandboxState, dst: SandboxState) -> bool:
    return dst in _TRANSITIONS.get(src, frozenset())


def allowed_transitions(src: SandboxState) -> frozenset[SandboxState]:
    return _TRANSITIONS.get(src, frozenset())


# --------------------------------------------------------------------------
# Политика, сеть, риск, изоляция
# --------------------------------------------------------------------------

class PolicyMode(str, Enum):
    SAFE = "SAFE"            # лёгкая rootless-изоляция для доверенной/простой работы
    DEVELOPER = "DEVELOPER"  # усиленный контейнер/gVisor-класс
    CONNECTED = "CONNECTED"  # сильная изоляция + контролируемый egress
    HOSTILE = "HOSTILE"      # аппаратный MicroVM/VM для неизвестного/высокорискового кода


class NetworkMode(str, Enum):
    OFFLINE = "OFFLINE"      # по умолчанию — никакого egress
    ALLOWLIST = "ALLOWLIST"  # только явные назначения
    INTERNET = "INTERNET"    # явный выбор политики/пользователя


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    HOSTILE = "HOSTILE"


class IsolationTier(str, Enum):
    """Технология изоляции по возрастанию силы. Риск задаёт МИНИМАЛЬНЫЙ tier;
    fail-closed запрещает опускаться ниже, даже если рантайм недоступен."""
    ROOTLESS = "ROOTLESS"      # namespaces / rootless
    CONTAINER = "CONTAINER"    # gVisor-класс
    MICROVM = "MICROVM"        # аппаратный VM/MicroVM

    @property
    def rank(self) -> int:
        return {"ROOTLESS": 0, "CONTAINER": 1, "MICROVM": 2}[self.value]


# Риск → минимально допустимая изоляция (ниже опускаться нельзя).
RISK_MIN_ISOLATION: dict[RiskLevel, IsolationTier] = {
    RiskLevel.LOW: IsolationTier.ROOTLESS,
    RiskLevel.MEDIUM: IsolationTier.CONTAINER,
    RiskLevel.HIGH: IsolationTier.CONTAINER,
    RiskLevel.HOSTILE: IsolationTier.MICROVM,
}

# Режим политики → минимально допустимая изоляция.
POLICY_MIN_ISOLATION: dict[PolicyMode, IsolationTier] = {
    PolicyMode.SAFE: IsolationTier.ROOTLESS,
    PolicyMode.DEVELOPER: IsolationTier.CONTAINER,
    PolicyMode.CONNECTED: IsolationTier.CONTAINER,
    PolicyMode.HOSTILE: IsolationTier.MICROVM,
}


@dataclass(slots=True)
class ResourceRequest:
    """Оценка стоимости среды ДО создания (идёт в Resource Brain admission)."""
    ram_bytes: int = 512 * 1024 * 1024
    disk_bytes: int = 1024 * 1024 * 1024
    cpu_cores: float = 1.0
    max_pids: int = 256
    max_open_files: int = 1024
    wall_time_seconds: int = 600
    net_quota_bytes: int | None = None


@dataclass(slots=True)
class RuntimeCapabilities:
    """Что реально умеет конкретный рантайм на этом хосте (для fail-closed)."""
    name: str
    tiers: frozenset[IsolationTier] = field(default_factory=frozenset)
    supports_offline: bool = True
    supports_allowlist: bool = False
    supports_readonly_root: bool = False
    supports_seccomp: bool = False
    supports_pid_limit: bool = False
    supports_mem_limit: bool = False

    def provides(self, tier: IsolationTier) -> bool:
        return tier in self.tiers


@dataclass(slots=True)
class SandboxSpec:
    """Заявка на песочницу от вызывающей стороны (агент/API)."""
    task: str
    policy_mode: PolicyMode = PolicyMode.SAFE
    network_mode: NetworkMode = NetworkMode.OFFLINE
    risk_hint: RiskLevel | None = None
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    allowlist: tuple[str, ...] = ()          # хосты для NetworkMode.ALLOWLIST
    secret_scopes: tuple[str, ...] = ()      # запрошенные брокером scope'ы
    workspace_source: str | None = None      # путь-источник (копируется, не монтируется как есть)
    # Источник считается НЕДОВЕРЕННЫМ по умолчанию (fail closed): внешний код
    # поднимает риск до MEDIUM и требует контейнерной изоляции. Явный
    # trusted_source=True — осознанное решение вызывающей стороны для своего же
    # рабочего каталога; риск тогда не поднимается.
    trusted_source: bool = False
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RiskAssessment:
    level: RiskLevel
    reasons: tuple[str, ...]
    min_isolation: IsolationTier


@dataclass(slots=True)
class SandboxPolicy:
    """Разрешённая (после fail-closed резолвинга) конфигурация среды."""
    mode: PolicyMode
    network_mode: NetworkMode
    isolation_tier: IsolationTier
    allowlist: tuple[str, ...]
    read_only_root: bool
    drop_caps: bool
    no_new_privs: bool
    mount_host_secrets: bool = False   # ВСЕГДА False — инвариант non-negotiable #4
    reuse_prod_browser_profile: bool = False  # ВСЕГДА False — non-negotiable #9


@dataclass(slots=True)
class SecretGrant:
    id: str
    sandbox_id: str
    scope: str
    issued_at: float
    ttl_seconds: float
    revoked: bool = False

    def is_valid(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self.revoked:
            return False
        return (now - self.issued_at) < self.ttl_seconds


@dataclass(slots=True)
class Artifact:
    rel_path: str
    size: int
    sha256: str
    quarantined: bool = False
    reasons: tuple[str, ...] = ()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass(slots=True)
class SandboxSession:
    """Живой объект песочницы: заявка + разрешённая политика + состояние."""
    id: str
    spec: SandboxSpec
    policy: SandboxPolicy | None = None
    risk: RiskAssessment | None = None
    state: SandboxState = SandboxState.REQUESTED
    lease_id: str | None = None
    runtime_handle: Any = None
    # Код возврата процесса песочницы. Живёт на сессии, чтобы вызывающему не
    # приходилось лезть во внутренности конкретного рантайма.
    exit_code: int | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[tuple[float, SandboxState, str]] = field(default_factory=list)
    error: str | None = None

    def record(self, state: SandboxState, note: str = "") -> None:
        self.state = state
        self.updated_at = time.time()
        self.history.append((self.updated_at, state, note))
