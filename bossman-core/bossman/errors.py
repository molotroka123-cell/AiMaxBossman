"""Единая таксономия ошибок Bossman Core (общий шов для этапов 4–7).

Одна иерархия `BossmanError` вместо разрозненных исключений: каждая ошибка несёт
машинный `code`, HTTP-статус и флаг `retryable`, чтобы runner, gateway и API
отвечали единообразно. Существующие исключения ядра (CloudDenied,
NeedsCloudApproval из llm.py; RouteNotFound, BackendError из gateway; облачные
отказы) не переписываются — они складываются в этот словарь на границе FastAPI
через `install_error_handlers`, поэтому старый оттестированный путь облачной
политики не ломается, а новые подсистемы (Resource Brain, Search, Remote,
Video) бросают уже `BossmanError`.

Инвариант: сообщение ошибки безопасно для клиента — сюда нельзя класть сырые
ключи/токены. Секреты вычищает слой obs.py на записи в лог, но и здесь мы не
формируем detail из заголовков авторизации.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"      # нет RAM/диска/лизов — 503, retryable
    QUEUE_FULL = "QUEUE_FULL"                      # очередь подсистемы полна — 503, retryable
    AUTH_DENIED = "AUTH_DENIED"                    # нет/битый токен устройства — 401
    DEVICE_REVOKED = "DEVICE_REVOKED"             # устройство отозвано/lock — 403
    SCOPE_DENIED = "SCOPE_DENIED"                  # у устройства нет права на действие — 403
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"        # модель не поднята/перегружена — 503, retryable
    SEARCH_FAILED = "SEARCH_FAILED"                # ошибка поискового плана — 500
    VIDEO_PROVIDER_FAILED = "VIDEO_PROVIDER_FAILED"  # провайдер видео упал — 502, retryable
    VIDEO_INVALID_OUTPUT = "VIDEO_INVALID_OUTPUT"    # результат не прошёл проверку — 502
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"        # действие припарковано до подтверждения — 202
    POLICY_DENIED = "POLICY_DENIED"                # запрещено политикой (cloud/ domain) — 403
    NOT_FOUND = "NOT_FOUND"                         # 404
    CONFLICT = "CONFLICT"                           # 409
    INTERNAL = "INTERNAL"                           # 500


_HTTP: dict[ErrorCode, int] = {
    ErrorCode.RESOURCE_EXHAUSTED: 503,
    ErrorCode.QUEUE_FULL: 503,
    ErrorCode.AUTH_DENIED: 401,
    ErrorCode.DEVICE_REVOKED: 403,
    ErrorCode.SCOPE_DENIED: 403,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.SEARCH_FAILED: 500,
    ErrorCode.VIDEO_PROVIDER_FAILED: 502,
    ErrorCode.VIDEO_INVALID_OUTPUT: 502,
    ErrorCode.APPROVAL_REQUIRED: 202,
    ErrorCode.POLICY_DENIED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.INTERNAL: 500,
}

_RETRYABLE: set[ErrorCode] = {
    ErrorCode.RESOURCE_EXHAUSTED,
    ErrorCode.QUEUE_FULL,
    ErrorCode.MODEL_UNAVAILABLE,
    ErrorCode.VIDEO_PROVIDER_FAILED,
}


class BossmanError(Exception):
    """Базовая ошибка домена. code → http/retryable берутся из таблиц выше,
    но их можно переопределить явно."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(
        self,
        detail: str = "",
        *,
        code: ErrorCode | None = None,
        http: int | None = None,
        retryable: bool | None = None,
        cid: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if code is not None:
            self.code = code
        self.detail = detail or self.code.value
        self.http = http if http is not None else _HTTP.get(self.code, 500)
        self.retryable = retryable if retryable is not None else (self.code in _RETRYABLE)
        self.cid = cid
        self.extra = dict(extra or {})
        super().__init__(f"{self.code.value}: {self.detail}")

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code.value,
            "message": self.detail,
            "retryable": self.retryable,
        }
        if self.cid:
            body["cid"] = self.cid
        if self.extra:
            body["extra"] = self.extra
        return {"error": body}


# --- конкретные подклассы: фиксируют code, чтобы raise был лаконичным ---

class ResourceExhausted(BossmanError):
    code = ErrorCode.RESOURCE_EXHAUSTED


class QueueFull(BossmanError):
    code = ErrorCode.QUEUE_FULL


class AuthDenied(BossmanError):
    code = ErrorCode.AUTH_DENIED


class DeviceRevoked(BossmanError):
    code = ErrorCode.DEVICE_REVOKED


class ScopeDenied(BossmanError):
    code = ErrorCode.SCOPE_DENIED


class ModelUnavailable(BossmanError):
    code = ErrorCode.MODEL_UNAVAILABLE


class SearchFailed(BossmanError):
    code = ErrorCode.SEARCH_FAILED


class VideoProviderFailed(BossmanError):
    code = ErrorCode.VIDEO_PROVIDER_FAILED


class VideoInvalidOutput(BossmanError):
    code = ErrorCode.VIDEO_INVALID_OUTPUT


class ApprovalRequired(BossmanError):
    code = ErrorCode.APPROVAL_REQUIRED


class PolicyDenied(BossmanError):
    code = ErrorCode.POLICY_DENIED


class NotFound(BossmanError):
    code = ErrorCode.NOT_FOUND


class Conflict(BossmanError):
    code = ErrorCode.CONFLICT


# --- складывание существующих исключений ядра под единую крышу ---

def _map_legacy(exc: Exception) -> BossmanError | None:
    """Существующие исключения ядра → BossmanError. Возвращает None, если exc
    неизвестен (тогда FastAPI отдаст обычную 500). Импорты — ленивые, чтобы
    errors.py не тянул llm/gateway при простом импорте таксономии."""
    name = type(exc).__name__
    if name == "CloudDenied":
        return PolicyDenied(str(exc) or "cloud egress denied by policy", code=ErrorCode.POLICY_DENIED)
    if name == "NeedsCloudApproval":
        return ApprovalRequired(str(exc) or "cloud call requires approval", code=ErrorCode.APPROVAL_REQUIRED)
    if name == "GatewayCloudDenied":
        return PolicyDenied(str(exc) or "cloud egress denied by gateway", code=ErrorCode.POLICY_DENIED)
    if name in ("RouteNotFound", "CloudPolicyDenied"):
        if name == "CloudPolicyDenied":
            return PolicyDenied(str(exc) or "no non-cloud route allowed", code=ErrorCode.POLICY_DENIED)
        return ModelUnavailable(str(exc) or "no route for alias", code=ErrorCode.MODEL_UNAVAILABLE)
    if name == "BackendError":
        return ModelUnavailable(str(exc) or "backend error", code=ErrorCode.MODEL_UNAVAILABLE)
    if name == "ProviderPolicyError":
        return PolicyDenied(str(exc) or "provider blocked by policy", code=ErrorCode.POLICY_DENIED)
    return None


def install_error_handlers(app: Any) -> None:
    """Навесить на FastAPI-приложение единый рендер ошибок: BossmanError и
    складываемые легаси-исключения → {"error": {...}} с правильным статусом."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    from . import correlation

    @app.exception_handler(BossmanError)
    async def _bossman_handler(request: "Request", exc: BossmanError):  # noqa: ANN001
        if exc.cid is None:
            exc.cid = correlation.current().get("request_id")
        return JSONResponse(status_code=exc.http, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def _legacy_handler(request: "Request", exc: Exception):  # noqa: ANN001
        mapped = _map_legacy(exc)
        if mapped is None:
            raise exc  # пусть FastAPI отдаст стандартную 500 (и залогирует трейс)
        mapped.cid = correlation.current().get("request_id")
        return JSONResponse(status_code=mapped.http, content=mapped.to_dict())
