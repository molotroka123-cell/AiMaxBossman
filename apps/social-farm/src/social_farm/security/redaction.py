"""Редакция: единственная дверь, через которую данные выходят в лог и аудит.

Правило простое и неудобное: **наружу идёт ссылка на секрет, а не значение**.
Но правило, которое соблюдается вниманием, рано или поздно не соблюдается.
Поэтому здесь стоит вторая линия — редактор, через который проходит всё, что
пишется в структурный лог, в `redacted_detail_json` аудита и в представление
аккаунта наружу.

Редактор работает по двум признакам сразу, и это не избыточность:

* **по имени поля** — `access_token`, `password`, `cookie` и родня. Ловит
  случай, когда сырой ответ провайдера целиком отправили в лог;
* **по известному значению** — вызывающий передаёт значения, которые в этом
  месте держит в руках. Ловит случай, когда токен приехал под безобидным
  именем (`value`, `data`, третий элемент кортежа).

Ни один из признаков сам по себе не полон, и это сказано прямо: имя поля не
знает про `{"v": "<токен>"}`, а список значений не знает про токен, который мы
никогда не видели. Вместе они закрывают то, что реально случается.

Канареечный тест (`tests/unit/test_secret_canary.py`) прогоняет известную
строку через все выходы разом и падает, если она где-то всплыла.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

MASK = "***REDACTED***"

# Имена полей, значение которых наружу не идёт никогда. Сравнение
# по вхождению и без регистра: `ig_access_token`, `X-Access-Token`, `AccessToken`
# — одно и то же поле с разных сторон.
SECRET_FIELD_HINTS: frozenset[str] = frozenset({
    "access_token", "accesstoken", "refresh_token", "refreshtoken", "id_token",
    "token", "password", "passwd", "secret", "client_secret", "app_secret",
    "api_key", "apikey", "private_key", "authorization", "auth_header",
    "cookie", "cookies", "session", "session_id", "sessionid", "set-cookie",
    "credential", "credentials", "otp", "totp", "mfa_code", "signature",
    "appsecret_proof", "code_verifier", "state_nonce", "csrf",
})

# Поля, которые ВЫГЛЯДЯТ секретными, но ими не являются и нужны для разбора
# инцидента. Ссылка на секрет — это и есть то, что должно остаться в аудите:
# по ней видно, какой именно секрет использовался, и не видно его значения.
SAFE_FIELD_EXCEPTIONS: frozenset[str] = frozenset({
    "auth_ref", "secret_ref", "token_ref", "browser_session_ref",
    "token_fingerprint", "secret_fingerprint", "token_type", "token_state",
    "webhook_subscription_state", "raw_payload_ref",
})

# Значение, похожее на длинный непрозрачный токен. Признак слабый и намеренно
# узкий: широкое правило вычистило бы идентификаторы медиа и сделало бы аудит
# бесполезным, а бесполезный аудит не читают.
_TOKEN_SHAPED = re.compile(r"^(?:EAA|IGQ|Bearer\s+)\S{20,}$")


def _is_secret_name(name: str) -> bool:
    lowered = str(name).lower()
    if lowered in SAFE_FIELD_EXCEPTIONS:
        return False
    return any(hint in lowered for hint in SECRET_FIELD_HINTS)


def _scrub_text(text: str, known: tuple[str, ...]) -> str:
    result = text
    for value in known:
        if value and value in result:
            result = result.replace(value, MASK)
    if _TOKEN_SHAPED.match(result.strip()):
        return MASK
    return result


def redact(payload: Any, *, known_values: Iterable[str] = (), _depth: int = 0) -> Any:
    """Рекурсивно вычистить секреты. Структура сохраняется, значения — нет.

    Глубина ограничена: аудит с деревом на сто уровней — это не аудит, а способ
    положить сериализатор.
    """
    known = tuple(v for v in known_values if v)
    if _depth > 12:
        return "***DEPTH_LIMIT***"
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if _is_secret_name(key):
                out[str(key)] = MASK
            else:
                out[str(key)] = redact(value, known_values=known, _depth=_depth + 1)
        return out
    if isinstance(payload, (list, tuple, set)):
        return [redact(item, known_values=known, _depth=_depth + 1)
                for item in payload]
    if isinstance(payload, str):
        return _scrub_text(payload, known)
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    # Неизвестный тип: строковое представление, тоже вычищенное. Объект
    # `SecretValue` здесь безопасен — его `__repr__` уже маска.
    return _scrub_text(repr(payload), known)


def safe_log_record(event: str, **fields: Any) -> dict[str, Any]:
    """Запись структурного лога. Другого способа писать лог у адаптера нет.

    Инвариант S5 («ни одного секрета в структурных логах») держится не тем, что
    все помнят про редакцию, а тем, что запись строится здесь.
    """
    known = tuple(fields.pop("_known_values", ()) or ())
    return {"event": str(event),
            "at": datetime.now(timezone.utc).isoformat(),
            **redact(fields, known_values=known)}


def audit_detail(*, action: str, account_id: str, capability: str = "",
                 outcome: str = "", detail: dict[str, Any] | None = None,
                 known_values: Iterable[str] = ()) -> dict[str, Any]:
    """`redacted_detail_json` для события аудита.

    Имя поля в схеме — `redacted_detail_json`, и оно не случайно: аудит хранит
    вычищенную подробность, а не сырой ответ провайдера. Сырой ответ живёт в
    отдельном хранилище, и в аудит идёт ссылка на него (`raw_payload_ref`).
    """
    body = redact(dict(detail or {}), known_values=known_values)
    body.update({"action": str(action), "account_id": str(account_id),
                 "capability": str(capability), "outcome": str(outcome)})
    return body


def assert_no_secret(payload: Any, *values: str) -> None:
    """Проверка «этой строки здесь нет». Используется тестами и сборкой.

    Функция живёт в рабочем коде, а не в тестах, чтобы её можно было позвать
    и на границе — например, перед записью события в append-only журнал.
    """
    import json as _json

    text = _json.dumps(payload, ensure_ascii=False, default=str)
    leaked = [v for v in values if v and v in text]
    if leaked:
        raise AssertionError(
            f"секрет попал в сериализацию ({len(leaked)} шт.); "
            f"отпечатки: {[v[:4] + '…' for v in leaked]}")


__all__ = ["MASK", "SAFE_FIELD_EXCEPTIONS", "SECRET_FIELD_HINTS", "assert_no_secret",
           "audit_detail", "redact", "safe_log_record"]
