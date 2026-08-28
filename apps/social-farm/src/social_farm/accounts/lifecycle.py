"""Подключение, обновление и отзыв — одним местом, где сходятся токен и секрет.

Порядок подключения взят из `52_OAUTH_TOKEN_LIFECYCLE` буквально: создать
ожидающий аккаунт, выдать одноразовый nonce, обменять код на сервере, положить
значение **только** в хранилище секретов, определить личность аккаунта у
провайдера, сохранить `auth_ref`, собрать возможности, подписаться на
webhook'и где возможно, отметить CONNECTED.

Два места этого порядка стоят объяснения.

**Обмен кода делается на сервере, и значение сюда приходит уже полученным.**
Этот файл не ходит в сеть: обмен — дело адаптера, у которого есть транспорт и
профиль провайдера. Здесь — то, что происходит с полученным значением, и
происходит с ним ровно одно: оно уходит в хранилище и больше нигде не
появляется, даже в возвращаемом значении функции.

**Отзыв не «помечает» токен.** Он затирает значение в хранилище, переводит
запись в `REVOKED`, а аккаунт — в `AUTH_EXPIRED`. Дальше действия не
предлагаются, потому что снимок возможностей после отзыва пересобирается и
оказывается пустым: тихого отказа при каждой попытке быть не должно, владелец
должен видеть причину раньше, чем нажмёт.
"""
from __future__ import annotations

import secrets as _secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from ..security.redaction import audit_detail
from ..security.vault import SecretValue, SecretVault
from .directory import (AccountDirectory, AccountRecord, AccountStatus, AccountType)
from .tokens import ScopeDrift, TokenRecord, TokenState


class ConnectError(RuntimeError):
    """Подключение не состоялось. Аккаунт остаётся в PENDING_CONNECT."""


class StateNonceError(ConnectError):
    """Обратный вызов пришёл с чужим или уже использованным nonce."""


@dataclass(frozen=True, slots=True)
class ConnectOutcome:
    """Итог подключения. Значения токена здесь нет — только ссылка и состояние."""

    account: AccountRecord
    auth_ref: str
    capabilities_stale: bool = True
    audit: dict[str, Any] | None = None


class NonceStore:
    """Одноразовые nonce для обратного вызова OAuth.

    Одноразовость исполняется буквально: `consume` отдаёт значение один раз.
    Повторно принятый state неотличим от подделки, а значит должен быть
    отклонён так же, как подделка.
    """

    def __init__(self) -> None:
        self._issued: dict[str, tuple[str, str]] = {}

    def issue(self, account_id: str) -> str:
        nonce = _secrets.token_urlsafe(32)
        self._issued[nonce] = (account_id, datetime.now(timezone.utc).isoformat())
        return nonce

    def consume(self, nonce: str, *, account_id: str) -> None:
        stored = self._issued.pop(nonce, None)
        if stored is None:
            raise StateNonceError(
                "state обратного вызова неизвестен или уже использован")
        if stored[0] != account_id:
            raise StateNonceError("state обратного вызова выдан другому аккаунту")

    def pending(self) -> int:
        return len(self._issued)


class AccountAuthService:
    """Жизненный цикл авторизации аккаунта поверх хранилища секретов."""

    def __init__(self, *, vault: SecretVault, directory: AccountDirectory) -> None:
        self._vault = vault
        self._directory = directory
        self._nonces = NonceStore()

    # -- подключение ------------------------------------------------------
    def begin_connect(self, *, account_id: str, provider: str,
                      policy_profile_id: str = "default",
                      timezone_name: str = "UTC") -> tuple[AccountRecord, str]:
        """Шаги 1–2: ожидающий аккаунт и одноразовый nonce.

        Аккаунт создаётся ДО перехода к провайдеру. Иначе обратный вызов
        придёт в никуда и его придётся привязывать по косвенным признакам —
        ровно тот способ, которым чужой обратный вызов и привязывается.

        Переподключение существующего аккаунта НЕ обнуляет запись: личность у
        провайдера остаётся, и именно она потом сверяется с тем, что принёс
        обратный вызов. Стереть её здесь значит снять эту проверку.
        """
        try:
            record = self._directory.get(account_id)
        except KeyError:
            record = AccountRecord(id=account_id, provider=provider,
                                   policy_profile_id=policy_profile_id,
                                   timezone=timezone_name,
                                   status=AccountStatus.PENDING_CONNECT)
            self._directory.put(record)
        return record, self._nonces.issue(account_id)

    def complete_connect(self, *, account_id: str, state_nonce: str,
                         token_value: str, provider_account_id: str,
                         handle: str | None = None,
                         account_type: AccountType = AccountType.UNKNOWN,
                         scopes: Iterable[str] = (), expires_at: str | None = None,
                         refreshable: bool = False, refresh_after: str | None = None,
                         token_type: str = "bearer",
                         provider_app_ref: str | None = None) -> ConnectOutcome:
        """Шаги 4–9: проверить state, спрятать значение, записать ссылку.

        Переподключение существующего аккаунта не создаёт второй записи, если
        совпала личность у провайдера (`52`, раздел Reconnect). Совпадение
        проверяется по `provider_account_id`, а не по `handle`: имя меняется,
        идентификатор — нет.
        """
        self._nonces.consume(state_nonce, account_id=account_id)
        record = self._directory.get(account_id)
        if (record.provider_account_id
                and record.provider_account_id != provider_account_id):
            raise ConnectError(
                f"обратный вызов принёс другой аккаунт провайдера "
                f"({provider_account_id}) — подключение отклонено, "
                f"чтобы не смешать два аккаунта в одной записи")

        existing_ref = record.auth_ref
        if existing_ref:
            metadata = self._vault.rotate(existing_ref, token_value,
                                          owner_account_id=account_id)
        else:
            metadata = self._vault.store(token_value, kind="oauth_access_token",
                                         owner_account_id=account_id)

        token = TokenRecord(
            auth_ref=metadata.ref, account_id=account_id, token_type=token_type,
            scopes=tuple(sorted(str(s) for s in scopes)),
            issued_at=datetime.now(timezone.utc).isoformat(), expires_at=expires_at,
            refreshable=refreshable, refresh_after=refresh_after,
            provider_app_ref=provider_app_ref,
            token_fingerprint=metadata.fingerprint)
        updated = self._directory.put(record.connected(
            provider_account_id=provider_account_id, handle=handle,
            account_type=account_type, token=token))

        # Снимок возможностей после подключения обязателен: до него ни одно
        # действие не предлагается (`G8`).
        return ConnectOutcome(
            account=updated, auth_ref=metadata.ref, capabilities_stale=True,
            audit=audit_detail(action="account.connect", account_id=account_id,
                               outcome="CONNECTED",
                               detail={"auth_ref": metadata.ref,
                                       "token_fingerprint": metadata.fingerprint,
                                       "scopes": list(token.scopes),
                                       "account_type": account_type.value}))

    # -- обновление -------------------------------------------------------
    def refresh(self, *, account_id: str, token_value: str,
                expires_at: str | None, refresh_after: str | None = None,
                scopes: Iterable[str] | None = None) -> ConnectOutcome:
        """Успешное обновление. Ссылка остаётся прежней, значение заменяется.

        Сохранение ссылки — не экономия. По ней связаны аудит, работы и
        события; новая ссылка на каждое обновление разорвала бы историю
        аккаунта на куски по числу обновлений.
        """
        record = self._directory.get(account_id)
        if not record.auth_ref or record.token is None:
            raise ConnectError(f"аккаунт {account_id} не подключён — нечего обновлять")
        metadata = self._vault.rotate(record.auth_ref, token_value,
                                      owner_account_id=account_id)
        token = record.token.refreshed(expires_at=expires_at,
                                       refresh_after=refresh_after, scopes=scopes,
                                       token_fingerprint=metadata.fingerprint)
        drift = record.token.scope_drift(scopes) if scopes is not None else ScopeDrift()
        status = (AccountStatus.DEGRADED if drift.demotes_capabilities
                  else AccountStatus.CONNECTED)
        updated = self._directory.put(
            record.with_token(token).with_status(status))
        return ConnectOutcome(
            account=updated, auth_ref=metadata.ref,
            capabilities_stale=True,
            audit=audit_detail(action="account.token.refresh", account_id=account_id,
                               outcome="REFRESHED",
                               detail={"auth_ref": metadata.ref,
                                       "token_fingerprint": metadata.fingerprint,
                                       "scope_drift": drift.to_dict()}))

    def apply_granted_scopes(self, *, account_id: str,
                             granted: Iterable[str]) -> ScopeDrift:
        """Дрейф разрешений. Пропавшее разрешение понижает аккаунт немедленно.

        «Немедленно» здесь означает: до следующего сбора возможностей, а не
        после. Между потерей разрешения и пересбором снимка есть окно, и в
        этом окне интерфейс не должен предлагать действие, которого уже нет.
        """
        record = self._directory.get(account_id)
        if record.token is None:
            raise ConnectError(f"аккаунт {account_id} не подключён")
        drift = record.token.scope_drift(granted)
        token = record.token.with_scopes(granted)
        status = record.status
        if drift.demotes_capabilities:
            status = AccountStatus.DEGRADED
        self._directory.put(record.with_token(token).with_status(status))
        return drift

    # -- отзыв и ошибки ---------------------------------------------------
    def revoke(self, account_id: str, *, reason: str = "") -> ConnectOutcome:
        """Отзыв токена. После него действия не предлагаются.

        Хранилище затирает значение, запись переходит в `REVOKED`, аккаунт —
        в `AUTH_EXPIRED`. Снимок возможностей, собранный после этого, не
        содержит ни одного выполнимого действия: владелец видит причину, а не
        серию отказов.
        """
        record = self._directory.get(account_id)
        if record.auth_ref:
            self._vault.revoke(record.auth_ref)
        token = record.token.revoked() if record.token else None
        updated = self._directory.put(
            record.with_token(token).with_status(AccountStatus.AUTH_EXPIRED))
        return ConnectOutcome(
            account=updated, auth_ref=record.auth_ref or "", capabilities_stale=True,
            audit=audit_detail(action="account.token.revoke", account_id=account_id,
                               outcome="REVOKED",
                               detail={"auth_ref": record.auth_ref,
                                       "reason": reason}))

    def on_provider_auth_error(self, *, account_id: str,
                               error_class: str) -> AccountRecord:
        """Классификация отказа авторизации от провайдера (`52`, invalidation).

        Обновление пробуется, только если оно безопасно: токен объявлен
        обновляемым и провайдер сказал именно про срок. Во всех остальных
        случаях аккаунт уходит в `AUTH_EXPIRED`, а мутации прекращаются.
        """
        record = self._directory.get(account_id)
        if record.token is None:
            return self._directory.put(record.with_status(AccountStatus.AUTH_EXPIRED))
        token = record.token.on_auth_error(error_class)
        status = record.status
        if token.state() in (TokenState.REAUTH_REQUIRED, TokenState.REVOKED):
            status = AccountStatus.AUTH_EXPIRED
        elif error_class == "PERMISSION_MISSING":
            status = AccountStatus.DEGRADED
        return self._directory.put(record.with_token(token).with_status(status))

    # -- значение ---------------------------------------------------------
    def resolve_for_adapter(self, account_id: str) -> SecretValue:
        """Значение токена — только здесь и только адаптеру.

        Проверяется трижды: аккаунт существует, ссылка принадлежит именно ему
        (инвариант A1 проверяется ещё раз внутри хранилища), состояние токена
        допускает обращение к провайдеру.
        """
        record = self._directory.get(account_id)
        if not record.auth_ref or record.token is None:
            raise ConnectError(f"аккаунт {account_id} не авторизован")
        if not record.token.allows_provider_calls():
            raise ConnectError(
                f"токен аккаунта {account_id} в состоянии "
                f"{record.token.state().value}: обращаться к провайдеру нельзя")
        return self._vault.resolve_for_adapter(record.auth_ref,
                                               owner_account_id=account_id)

    def health(self, account_id: str) -> dict[str, Any]:
        record = self._directory.get(account_id)
        vault_health = (self._vault.health(record.auth_ref).to_dict()
                        if record.auth_ref else
                        {"ref": None, "present": False, "usable": False,
                         "reason": "ссылки на токен нет"})
        return {"account_id": account_id, "status": record.status.value,
                "token_state": record.token_state().value,
                "allows_mutation": record.allows_mutation(), "secret": vault_health,
                "refresh": (record.token.refresh_plan().to_dict()
                            if record.token else None)}


__all__ = ["AccountAuthService", "ConnectError", "ConnectOutcome", "NonceStore",
           "StateNonceError"]
