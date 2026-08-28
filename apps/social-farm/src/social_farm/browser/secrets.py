"""Секреты браузерного резерва: ссылка вместо значения и три линии редакции.

Правило приложения: **секрет не передаётся аргументом**. В браузере это правило
имеет буквальный смысл — значение пароля не должно оказаться ни в аргументах
инструмента, ни в снимке страницы, ни в трассе, ни в аудите, ни в тексте
ошибки. Поэтому здесь три независимые линии, и каждая рассчитана на то, что
предыдущая не сработала:

1. **Значение не появляется.** Поле `type=password` отдаётся в снимок без
   значения — не «замазанным», а без него: считывать его незачем ни в одном
   сценарии (`browser/dom.py`).
2. **Ввод по ссылке.** `fill_secret` принимает `SecretRef`, а не строку.
   Значение достаётся из хранилища внутри воркера и живёт ровно до подстановки.
3. **Редакция по значению и по имени поля.** Всё, что уходит наружу, проходит
   через `Redactor`: он вычищает известные значения секретов из строк и
   вычищает поля с «секретными» именами, даже если значение нам неизвестно —
   cookie и токен могли попасть в структуру не через нас.

Третья линия существует потому, что первые две можно обойти по неосторожности:
пароль, введённый в поле `type=text`, вернулся бы обычным значением.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable

MASK = "***"

# Имена полей, значение которых не выводится никогда — даже если мы не знаем,
# что именно там лежит. Перечень из `32_BROWSER_SECURITY`: пароли, токены,
# cookie, значения из session/local storage, коды восстановления.
SECRET_FIELD_NAMES = frozenset({
    "password", "passwd", "pass", "pwd", "secret", "token", "access_token",
    "refresh_token", "id_token", "api_key", "apikey", "authorization", "auth",
    "cookie", "cookies", "set-cookie", "set_cookie", "session", "session_id",
    "sessionid", "csrftoken", "csrf_token", "recovery_code", "recovery_codes",
    "backup_code", "backup_codes", "otp", "totp", "one_time_code", "mfa_code",
    "local_storage", "session_storage", "credential", "credentials",
})

_SECRET_NAME_HINT = re.compile(
    r"(?i)(pass(word|wd)?|secret|token|cookie|api[-_]?key|authorization|"
    r"credential|recovery[-_]?code|backup[-_]?code|otp|csrf)")


class SecretNotFound(LookupError):
    """Ссылка на секрет никуда не ведёт. Это отказ, а не пустая строка."""


@dataclass(frozen=True, slots=True)
class SecretRef:
    """Ссылка на секрет. Значения не содержит и содержать не может.

    Именно это ходит в аргументах операций, пишется в аудит и пересекает
    границу процесса воркера. Значение остаётся в хранилище.
    """

    ref: str

    def __post_init__(self) -> None:
        if not self.ref or not self.ref.strip():
            raise ValueError("ссылка на секрет пуста")

    def __str__(self) -> str:
        return self.ref

    def to_dict(self) -> dict[str, Any]:
        return {"secret_ref": self.ref}


@runtime_checkable
class SecretResolver(Protocol):
    """Хранилище, умеющее превратить ссылку в значение. Один-единственный вызов
    во всём браузерном резерве, и он происходит внутри воркера."""

    def resolve(self, ref: SecretRef) -> str: ...


@dataclass(slots=True)
class MappingSecretResolver:
    """Резолвер поверх обычного словаря. Для тестов и для локального запуска.

    Значения не логируются и не выводятся в `repr`: дата-класс со `slots` и
    собственным `__repr__` не покажет содержимое, если объект попадёт в трассу.
    """

    values: dict[str, str] = field(default_factory=dict)

    def resolve(self, ref: SecretRef) -> str:
        try:
            return self.values[ref.ref]
        except KeyError as exc:
            raise SecretNotFound(f"секрет по ссылке {ref.ref} не найден") from exc

    def __repr__(self) -> str:
        return f"MappingSecretResolver(refs={sorted(self.values)!r})"


def looks_like_secret_name(name: str) -> bool:
    key = str(name).strip().lower().replace("-", "_")
    return key in SECRET_FIELD_NAMES or bool(_SECRET_NAME_HINT.search(key))


class Redactor:
    """Единственная дверь, через которую данные страницы уходят наружу.

    Хранит значения секретов, которые рантайм сам подставлял в страницу. Наружу
    их не отдаёт — они нужны только чтобы вычеркнуть их из всего остального.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._values: set[str] = set()
        for value in values:
            self.remember(value)

    def remember(self, value: str) -> None:
        """Запомнить значение, которое мы подставили в страницу.

        Очень короткие значения не запоминаются: вычёркивание строки из двух
        символов изуродовало бы весь остальной текст и спрятало бы от владельца
        то, ради чего он смотрит на снимок.
        """
        if value and len(value) >= 4:
            self._values.add(value)

    @property
    def count(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Redactor(known={self.count})"

    def text(self, value: str) -> str:
        for secret in self._values:
            if secret in value:
                value = value.replace(secret, MASK)
        return value

    def __call__(self, value: Any) -> Any:
        return self.scrub(value)

    def scrub(self, value: Any) -> Any:
        """Рекурсивно вычистить значение перед выдачей наружу."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for key, item in value.items():
                if isinstance(key, str) and looks_like_secret_name(key):
                    # Имя поля говорит, что там секрет. Значение не смотрим:
                    # оно может быть чужим и нам неизвестным.
                    out[key] = MASK if item not in (None, "", [], {}) else item
                else:
                    out[key] = self.scrub(item)
            return out
        if isinstance(value, (list, tuple)):
            return [self.scrub(item) for item in value]
        return value


def redact_secrets(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Разовая редакция без хранения состояния."""
    return Redactor(secrets).scrub(value)


__all__ = ["MASK", "SECRET_FIELD_NAMES", "MappingSecretResolver", "Redactor",
           "SecretNotFound", "SecretRef", "SecretResolver", "looks_like_secret_name",
           "redact_secrets"]
