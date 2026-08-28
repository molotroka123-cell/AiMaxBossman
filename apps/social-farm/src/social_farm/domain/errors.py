"""Нормализованные ошибки провайдера.

Два флага, которые нельзя путать, и в этом весь смысл файла:

* `retryable` — можно ли повторить НАШУ операцию;
* `safe_to_retry_external` — безопасно ли повторить ВНЕШНИЙ ЭФФЕКТ.

Они расходятся ровно в самом опасном случае. `UNKNOWN_EXTERNAL_STATE` —
`retryable=true`, потому что работу нужно продолжить, и одновременно
`safe_to_retry_external=false`, потому что публикация могла дойти. Свести их в
один флаг значит либо потерять работу, либо опубликовать дважды.

Перечень классов закрыт (`provider_error.schema.json`). Коды, которых в нём
нет, отображаются на существующие с пояснением в `safe_detail`, а перечень не
расширяется: он контракт, а не список удобных названий.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorClass(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    PERMISSION_MISSING = "PERMISSION_MISSING"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    PROVIDER_POLICY_BLOCKED = "PROVIDER_POLICY_BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    TRANSIENT_PROVIDER = "TRANSIENT_PROVIDER"
    PERMANENT_PROVIDER = "PERMANENT_PROVIDER"
    MEDIA_INVALID = "MEDIA_INVALID"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BROWSER_REQUIRES_TAKEOVER = "BROWSER_REQUIRES_TAKEOVER"
    BROWSER_STALE_TARGET = "BROWSER_STALE_TARGET"
    UNKNOWN_EXTERNAL_STATE = "UNKNOWN_EXTERNAL_STATE"
    STORAGE_ERROR = "STORAGE_ERROR"
    GENERATION_ERROR = "GENERATION_ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


# (retryable, safe_to_retry_external)
_DEFAULTS: dict[ErrorClass, tuple[bool, bool]] = {
    ErrorClass.AUTH_REQUIRED: (False, False),
    ErrorClass.AUTH_EXPIRED: (True, True),          # после обновления токена
    ErrorClass.PERMISSION_MISSING: (False, False),
    ErrorClass.CAPABILITY_UNAVAILABLE: (False, False),
    ErrorClass.PROVIDER_POLICY_BLOCKED: (False, False),
    ErrorClass.RATE_LIMITED: (True, True),          # эффекта не было — отказали на входе
    ErrorClass.TRANSIENT_PROVIDER: (True, True),
    ErrorClass.PERMANENT_PROVIDER: (False, False),
    ErrorClass.MEDIA_INVALID: (False, False),
    ErrorClass.CONTENT_REJECTED: (False, False),
    ErrorClass.WAITING_APPROVAL: (True, False),
    ErrorClass.BROWSER_REQUIRES_TAKEOVER: (True, False),
    ErrorClass.BROWSER_STALE_TARGET: (True, False),
    # Единственная пара, ради которой всё это разделение и существует.
    ErrorClass.UNKNOWN_EXTERNAL_STATE: (True, False),
    ErrorClass.STORAGE_ERROR: (True, False),
    ErrorClass.GENERATION_ERROR: (True, True),      # генерация ничего не меняет снаружи
    # Таймаут — это НЕ «не дошло». Ответа нет, значит состояние неизвестно.
    ErrorClass.TIMEOUT: (True, False),
    ErrorClass.CANCELLED: (False, False),
}

# Коды из руководств, которых нет в закрытом перечне (`DIGEST_CORE` C15).
ALIASES: dict[str, tuple[ErrorClass, str]] = {
    "FAIL_MEDIA_MISSING": (ErrorClass.STORAGE_ERROR, "FAIL_MEDIA_MISSING"),
    "FAIL_UNSUPPORTED": (ErrorClass.MEDIA_INVALID, "FAIL_UNSUPPORTED"),
    "FAIL_CORRUPT": (ErrorClass.MEDIA_INVALID, "FAIL_CORRUPT"),
    "FAIL_TOO_LARGE": (ErrorClass.MEDIA_INVALID, "FAIL_TOO_LARGE"),
    "FAIL_DURATION": (ErrorClass.MEDIA_INVALID, "FAIL_DURATION"),
    "FAIL_CODEC": (ErrorClass.MEDIA_INVALID, "FAIL_CODEC"),
    "FAIL_ASPECT": (ErrorClass.MEDIA_INVALID, "FAIL_ASPECT"),
    "FAIL_PROVIDER_RULE_UNKNOWN": (ErrorClass.MEDIA_INVALID,
                                   "FAIL_PROVIDER_RULE_UNKNOWN"),
    "DEADLINE_EXCEEDED": (ErrorClass.CANCELLED, "DEADLINE_EXCEEDED"),
    "APPROVAL_EXPIRED": (ErrorClass.CANCELLED, "APPROVAL_EXPIRED"),
    "POLICY_DENY": (ErrorClass.CANCELLED, "POLICY_DENY"),
}


@dataclass(frozen=True, slots=True)
class ProviderError(Exception):
    """Ошибка, приведённая к контракту. Поля — из `provider_error.schema.json`."""

    error_class: ErrorClass
    retryable: bool
    safe_to_retry_external: bool
    provider_code: str | None = None
    provider_request_id: str | None = None
    retry_after_seconds: float | None = None
    user_action: str | None = None
    safe_detail: str | None = None

    @classmethod
    def of(cls, kind: ErrorClass | str, **overrides: Any) -> "ProviderError":
        """Собрать ошибку с безопасными значениями флагов по умолчанию."""
        detail = overrides.pop("safe_detail", None)
        if isinstance(kind, str) and kind in ALIASES:
            mapped, alias = ALIASES[kind]
            kind, detail = mapped, detail or alias
        error_class = kind if isinstance(kind, ErrorClass) else ErrorClass(str(kind))
        retryable, external = _DEFAULTS[error_class]
        return cls(error_class=error_class,
                   retryable=bool(overrides.pop("retryable", retryable)),
                   safe_to_retry_external=bool(
                       overrides.pop("safe_to_retry_external", external)),
                   safe_detail=detail, **overrides)

    def to_dict(self) -> dict[str, Any]:
        return {"class": self.error_class.value, "retryable": self.retryable,
                "safe_to_retry_external": self.safe_to_retry_external,
                "provider_code": self.provider_code,
                "provider_request_id": self.provider_request_id,
                "retry_after_seconds": self.retry_after_seconds,
                "user_action": self.user_action, "safe_detail": self.safe_detail}

    def __str__(self) -> str:
        tail = f" ({self.safe_detail})" if self.safe_detail else ""
        return f"{self.error_class.value}{tail}"


__all__ = ["ALIASES", "ErrorClass", "ProviderError"]
