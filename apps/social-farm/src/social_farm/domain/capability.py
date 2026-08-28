"""Настоящие возможности подключённого аккаунта.

Единого флага «полный контроль» не существует. Для каждого аккаунта строится
матрица: что он на самом деле умеет, каким путём (официальный API или браузер),
и если не умеет — то почему именно. Разница между «не поддерживается» и
«нужна проверка приложения в Meta» для владельца огромна: первое означает
«забудь», второе — «подай заявку». Свести их к одному «нельзя» значит соврать.

Из этого следует главное правило интерфейса: **действие не показывается
рабочим, пока возможность его не подтвердила**. Кнопка, которую нельзя нажать,
честнее ошибки после нажатия.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Adapter(str, Enum):
    """Путь, которым действие может быть выполнено."""

    OFFICIAL = "official"
    BROWSER = "browser"


class CapabilityStatus(str, Enum):
    """Девять состояний. Перечень закрытый — он в схеме `capability.schema.json`."""

    SUPPORTED_OFFICIAL = "SUPPORTED_OFFICIAL"
    SUPPORTED_BROWSER = "SUPPORTED_BROWSER"
    SUPPORTED_BOTH = "SUPPORTED_BOTH"
    READ_ONLY = "READ_ONLY"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    REQUIRES_APP_REVIEW = "REQUIRES_APP_REVIEW"
    REQUIRES_ACCOUNT_TYPE = "REQUIRES_ACCOUNT_TYPE"
    REQUIRES_USER_INTERACTION = "REQUIRES_USER_INTERACTION"
    TEMPORARILY_DISABLED = "TEMPORARILY_DISABLED"


# Какими путями можно выполнить действие в каждом состоянии. Пустое множество
# означает, что действие не выполняется вообще — ни автоматически, ни с
# подтверждением человека. Политика такое состояние переопределить не может:
# разрешение не создаёт возможности.
_ADAPTERS: dict[CapabilityStatus, frozenset[Adapter]] = {
    CapabilityStatus.SUPPORTED_OFFICIAL: frozenset({Adapter.OFFICIAL}),
    CapabilityStatus.SUPPORTED_BROWSER: frozenset({Adapter.BROWSER}),
    CapabilityStatus.SUPPORTED_BOTH: frozenset({Adapter.OFFICIAL, Adapter.BROWSER}),
    CapabilityStatus.READ_ONLY: frozenset(),
    CapabilityStatus.NOT_SUPPORTED: frozenset(),
    CapabilityStatus.REQUIRES_APP_REVIEW: frozenset(),
    CapabilityStatus.REQUIRES_ACCOUNT_TYPE: frozenset(),
    CapabilityStatus.REQUIRES_USER_INTERACTION: frozenset(),
    CapabilityStatus.TEMPORARILY_DISABLED: frozenset(),
}

# Что владельцу делать с этим состоянием. Текст идёт в интерфейс и в ответ
# моста, поэтому он объясняет причину, а не повторяет код состояния.
_EXPLAIN: dict[CapabilityStatus, str] = {
    CapabilityStatus.SUPPORTED_OFFICIAL: "доступно через официальный API",
    CapabilityStatus.SUPPORTED_BROWSER: "доступно только через браузерный резерв, "
                                        "и он должен быть включён для этого аккаунта",
    CapabilityStatus.SUPPORTED_BOTH: "доступно и через официальный API, и через браузер",
    CapabilityStatus.READ_ONLY: "только чтение: провайдер отдаёт данные, но менять их "
                                "этим способом нельзя",
    CapabilityStatus.NOT_SUPPORTED: "провайдер этого не умеет — ждать нечего",
    CapabilityStatus.REQUIRES_APP_REVIEW: "нужна проверка приложения у провайдера: "
                                          "подайте заявку и получите разрешение",
    CapabilityStatus.REQUIRES_ACCOUNT_TYPE: "нужен другой тип аккаунта "
                                            "(например, Business или Creator)",
    CapabilityStatus.REQUIRES_USER_INTERACTION: "провайдер требует действия человека — "
                                                "автоматически это не выполняется",
    CapabilityStatus.TEMPORARILY_DISABLED: "временно отключено; состояние стоит обновить",
}

_ACTIONABLE = frozenset(s for s, a in _ADAPTERS.items() if a)


class CapabilityError(ValueError):
    """Возможность не подтверждает действие. Это не отказ политики."""


def adapters_for(status: CapabilityStatus) -> frozenset[Adapter]:
    return _ADAPTERS[status]


def is_actionable(status: CapabilityStatus) -> bool:
    """Можно ли вообще выполнить действие — хоть каким-нибудь путём."""
    return bool(_ADAPTERS[status])


def explain(status: CapabilityStatus) -> str:
    return _EXPLAIN[status]


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class Capability:
    """Одна возможность аккаунта. Поля — ровно из `capability.schema.json`."""

    name: str
    status: CapabilityStatus
    source: str
    observed_at: str
    provider_api_version: str | None = None
    adapter_version: str = ""
    reason: str | None = None
    expires_at: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Capability":
        allowed = {"name", "status", "source", "observed_at", "provider_api_version",
                   "adapter_version", "reason", "expires_at"}
        unknown = set(raw) - allowed
        if unknown:
            # additionalProperties: false. Молча проглоченное поле — это поле,
            # которое кто-то считает работающим, а оно не работает.
            raise CapabilityError(f"неизвестные поля возможности: {sorted(unknown)}")
        for required in ("name", "status", "source", "observed_at"):
            if not raw.get(required):
                raise CapabilityError(f"в возможности нет обязательного поля {required}")
        try:
            status = CapabilityStatus(str(raw["status"]))
        except ValueError as exc:
            raise CapabilityError(
                f"неизвестное состояние возможности {raw['status']!r}; "
                f"перечень закрыт: {[s.value for s in CapabilityStatus]}") from exc
        return cls(
            name=str(raw["name"]), status=status, source=str(raw["source"]),
            observed_at=str(raw["observed_at"]),
            provider_api_version=raw.get("provider_api_version"),
            adapter_version=str(raw.get("adapter_version") or ""),
            reason=raw.get("reason"), expires_at=raw.get("expires_at"))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value, "source": self.source,
                "observed_at": self.observed_at,
                "provider_api_version": self.provider_api_version,
                "adapter_version": self.adapter_version, "reason": self.reason,
                "expires_at": self.expires_at}

    @property
    def adapters(self) -> frozenset[Adapter]:
        return adapters_for(self.status)

    @property
    def actionable(self) -> bool:
        return is_actionable(self.status)

    def is_expired(self, now: datetime | None = None) -> bool:
        """Просроченный снимок — не разрешение. Обновите его перед действием."""
        deadline = _parse_ts(self.expires_at)
        if deadline is None:
            return False
        return (now or datetime.now(timezone.utc)) >= deadline

    def why_not(self) -> str:
        return explain(self.status)


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Снимок возможностей аккаунта на момент времени.

    Версия и версия API провайдера хранятся вместе с ним: возможность,
    измеренная год назад на другой версии Graph API, — это не знание, а догадка.
    """

    account_id: str
    provider: str
    observed_at: str
    adapter_version: str = ""
    provider_api_version: str | None = None
    version: int = 1
    capabilities: dict[str, Capability] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CapabilitySnapshot":
        observed = str(raw.get("observed_at") or "")
        adapter_version = str(raw.get("adapter_version") or "")
        api_version = raw.get("provider_api_version")
        items: dict[str, Capability] = {}
        for entry in raw.get("capabilities") or []:
            # Снимок несёт общие для всех возможностей поля один раз;
            # достраиваем их, чтобы каждая возможность была самодостаточной.
            merged = {"source": raw.get("provider") or "provider",
                      "observed_at": observed, "adapter_version": adapter_version,
                      "provider_api_version": api_version, **dict(entry)}
            cap = Capability.from_dict(merged)
            if cap.name in items:
                raise CapabilityError(f"возможность {cap.name} объявлена дважды")
            items[cap.name] = cap
        return cls(account_id=str(raw.get("account_id") or ""),
                   provider=str(raw.get("provider") or ""), observed_at=observed,
                   adapter_version=adapter_version, provider_api_version=api_version,
                   version=int(raw.get("version") or 1), capabilities=items)

    def get(self, name: str) -> Capability | None:
        return self.capabilities.get(name)

    def status_of(self, name: str) -> CapabilityStatus:
        """Возможности нет в снимке — значит она НЕ поддерживается.

        Отсутствие сведений трактуется как запрет, а не как разрешение: иначе
        любая ошибка сбора возможностей открывала бы действия, которых нет.
        """
        cap = self.capabilities.get(name)
        return cap.status if cap else CapabilityStatus.NOT_SUPPORTED

    def actionable_names(self) -> list[str]:
        """Что реально можно предложить рабочим — и больше ничего."""
        return sorted(n for n, c in self.capabilities.items() if c.actionable)

    def require(self, name: str, adapter: Adapter | None = None,
                now: datetime | None = None) -> Capability:
        """Проверка перед действием. Бросает, если возможность его не подтверждает.

        Это единственная дверь: любой путь к внешнему эффекту проходит здесь
        раньше политики. Политика решает, спрашивать ли человека; возможность
        решает, существует ли действие вообще.
        """
        cap = self.capabilities.get(name)
        if cap is None:
            raise CapabilityError(
                f"возможность {name} не подтверждена для аккаунта {self.account_id}: "
                f"её нет в снимке возможностей")
        if not cap.actionable:
            raise CapabilityError(
                f"возможность {name} недоступна ({cap.status.value}): {cap.why_not()}"
                + (f"; причина от провайдера: {cap.reason}" if cap.reason else ""))
        if cap.is_expired(now):
            raise CapabilityError(
                f"снимок возможности {name} просрочен (expires_at={cap.expires_at}) — "
                f"обновите возможности аккаунта перед действием")
        if adapter is not None and adapter not in cap.adapters:
            available = ", ".join(sorted(a.value for a in cap.adapters))
            raise CapabilityError(
                f"возможность {name} недоступна через {adapter.value}: "
                f"состояние {cap.status.value} допускает только {available}")
        return cap


__all__ = ["Adapter", "Capability", "CapabilityError", "CapabilitySnapshot",
           "CapabilityStatus", "adapters_for", "explain", "is_actionable", "_ACTIONABLE"]
