"""Криптографическое ядро Stage 6 (только stdlib, без FastAPI/БД).

Здесь живут: таксономия скоупов, dataclass-записи (устройство/сессия/принципал),
помощники по токенам (секрет генерируется, хранится ТОЛЬКО как sha256) и
синхронный in-memory `DeviceRegistry` — контракт совместимости для приёмочного
теста `test_device_revoke`.

Инвариант безопасности: сырой секрет возвращается вызывающему РОВНО один раз и
нигде не сохраняется и не логируется; в хранилище идёт только его sha256-хэш;
сверка — в постоянном времени через `hmac.compare_digest`.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

# --- скоупы: единственный источник правды по правам устройства ---
SCOPE_CHAT = "chat"          # обычный диалог, задачи/проекты
SCOPE_EVENTS = "events"      # подписка на push-события
SCOPE_APPROVE = "approve"    # решение подтверждений (approvals.decide)
SCOPE_ADMIN = "admin"        # провижининг устройств, экстренная блокировка

KNOWN_SCOPES = frozenset({SCOPE_CHAT, SCOPE_EVENTS, SCOPE_APPROVE, SCOPE_ADMIN})

# Префиксы токенов позволяют маршрутизировать поиск (креденшл устройства vs
# токен сессии) без перебора обеих таблиц. Сам префикс — не секрет.
DEVICE_TOKEN_PREFIX = "rcd_"   # remote-client device
SESSION_TOKEN_PREFIX = "rcs_"  # remote-client session


def hash_token(raw: str) -> str:
    """sha256-хэш секрета (то, что реально кладём в хранилище)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def constant_time_eq(stored_hash: str, presented_hash: str) -> bool:
    """Сравнение хэшей в постоянном времени (защита от тайминг-атаки)."""
    return hmac.compare_digest(stored_hash, presented_hash)


def new_device_id() -> str:
    return "dev_" + secrets.token_hex(8)


def new_session_id() -> str:
    return "ses_" + secrets.token_hex(8)


def new_device_secret() -> str:
    return DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)


def new_session_secret() -> str:
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def normalize_scopes(scopes) -> frozenset[str]:
    """Привести к frozenset и отбросить пустые значения (без валидации на
    неизвестные — это делает вызывающий слой в зависимости от контекста)."""
    return frozenset(s for s in scopes if s)


# --- записи домена ---

@dataclass(slots=True)
class DeviceRecord:
    id: str
    name: str
    scopes: frozenset[str]
    revoked: bool = False
    locked: bool = False
    created_at: float = field(default_factory=time.time)

    @property
    def active(self) -> bool:
        return not self.revoked and not self.locked


@dataclass(slots=True)
class SessionRecord:
    id: str
    device_id: str
    revoked: bool = False
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass(slots=True)
class CredentialMatch:
    """Результат поиска креденшла по хэшу: id устройства и хранимый хэш
    (нужен, чтобы финально подтвердить совпадение через compare_digest)."""
    device_id: str
    token_sha256: str
    session_id: str | None = None


@dataclass(slots=True, frozen=True)
class Principal:
    """Аутентифицированный субъект запроса: устройство и (опционально) сессия."""
    device_id: str
    scopes: frozenset[str]
    name: str = ""
    session_id: str | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


# --- Синхронный in-memory реестр: контракт приёмочного теста ---

@dataclass(slots=True)
class _Device:
    id: str
    name: str
    token_hash: str
    scopes: frozenset[str]
    revoked: bool = False
    created_at: float = 0.0


class DeviceRegistry:
    """TEST/PROTOTYPE-хранилище устройств в памяти.

    Оставлено как синхронная инъектируемая реализация по умолчанию и как контракт
    приёмочного теста (`enroll`/`verify`/`revoke`). Боевой путь — Postgres через
    `PostgresDeviceStore` (см. store.py). Секрет хранится ХЭШИРОВАННЫМ, сверка —
    в постоянном времени; `verify` дополнительно проверяет скоуп, если он задан.
    """

    __slots__ = ("_devices",)

    def __init__(self) -> None:
        self._devices: dict[str, _Device] = {}

    def enroll(self, name: str, scopes=(SCOPE_CHAT, SCOPE_EVENTS)) -> tuple[str, str]:
        """Зарегистрировать устройство. Возвращает (device_id, raw_token).
        raw_token показывается вызывающему ЕДИНСТВЕННЫЙ раз."""
        raw = new_device_secret()
        did = new_device_id()
        self._devices[did] = _Device(
            id=did, name=name, token_hash=hash_token(raw),
            scopes=normalize_scopes(scopes), revoked=False, created_at=time.time())
        return did, raw

    def verify(self, device_id: str, token: str, scope: str | None = None) -> bool:
        """Проверить токен устройства (и опционально наличие скоупа).
        Постоянное время сравнения; отозванное устройство всегда False."""
        d = self._devices.get(device_id)
        if not d or d.revoked:
            return False
        ok = constant_time_eq(d.token_hash, hash_token(token))
        return ok and (scope is None or scope in d.scopes)

    def revoke(self, device_id: str) -> None:
        d = self._devices.get(device_id)
        if d is not None:
            d.revoked = True

    def scopes(self, device_id: str) -> frozenset[str]:
        d = self._devices.get(device_id)
        return d.scopes if d else frozenset()
