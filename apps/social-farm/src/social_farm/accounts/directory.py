"""Запись подключённого аккаунта и справочник аккаунтов.

Запись несёт `auth_ref`, а не токен. Это не деталь реализации: инвариант S1
(«ни одного сырого токена провайдера в доменной базе») держится тем, что поля
для токена в записи просто нет — не тем, что его туда не кладут.

Справочник намеренно не знает про SQL. Он протокол, и под него подставляется
репозиторий из `storage/` (W2), когда тот появится. Здесь живёт та реализация,
которая нужна адаптеру и тестам: хранение в памяти с теми же гарантиями
изоляции.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .tokens import TokenRecord, TokenState


class AccountStatus(str, Enum):
    """Состояния из `social_account.schema.json`. Перечень закрыт."""

    PENDING_CONNECT = "PENDING_CONNECT"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    BROWSER_ONLY = "BROWSER_ONLY"
    DISABLED = "DISABLED"
    DISCONNECTED = "DISCONNECTED"


class AccountType(str, Enum):
    """Тип аккаунта у провайдера.

    `UNKNOWN` — не то же самое, что `PERSONAL`. Спека прямо запрещает выводить
    возможности из типа аккаунта (`02_CAPABILITY_MODEL`: «Never infer
    capability from account name/type alone»), но тип всё равно нужен: он
    объясняет владельцу, ПОЧЕМУ возможности нет, а `REQUIRES_ACCOUNT_TYPE`
    без него превращается в загадку.
    """

    BUSINESS = "BUSINESS"
    CREATOR = "CREATOR"
    PERSONAL = "PERSONAL"
    UNKNOWN = "UNKNOWN"

    @property
    def professional(self) -> bool:
        return self in (AccountType.BUSINESS, AccountType.CREATOR)


# Состояния аккаунта, при которых внешние мутации не предлагаются вообще.
NON_MUTATING_STATUSES: frozenset[AccountStatus] = frozenset({
    AccountStatus.PENDING_CONNECT, AccountStatus.AUTH_EXPIRED,
    AccountStatus.DISABLED, AccountStatus.DISCONNECTED,
})

_STATUS_EXPLAIN: dict[AccountStatus, str] = {
    AccountStatus.PENDING_CONNECT: "подключение не завершено",
    AccountStatus.CONNECTED: "подключён",
    AccountStatus.DEGRADED: "работает частично: часть возможностей недоступна",
    AccountStatus.AUTH_EXPIRED: "доступ утрачен — переподключите аккаунт",
    AccountStatus.REQUIRES_REVIEW: "нужна проверка приложения у провайдера",
    AccountStatus.BROWSER_ONLY: "официальный путь недоступен; остался браузерный резерв",
    AccountStatus.DISABLED: "выключен владельцем",
    AccountStatus.DISCONNECTED: "отключён",
}


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """Подключённый аккаунт. Поля — из `social_account.schema.json` плюс G13."""

    id: str
    provider: str
    policy_profile_id: str = "default"
    status: AccountStatus = AccountStatus.PENDING_CONNECT
    timezone: str = "UTC"
    provider_account_id: str | None = None
    handle: str | None = None
    account_type: AccountType = AccountType.UNKNOWN
    auth_ref: str | None = None
    browser_session_ref: str | None = None
    last_sync_at: str | None = None
    # G13: колонки, которых в схеме нет, но без которых конвейер неисполним.
    media_profile_id: str | None = None
    rate_limit_bucket_id: str | None = None
    webhook_subscription_state: str = "NONE"
    token: TokenRecord | None = field(default=None, repr=False)

    # -- вычисляемое ------------------------------------------------------
    def token_state(self, now: datetime | None = None) -> TokenState:
        return self.token.state(now) if self.token else TokenState.UNKNOWN

    def allows_mutation(self, now: datetime | None = None) -> bool:
        """Мутации разрешены, только если согласны И статус, И токен.

        Согласие обоих обязательно: статус `CONNECTED` при отозванном токене —
        это рассинхронизация, и трактовать её в пользу действия нельзя.
        """
        if self.status in NON_MUTATING_STATUSES:
            return False
        return self.token is not None and self.token.allows_mutation(now)

    def why_not_mutating(self, now: datetime | None = None) -> str:
        if self.status in NON_MUTATING_STATUSES:
            return _STATUS_EXPLAIN[self.status]
        if self.token is None:
            return "аккаунт не авторизован: ссылки на токен нет"
        state = self.token.state(now)
        return {
            TokenState.REVOKED: "токен отозван — переподключите аккаунт",
            TokenState.EXPIRED: "срок токена вышел — идёт обновление",
            TokenState.REAUTH_REQUIRED: "провайдер требует повторной авторизации",
            TokenState.UNKNOWN: "состояние токена не подтверждено",
        }.get(state, "")

    # -- переходы ---------------------------------------------------------
    def with_status(self, status: AccountStatus) -> "AccountRecord":
        return replace(self, status=status)

    def with_token(self, token: TokenRecord | None) -> "AccountRecord":
        return replace(self, token=token, auth_ref=token.auth_ref if token else None)

    def connected(self, *, provider_account_id: str, handle: str | None,
                  account_type: AccountType, token: TokenRecord) -> "AccountRecord":
        return replace(self, provider_account_id=provider_account_id, handle=handle,
                       account_type=account_type, token=token, auth_ref=token.auth_ref,
                       status=AccountStatus.CONNECTED)

    def auth_lost(self) -> "AccountRecord":
        """Провайдер отказал в авторизации: мутации прекращаются.

        Чтение локальной истории остаётся: владелец не должен терять доступ к
        тому, что уже опубликовано, из-за протухшего токена.
        """
        token = self.token.needs_reauth() if self.token else None
        return replace(self, status=AccountStatus.AUTH_EXPIRED, token=token)

    # -- наружу -----------------------------------------------------------
    def to_schema_dict(self) -> dict[str, Any]:
        """Ровно поля `social_account.schema.json` (`additionalProperties: false`)."""
        return {"id": self.id, "provider": self.provider,
                "provider_account_id": self.provider_account_id,
                "handle": self.handle,
                "account_type": self.account_type.value
                if self.account_type is not AccountType.UNKNOWN else None,
                "status": self.status.value, "timezone": self.timezone,
                "auth_ref": self.auth_ref,
                "browser_session_ref": self.browser_session_ref,
                "policy_profile_id": self.policy_profile_id,
                "last_sync_at": self.last_sync_at}

    def to_public_dict(self, now: datetime | None = None) -> dict[str, Any]:
        """Представление аккаунта наружу: интерфейс, мост, аудит.

        Здесь нет и не может быть значения токена — только ссылка на него и
        состояние. Канареечный тест прогоняет через эту функцию известную
        строку-секрет и падает, если она всплыла.
        """
        body = self.to_schema_dict()
        body.update({
            "account_type_raw": self.account_type.value,
            "media_profile_id": self.media_profile_id,
            "rate_limit_bucket_id": self.rate_limit_bucket_id,
            "webhook_subscription_state": self.webhook_subscription_state,
            "status_explained": _STATUS_EXPLAIN[self.status],
            "token": self.token.to_public_dict(now) if self.token else None,
            "allows_mutation": self.allows_mutation(now),
            "why_not_mutating": self.why_not_mutating(now)})
        return body


@runtime_checkable
class AccountDirectory(Protocol):
    """Что адаптеру нужно знать об аккаунте, чтобы позвать провайдера."""

    def get(self, account_id: str) -> AccountRecord: ...
    def put(self, record: AccountRecord) -> AccountRecord: ...
    def all(self) -> list[AccountRecord]: ...


class UnknownAccount(KeyError):
    """Аккаунта нет. Это не повод создать его на лету."""


class InMemoryAccountDirectory:
    """Справочник в памяти. Под тем же протоколом живёт репозиторий из `storage/`."""

    def __init__(self, records: list[AccountRecord] | None = None) -> None:
        self._records: dict[str, AccountRecord] = {r.id: r for r in (records or [])}

    def get(self, account_id: str) -> AccountRecord:
        record = self._records.get(account_id)
        if record is None:
            raise UnknownAccount(f"аккаунта {account_id} нет в справочнике")
        return record

    def put(self, record: AccountRecord) -> AccountRecord:
        self._records[record.id] = record
        return record

    def all(self) -> list[AccountRecord]:
        return [self._records[k] for k in sorted(self._records)]


__all__ = ["AccountDirectory", "AccountRecord", "AccountStatus", "AccountType",
           "InMemoryAccountDirectory", "NON_MUTATING_STATUSES", "UnknownAccount"]
