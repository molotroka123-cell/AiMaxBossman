"""Запись о браузерном действии.

`09_INSTAGRAM_BROWSER_FALLBACK` перечисляет, что обязано попасть в аудит: тип
действия, семантическая личность цели, адрес до и после, ссылка на снимок
экрана, результат, отпечаток DOM. И одно требование поверх: «Redact sensitive
data before persistence».

Поэтому запись нельзя сериализовать мимо редактора: `to_dict` принимает его
обязательным аргументом. Забыть редакцию не получится — не на что будет
вызвать. Разметка страницы в запись не кладётся вообще: в ней лежат и cookie в
скрытых полях, и токены форм, и переписка.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .secrets import Redactor


@dataclass(frozen=True, slots=True)
class BrowserAuditRecord:
    """Одно браузерное действие, как оно будет видно человеку через месяц."""

    account_id: str
    action: str
    result: str
    at: str
    # Личность цели в человеческом виде: `button[опубликовать]#0`.
    target_identity: str = ""
    target_fingerprint: str = ""
    url_before: str = ""
    url_after: str = ""
    selector_pack_version: str = ""
    strategy_kind: str = ""
    state_before: str = ""
    state_after: str = ""
    screenshot_ref: str = ""
    idempotency_key: str = ""
    approval_ref: str = ""
    # Ссылка на секрет, если действие вводило секрет. НЕ значение.
    secret_ref: str = ""
    error_class: str = ""
    detail: str = ""

    def to_dict(self, redactor: Redactor) -> dict[str, Any]:
        """Сериализация только через редактор. Другого пути нет намеренно."""
        return redactor.scrub({
            "account_id": self.account_id, "action": self.action,
            "result": self.result, "at": self.at,
            "target_identity": self.target_identity,
            "target_fingerprint": self.target_fingerprint,
            "url_before": self.url_before, "url_after": self.url_after,
            "selector_pack_version": self.selector_pack_version,
            "strategy_kind": self.strategy_kind,
            "state_before": self.state_before, "state_after": self.state_after,
            "screenshot_ref": self.screenshot_ref,
            "idempotency_key": self.idempotency_key,
            "approval_ref": self.approval_ref,
            # Ключ называется `vault_ref`, а не `secret_ref`, сознательно:
            # редактор вычищает любое поле, в имени которого есть «secret»,
            # и это правильно — но ССЫЛКА на секрет обязана остаться видимой,
            # иначе аудит не скажет, чем именно входили. Значения здесь нет.
            "vault_ref": self.secret_ref,
            "error_class": self.error_class, "detail": self.detail,
        })


@runtime_checkable
class BrowserAuditSink(Protocol):
    def write(self, record: BrowserAuditRecord) -> None: ...


@dataclass(slots=True)
class InMemoryAuditSink:
    """Накопитель записей. Хранилище аудита — за пределами этого потока (W2)."""

    records: list[BrowserAuditRecord] = field(default_factory=list)

    def write(self, record: BrowserAuditRecord) -> None:
        self.records.append(record)

    def dicts(self, redactor: Redactor) -> list[dict[str, Any]]:
        return [record.to_dict(redactor) for record in self.records]

    def by_action(self, action: str) -> list[BrowserAuditRecord]:
        return [r for r in self.records if r.action == action]


__all__ = ["BrowserAuditRecord", "BrowserAuditSink", "InMemoryAuditSink"]
