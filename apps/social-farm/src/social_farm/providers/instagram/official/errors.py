"""Перевод ошибок провайдера в закрытый перечень домена.

Правила из `56_PROVIDER_ERROR_TRANSLATION` реализованы буквально, но источники
у них разные, и это различие определяет всю форму файла:

* **коды провайдера** — факт версии. Их здесь нет: карта кодов живёт в профиле
  провайдера (`error_codes`), а поставляемый профиль её не заполняет, потому
  что сверить с документацией Meta было не с чем;
* **коды состояния HTTP** — не факт Meta, а RFC 9110. 401, 403, 429, 5xx
  значат одно и то же у любого провайдера, и по ним классификация работает
  даже на пустой карте кодов.

Поэтому адаптер без сверенного профиля всё равно переводит ошибки правильно в
основных случаях, а с заполненной картой — точнее.

## Самое важное правило файла

**Ответа нет — состояние неизвестно, а не «не дошло».**

Обрыв или таймаут ПОСЛЕ отправки изменяющего запроса даёт
`UNKNOWN_EXTERNAL_STATE` с `safe_to_retry_external=False`. Туда же уходит 5xx
на изменяющей операции: провайдер ответил, но что он успел сделать до отказа —
неизвестно. Спека допускает здесь `TRANSIENT_PROVIDER` («5xx before known
external effect»), и для чтения это верно; для мутации «before» доказать
нечем. Выбор в пользу сверки сделан осознанно: повторная публикация не
чинится откатом, а лишняя сверка стоит одного запроса.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ....domain.errors import ErrorClass, ProviderError
from .profile import ProviderProfile
from .transport import (GraphResponse, RequestNotSent, ResponseUnknown,
                        TransportError, TransportUnavailable)

# Классификация по коду состояния HTTP. Работает без карты кодов провайдера.
_BY_STATUS: dict[int, ErrorClass] = {
    400: ErrorClass.PERMANENT_PROVIDER,
    401: ErrorClass.AUTH_EXPIRED,
    403: ErrorClass.PERMISSION_MISSING,
    404: ErrorClass.PERMANENT_PROVIDER,
    405: ErrorClass.CAPABILITY_UNAVAILABLE,
    408: ErrorClass.TIMEOUT,
    409: ErrorClass.PERMANENT_PROVIDER,
    413: ErrorClass.MEDIA_INVALID,
    415: ErrorClass.MEDIA_INVALID,
    422: ErrorClass.CONTENT_REJECTED,
    429: ErrorClass.RATE_LIMITED,
}

_USER_ACTION: dict[ErrorClass, str] = {
    ErrorClass.AUTH_EXPIRED: "Обновите доступ: аккаунт требует переподключения.",
    ErrorClass.AUTH_REQUIRED: "Подключите аккаунт заново — провайдер отклонил доступ.",
    ErrorClass.PERMISSION_MISSING: "Разрешение не выдано. Проверьте состав разрешений "
                                   "приложения и переподключите аккаунт.",
    ErrorClass.CAPABILITY_UNAVAILABLE: "Проверьте матрицу возможностей аккаунта.",
    ErrorClass.PROVIDER_POLICY_BLOCKED: "Провайдер запретил это действие. Обходить "
                                        "запрет приложение не будет.",
    ErrorClass.RATE_LIMITED: "Частота обращений исчерпана. Работа подождёт.",
    ErrorClass.MEDIA_INVALID: "Медиа не проходит ограничения провайдера.",
    ErrorClass.CONTENT_REJECTED: "Содержимое отклонено провайдером.",
    ErrorClass.UNKNOWN_EXTERNAL_STATE: "Сверьте состояние у провайдера перед любым "
                                       "повтором: действие могло дойти.",
    ErrorClass.TRANSIENT_PROVIDER: "Временный отказ провайдера — работа повторится.",
    ErrorClass.PERMANENT_PROVIDER: "Провайдер отказал окончательно; повтор не поможет.",
}


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """Обстоятельства вызова. Без них перевод неоднозначен.

    `mutating` здесь решает всё: один и тот же обрыв соединения для чтения —
    временный отказ, а для публикации — неизвестное внешнее состояние.
    """

    operation: str
    mutating: bool = False
    capability: str = ""
    profile: ProviderProfile | None = None


def _error_body(response: GraphResponse) -> dict[str, Any]:
    body = response.body if isinstance(response.body, dict) else {}
    error = body.get("error")
    return error if isinstance(error, dict) else {}


def _request_id(response: GraphResponse) -> str | None:
    error = _error_body(response)
    for key in ("fbtrace_id", "trace_id", "request_id"):
        if error.get(key):
            return str(error[key])
    for header in ("x-fb-trace-id", "x-fb-request-id", "x-request-id",
                   "x-fixture-request-id"):
        found = response.header(header)
        if found:
            return found
    return None


def _retry_after(response: GraphResponse, profile: ProviderProfile | None) -> float | None:
    header = (profile.rate_limit.retry_after_header if profile else "Retry-After")
    raw = response.header(header) or response.header("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _from_profile_codes(error: dict[str, Any],
                        profile: ProviderProfile | None) -> ErrorClass | None:
    """Карта кодов провайдера. Пустая в поставке — и это честно.

    Заполняется из документации Meta при сверке профиля. Пока пуста,
    классификация идёт по коду состояния HTTP, и это работает, просто грубее.
    """
    if profile is None or not profile.error_codes:
        return None
    for key in ("code", "error_subcode", "error_code", "type"):
        value = error.get(key)
        if value is None:
            continue
        mapped = profile.error_codes.get(str(value))
        if mapped:
            try:
                return ErrorClass(mapped)
            except ValueError:
                # Профиль ссылается на класс, которого нет в закрытом перечне.
                # Перечень не расширяется (`domain/errors`), профиль чинится.
                return None
    return None


def translate_response(response: GraphResponse, context: ErrorContext) -> ProviderError:
    """Ответ провайдера с ошибкой → класс домена."""
    profile = context.profile
    error = _error_body(response)
    provider_code = str(error.get("code")) if error.get("code") is not None else None
    request_id = _request_id(response)

    mapped = _from_profile_codes(error, profile)
    if mapped is None:
        mapped = _BY_STATUS.get(response.status)
    if mapped is None:
        mapped = (ErrorClass.TRANSIENT_PROVIDER if response.status >= 500
                  else ErrorClass.PERMANENT_PROVIDER)

    # 5xx на изменяющей операции: провайдер ответил отказом, но что он успел
    # сделать до него — неизвестно. Сверка дешевле второй публикации.
    if response.status >= 500 and context.mutating:
        mapped = ErrorClass.UNKNOWN_EXTERNAL_STATE
    elif response.status >= 500:
        mapped = ErrorClass.TRANSIENT_PROVIDER

    detail = str(error.get("message") or "").strip()
    safe_detail = (f"{context.operation}: {detail}" if detail
                   else f"{context.operation}: провайдер вернул {response.status}")
    return ProviderError.of(
        mapped, provider_code=provider_code, provider_request_id=request_id,
        retry_after_seconds=_retry_after(response, profile),
        user_action=_USER_ACTION.get(mapped), safe_detail=safe_detail)


def translate_exception(exc: BaseException, context: ErrorContext) -> ProviderError:
    """Исключение транспорта → класс домена.

    Здесь и живёт то самое различие, ради которого транспорт делит ошибки на
    два класса: запрос не ушёл — повторяем; ответа нет — сверяем.
    """
    if isinstance(exc, ResponseUnknown):
        if context.mutating:
            return ProviderError.of(
                ErrorClass.UNKNOWN_EXTERNAL_STATE,
                safe_detail=f"{context.operation}: {exc}",
                user_action=_USER_ACTION[ErrorClass.UNKNOWN_EXTERNAL_STATE])
        # Чтение ничего не меняло снаружи: сверять нечего, можно повторить.
        return ProviderError.of(
            ErrorClass.TRANSIENT_PROVIDER,
            safe_detail=f"{context.operation}: ответа нет, но операция читающая — "
                        f"внешнего эффекта не было ({exc})",
            user_action=_USER_ACTION[ErrorClass.TRANSIENT_PROVIDER])
    if isinstance(exc, RequestNotSent):
        return ProviderError.of(
            ErrorClass.TRANSIENT_PROVIDER,
            safe_detail=f"{context.operation}: запрос не ушёл ({exc})",
            user_action=_USER_ACTION[ErrorClass.TRANSIENT_PROVIDER])
    if isinstance(exc, TransportUnavailable):
        return ProviderError.of(
            ErrorClass.CAPABILITY_UNAVAILABLE,
            safe_detail=f"{context.operation}: {exc}",
            user_action="Заполните профиль провайдера и установите зависимости "
                        "группы `official`.")
    if isinstance(exc, TransportError):
        # Неизвестный подкласс транспортной ошибки. Мутация → сверка: умолчание
        # выбрано так, чтобы забытая ветка останавливала, а не публиковала.
        kind = (ErrorClass.UNKNOWN_EXTERNAL_STATE if context.mutating
                else ErrorClass.TRANSIENT_PROVIDER)
        return ProviderError.of(kind, safe_detail=f"{context.operation}: {exc}",
                                user_action=_USER_ACTION.get(kind))
    if isinstance(exc, TimeoutError):
        kind = (ErrorClass.UNKNOWN_EXTERNAL_STATE if context.mutating
                else ErrorClass.TIMEOUT)
        return ProviderError.of(kind, safe_detail=f"{context.operation}: таймаут",
                                user_action=_USER_ACTION.get(kind))
    kind = (ErrorClass.UNKNOWN_EXTERNAL_STATE if context.mutating
            else ErrorClass.TRANSIENT_PROVIDER)
    return ProviderError.of(
        kind, safe_detail=f"{context.operation}: {type(exc).__name__}",
        user_action=_USER_ACTION.get(kind))


__all__ = ["ErrorContext", "translate_exception", "translate_response"]
