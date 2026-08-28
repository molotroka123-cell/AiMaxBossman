"""Жизненный цикл токена: состояния, обновление, отзыв, дрейф разрешений.

Файл держит **метаданные** токена и ничего больше. Значение живёт в хранилище
секретов, а здесь — `auth_ref`, срок, набор выданных разрешений и состояние.
Разделение не косметическое: метаданные читает планировщик, интерфейс и мост,
и все они не должны иметь возможности прочитать токен даже по ошибке.

Три решения, которые определяют форму:

1. **Модель обновления у каждого провайдера своя** (`52_OAUTH_TOKEN_LIFECYCLE`:
   «must not assume all providers use the same refresh model»). Поэтому запись
   не решает, как обновляться, — она хранит то, что сказал адаптер:
   обновляемый ли токен, до какого момента он годен, с какого момента пора
   обновлять, требуется ли участие человека.

2. **Отзыв — не разновидность истечения.** Истёкший токен обновляется сам,
   отозванный требует человека. Свести их в одно состояние значит выстроить
   бесконечный цикл попыток обновления там, где нужно письмо владельцу.

3. **Пропавшее разрешение понижает возможности немедленно.** Спека: «demote
   unsupported actions immediately». Не при следующем сборе возможностей, не
   через сутки: набор разрешений изменился — снимок недействителен.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable

# Сколько остаётся до истечения, когда токен пора обновлять, если провайдер не
# сказал иначе. Значение в конфигурации, а не в коде вызывающего (`G4`).
DEFAULT_REFRESH_MARGIN = timedelta(hours=24)


class TokenState(str, Enum):
    """Состояния токена. Перечень закрыт.

    `UNKNOWN` существует отдельно от `EXPIRED` намеренно: «мы не знаем срока»
    и «срок вышел» ведут к разным действиям. Первое — повод спросить
    провайдера, второе — повод обновить токен.
    """

    ACTIVE = "ACTIVE"
    NEEDS_REFRESH = "NEEDS_REFRESH"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    UNKNOWN = "UNKNOWN"


# Состояния, при которых внешние мутации не выполняются. Чтение локальной
# истории при этом остаётся доступным (`52`, раздел Token invalidation).
BLOCKS_MUTATION: frozenset[TokenState] = frozenset({
    TokenState.EXPIRED, TokenState.REVOKED, TokenState.REAUTH_REQUIRED,
    TokenState.UNKNOWN,
})

# Состояния, при которых нельзя даже читать у провайдера: токена фактически нет.
BLOCKS_PROVIDER_CALLS: frozenset[TokenState] = frozenset({
    TokenState.REVOKED, TokenState.REAUTH_REQUIRED,
})


def _parse(value: Any) -> datetime | None:
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
class ScopeDrift:
    """Что изменилось в наборе выданных разрешений после переподключения."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)

    @property
    def demotes_capabilities(self) -> bool:
        """Пропавшее разрешение обязано понизить возможности немедленно."""
        return bool(self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {"added": list(self.added), "removed": list(self.removed),
                "changed": self.changed, "demotes": self.demotes_capabilities}


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """Что планировщику делать с этим токеном.

    `job` — `AUTH_REFRESH`, `REAUTH` или пусто. Планировщик не разбирается в
    моделях обновления провайдеров; он читает это поле.
    """

    due: bool
    job: str = ""
    not_before: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"due": self.due, "job": self.job, "not_before": self.not_before,
                "reason": self.reason}


@dataclass(frozen=True, slots=True)
class TokenRecord:
    """Метаданные токена. Значения здесь нет и быть не может.

    Поля — из `04_AUTH_AND_SECRET_VAULT`: ссылка, тип, разрешения, выдан,
    истекает, состояние обновления, ссылка на приложение провайдера.
    """

    auth_ref: str
    account_id: str
    token_type: str = "bearer"
    scopes: tuple[str, ...] = ()
    issued_at: str | None = None
    expires_at: str | None = None
    refreshable: bool = False
    refresh_after: str | None = None
    reauth_required: bool = False
    revoked_at: str | None = None
    provider_app_ref: str | None = None
    last_error_class: str | None = None
    token_fingerprint: str = ""

    # -- состояние --------------------------------------------------------
    def state(self, now: datetime | None = None) -> TokenState:
        """Состояние выводится из полей, а не хранится отдельно.

        Хранимое состояние рассинхронизируется с датами при первой же правке
        в обход сеттера. Вычисляемое — не может.
        """
        moment = now or datetime.now(timezone.utc)
        if self.revoked_at:
            return TokenState.REVOKED
        if self.reauth_required:
            return TokenState.REAUTH_REQUIRED
        expires = _parse(self.expires_at)
        if expires is None:
            # Провайдер не сообщил срока. Это не «вечный токен».
            return TokenState.UNKNOWN if not self.issued_at else TokenState.ACTIVE
        if moment >= expires:
            return TokenState.EXPIRED
        after = _parse(self.refresh_after) or (expires - DEFAULT_REFRESH_MARGIN)
        return TokenState.NEEDS_REFRESH if moment >= after else TokenState.ACTIVE

    def allows_mutation(self, now: datetime | None = None) -> bool:
        return self.state(now) not in BLOCKS_MUTATION

    def allows_provider_calls(self, now: datetime | None = None) -> bool:
        return self.state(now) not in BLOCKS_PROVIDER_CALLS

    def seconds_left(self, now: datetime | None = None) -> float | None:
        expires = _parse(self.expires_at)
        if expires is None:
            return None
        return (expires - (now or datetime.now(timezone.utc))).total_seconds()

    # -- планирование -----------------------------------------------------
    def refresh_plan(self, now: datetime | None = None) -> RefreshPlan:
        """Что делать дальше. Планировщик создаёт работу `AUTH_REFRESH` заранее."""
        state = self.state(now)
        if state is TokenState.REVOKED:
            return RefreshPlan(due=True, job="REAUTH",
                               reason="токен отозван — нужно переподключение аккаунта")
        if state is TokenState.REAUTH_REQUIRED:
            return RefreshPlan(due=True, job="REAUTH",
                               reason="провайдер требует повторной авторизации человеком")
        if state is TokenState.UNKNOWN:
            return RefreshPlan(due=True, job="REAUTH",
                               reason="срок токена неизвестен — состояние не подтверждено")
        if state is TokenState.EXPIRED:
            return RefreshPlan(
                due=True, job="AUTH_REFRESH" if self.refreshable else "REAUTH",
                reason="срок токена вышел")
        if state is TokenState.NEEDS_REFRESH:
            return RefreshPlan(
                due=True, job="AUTH_REFRESH" if self.refreshable else "REAUTH",
                not_before=self.refresh_after, reason="подошёл срок обновления")
        expires = _parse(self.expires_at)
        planned = (expires - DEFAULT_REFRESH_MARGIN).isoformat() if expires else None
        return RefreshPlan(due=False, not_before=self.refresh_after or planned,
                           reason="токен действителен")

    # -- переходы ---------------------------------------------------------
    def refreshed(self, *, expires_at: str | None, issued_at: str | None = None,
                  refresh_after: str | None = None, scopes: Iterable[str] | None = None,
                  token_fingerprint: str = "") -> "TokenRecord":
        """Успешное обновление. Ссылка та же — значение в хранилище заменено."""
        return replace(
            self, issued_at=issued_at or datetime.now(timezone.utc).isoformat(),
            expires_at=expires_at, refresh_after=refresh_after,
            reauth_required=False, revoked_at=None, last_error_class=None,
            scopes=tuple(sorted(scopes)) if scopes is not None else self.scopes,
            token_fingerprint=token_fingerprint or self.token_fingerprint)

    def revoked(self, at: str | None = None) -> "TokenRecord":
        """Отзыв. Обратно только через переподключение."""
        return replace(self, revoked_at=at or datetime.now(timezone.utc).isoformat(),
                       refreshable=False, token_fingerprint="")

    def needs_reauth(self, reason_class: str = "AUTH_REQUIRED") -> "TokenRecord":
        return replace(self, reauth_required=True, last_error_class=reason_class)

    def on_auth_error(self, error_class: str) -> "TokenRecord":
        """Классификация ошибки авторизации от провайдера.

        `AUTH_EXPIRED` — обновляемся, если провайдер это позволяет.
        `AUTH_REQUIRED` — обновление не поможет, нужен человек.
        `PERMISSION_MISSING` — токен цел, а разрешения нет: трогать токен
        нельзя, иначе мы «починим» тем, что сломает работающее подключение.
        """
        if error_class == "PERMISSION_MISSING":
            return replace(self, last_error_class=error_class)
        if error_class == "AUTH_EXPIRED" and self.refreshable:
            return replace(self, last_error_class=error_class,
                           refresh_after=datetime.now(timezone.utc).isoformat())
        return self.needs_reauth(error_class)

    # -- дрейф разрешений -------------------------------------------------
    def scope_drift(self, granted: Iterable[str]) -> ScopeDrift:
        fresh = set(str(s) for s in granted)
        known = set(self.scopes)
        return ScopeDrift(added=tuple(sorted(fresh - known)),
                          removed=tuple(sorted(known - fresh)))

    def with_scopes(self, granted: Iterable[str]) -> "TokenRecord":
        return replace(self, scopes=tuple(sorted(str(s) for s in granted)))

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    # -- наружу -----------------------------------------------------------
    def to_public_dict(self, now: datetime | None = None) -> dict[str, Any]:
        """То, что видит интерфейс, мост и аудит. Значения токена здесь нет.

        `token_fingerprint` — восемь знаков от sha256. По ним видно, что токен
        сменился, и по ним не восстанавливается токен.
        """
        return {"auth_ref": self.auth_ref, "account_id": self.account_id,
                "token_type": self.token_type, "scopes": list(self.scopes),
                "issued_at": self.issued_at, "expires_at": self.expires_at,
                "refreshable": self.refreshable, "refresh_after": self.refresh_after,
                "reauth_required": self.reauth_required, "revoked_at": self.revoked_at,
                "provider_app_ref": self.provider_app_ref,
                "last_error_class": self.last_error_class,
                "token_fingerprint": self.token_fingerprint,
                "token_state": self.state(now).value,
                "allows_mutation": self.allows_mutation(now)}


@dataclass(frozen=True, slots=True)
class OAuthState:
    """Одноразовый nonce для защиты обратного вызова OAuth.

    «single use» из спеки исполняется буквально: `consume` отдаёт значение
    один раз и после этого возвращает `None`. Повторно принятый state — это
    либо ошибка, либо подделка, и различить их мы не можем.
    """

    value: str
    account_id: str
    created_at: str
    used: bool = False
    _store: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_public_dict(self) -> dict[str, Any]:
        # Само значение nonce наружу не идёт: оно и есть защита.
        return {"account_id": self.account_id, "created_at": self.created_at,
                "used": self.used}


__all__ = ["BLOCKS_MUTATION", "BLOCKS_PROVIDER_CALLS", "DEFAULT_REFRESH_MARGIN",
           "OAuthState", "RefreshPlan", "ScopeDrift", "TokenRecord", "TokenState"]
