"""Контракт адаптера провайдера.

Одно правило определяет всю форму этого файла: **адаптер не решает
AUTO/ASK/DENY**. Он умеет говорить с провайдером и рассказывать, что тот
ответил. Решает политика — выше, зная аккаунт, действие и контекст. Адаптер,
который сам решил «это безопасно», забирает решение у владельца и прячет его в
коде провайдера, где его никто не найдёт.

Второе правило, из которого следует форма результата: **адаптер обязан уметь
сказать «не знаю»**. Соединение, оборвавшееся после отправки запроса на
публикацию, — это не отказ и не успех. Ответ `UNKNOWN_EXTERNAL_STATE` с
`safe_to_retry_external=False` здесь единственный честный, и он должен быть так
же легко выразим, как успех, иначе его никто не вернёт.

BOSSMAN и интерфейс не знают ни одного endpoint'а, скоупа или селектора
конкретной площадки — всё это остаётся за адаптером.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ...domain.capability import CapabilitySnapshot
from ...domain.errors import ErrorClass, ProviderError


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Что провайдер сообщил о лимитах. Пустое — «не сообщил», а не «их нет»."""

    bucket: str = ""
    limit: int | None = None
    remaining: int | None = None
    reset_at: str | None = None
    retry_after_seconds: float | None = None

    @property
    def exhausted(self) -> bool:
        return self.remaining is not None and self.remaining <= 0


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """Результат одной операции адаптера.

    `raw_payload_ref` — ССЫЛКА на сырой ответ в хранилище, а не сам ответ:
    сырые ответы провайдера содержат идентификаторы людей и текст переписки, и
    протаскивать их через домен и логи не нужно.
    """

    ok: bool
    normalized: dict[str, Any] = field(default_factory=dict)
    provider_request_id: str | None = None
    raw_payload_ref: str | None = None
    rate_limit: RateLimit = field(default_factory=RateLimit)
    error: ProviderError | None = None
    # Идентификатор объекта, созданного у провайдера. Заполняется ТОЛЬКО когда
    # факт создания подтверждён: пустое поле при ok=False не означает, что
    # ничего не создалось.
    provider_object_id: str | None = None

    @classmethod
    def success(cls, normalized: dict[str, Any] | None = None, **kw: Any) -> "AdapterResult":
        return cls(ok=True, normalized=dict(normalized or {}), **kw)

    @classmethod
    def failure(cls, error: ProviderError, **kw: Any) -> "AdapterResult":
        return cls(ok=False, error=error, **kw)

    @classmethod
    def unknown_state(cls, detail: str, **kw: Any) -> "AdapterResult":
        """Запрос мог дойти. Это отдельный исход, а не разновидность отказа."""
        return cls(ok=False, error=ProviderError.of(
            ErrorClass.UNKNOWN_EXTERNAL_STATE, safe_detail=detail,
            user_action="Сверьте состояние у провайдера перед любым повтором."), **kw)

    @property
    def safe_to_retry_external(self) -> bool:
        """Можно ли повторить ВНЕШНИЙ эффект. По умолчанию — нет.

        Умолчание выбрано так, чтобы забытая обработка ошибки приводила к
        остановке, а не ко второй публикации.
        """
        if self.ok:
            return False
        return bool(self.error and self.error.safe_to_retry_external)


@dataclass(frozen=True, slots=True)
class AdapterMetadata:
    id: str
    version: str
    provider: str
    auth_schemes: tuple[str, ...] = ()


@runtime_checkable
class SocialProviderAdapter(Protocol):
    """Нормализованные операции. Имена — из `provider_adapter.yaml`.

    Адаптер, который какую-то операцию не умеет, возвращает
    `CAPABILITY_UNAVAILABLE`, а не бросает `NotImplementedError`: разница между
    «эта площадка так не умеет» и «мы это не написали» видна владельцу и должна
    доходить до него неискажённой.
    """

    metadata: AdapterMetadata

    async def connect(self, account_ref: str) -> AdapterResult: ...
    async def disconnect(self, account_ref: str) -> AdapterResult: ...
    async def refresh_auth(self, account_ref: str) -> AdapterResult: ...
    async def capabilities(self, account_ref: str) -> CapabilitySnapshot: ...
    async def health(self) -> AdapterResult: ...

    async def list_media(self, account_ref: str, cursor: str = "") -> AdapterResult: ...
    async def get_media(self, account_ref: str, media_id: str) -> AdapterResult: ...
    async def publish(self, account_ref: str, payload: dict[str, Any], *,
                      idempotency_key: str) -> AdapterResult: ...
    async def get_publish_status(self, account_ref: str,
                                 container_id: str) -> AdapterResult: ...
    async def delete_media(self, account_ref: str, media_id: str, *,
                           idempotency_key: str) -> AdapterResult: ...
    async def archive_media(self, account_ref: str, media_id: str, *,
                            idempotency_key: str) -> AdapterResult: ...

    async def list_comments(self, account_ref: str, media_id: str,
                            cursor: str = "") -> AdapterResult: ...
    async def reply_comment(self, account_ref: str, comment_id: str, text: str, *,
                            idempotency_key: str) -> AdapterResult: ...
    async def moderate_comment(self, account_ref: str, comment_id: str, action: str, *,
                               idempotency_key: str) -> AdapterResult: ...

    async def list_threads(self, account_ref: str, cursor: str = "") -> AdapterResult: ...
    async def list_messages(self, account_ref: str, thread_id: str,
                            cursor: str = "") -> AdapterResult: ...
    async def reply_message(self, account_ref: str, thread_id: str, text: str, *,
                            idempotency_key: str) -> AdapterResult: ...

    async def get_account_insights(self, account_ref: str,
                                   metrics: list[str]) -> AdapterResult: ...
    async def get_media_insights(self, account_ref: str, media_id: str,
                                 metrics: list[str]) -> AdapterResult: ...

    async def subscribe_webhooks(self, account_ref: str) -> AdapterResult: ...
    async def reconcile(self, account_ref: str, cursor: str) -> AdapterResult: ...


# Операции, меняющие внешний мир. Каждая обязана принимать ключ идемпотентности —
# это проверяется тестом, а не соблюдается на память.
MUTATING_OPERATIONS = frozenset({
    "publish", "delete_media", "archive_media", "reply_comment", "moderate_comment",
    "reply_message",
})

# Операции, которых достаточно для чтения. Их отсутствие делает аккаунт
# бесполезным, но не опасным.
READ_OPERATIONS = frozenset({
    "list_media", "get_media", "get_publish_status", "list_comments", "list_threads",
    "list_messages", "get_account_insights", "get_media_insights", "reconcile",
})

ALL_OPERATIONS = MUTATING_OPERATIONS | READ_OPERATIONS | frozenset({
    "connect", "disconnect", "refresh_auth", "capabilities", "health",
    "subscribe_webhooks",
})


def unsupported(operation: str, provider: str) -> AdapterResult:
    """Единый способ сказать «эта площадка так не умеет»."""
    return AdapterResult.failure(ProviderError.of(
        ErrorClass.CAPABILITY_UNAVAILABLE,
        safe_detail=f"{provider}: операция {operation} не поддерживается адаптером",
        user_action="Проверьте матрицу возможностей аккаунта."))


__all__ = ["ALL_OPERATIONS", "AdapterMetadata", "AdapterResult", "MUTATING_OPERATIONS",
           "READ_OPERATIONS", "RateLimit", "SocialProviderAdapter", "unsupported"]
