"""Секреты браузерного резерва: ссылка вместо значения и три линии редакции.

Правило приложения: **секрет не передаётся аргументом**. В браузере это правило
имеет буквальный смысл — значение пароля не должно оказаться ни в аргументах
инструмента, ни в снимке страницы, ни в трассе, ни в аудите, ни в тексте
ошибки. Поэтому здесь три независимые линии, и каждая рассчитана на то, что
предыдущая не сработала:

1. **Значение не появляется.** Секретное поле отдаётся в снимок без значения —
   не «замазанным», а без него: считывать его незачем ни в одном сценарии.
   Что считается секретным полем, решает `browser/dom.py`, и это не только
   `type=password`: скрытые поля, коды из SMS и пароли в текстовых полях тоже.
2. **Ввод по ссылке.** `fill_secret` принимает `SecretRef`, а не строку.
   Значение достаётся из хранилища внутри воркера и живёт ровно до подстановки.
3. **Редакция по значению и по имени поля.** Всё, что уходит наружу, проходит
   через `Redactor`: он вычищает известные значения секретов из строк и
   вычищает поля с «секретными» именами, даже если значение нам неизвестно —
   cookie и токен могли попасть в структуру не через нас.

Третья линия существует потому, что первые две можно обойти по неосторожности,
и она рассчитана на то, что строка по дороге изменилась: секрет ищется в тех
формах, в которых он реально доезжает наружу (схлопнутые пробелы, нижний
регистр, percent-кодирование, HTML-экранирование), и отдельно — обрезанным.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable
from urllib.parse import quote, quote_plus

MASK = "***"

# Короче этого куска совпадение уликой не считается: вычёркивание восьми
# символов изуродовало бы страницу, а пароль «parol123» и так не пароль.
MIN_PARTIAL_LEN = 12

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


def secret_variants(secret: str) -> list[str]:
    """Формы, в которых секрет РЕАЛЬНО встречается в тексте, уходящем наружу.

    Точное совпадение — наивное допущение: по дороге строка успевает
    измениться, оставаясь тем же секретом. Каждая форма ниже — не выдумка, а
    место в этом же приложении, где строка меняется:

    * схлопнутые пробелы — `clean()` в `PAGE_SCRIPT` и `_norm` в `dom.py`;
    * нижний регистр — `normalize_text` в `fingerprint.py`, через который
      проходит `semantic_identity`, а она идёт в аудит и в текст ошибки;
    * percent-кодирование — адрес страницы после отправки формы `method=GET`;
    * HTML-экранирование — разметка страницы.

    Обрезка сюда не входит: она обрабатывается отдельно, поиском самого
    длинного присутствующего префикса (`_mask_variant`).
    """
    out: list[str] = [secret]
    collapsed = re.sub(r"\s+", " ", secret).strip()
    for value in (collapsed, secret.strip(), secret.casefold(), collapsed.casefold(),
                  quote(secret, safe=""), quote_plus(secret), _html.escape(secret)):
        if value and value not in out:
            out.append(value)
    # Длинные варианты маскируются первыми: короткий иначе съел бы хвост длинного.
    return sorted(out, key=len, reverse=True)


def _mask_variant(text: str, variant: str) -> str:
    """Вычеркнуть вариант секрета, включая его ОБРЕЗАННЫЙ вид.

    Обрезка встречается на каждом шагу: `snapshot_max_text` в снимке, 220
    символов в описании элемента, `_TEXT_LIMIT` в отпечатке. Обрезанный пароль
    остаётся паролем — искать надо самый длинный присутствующий префикс, а не
    строку целиком.
    """
    if not variant:
        return text
    if variant in text:
        text = text.replace(variant, MASK)
    if len(variant) < MIN_PARTIAL_LEN:
        return text
    while True:
        low, high, best = MIN_PARTIAL_LEN, len(variant) - 1, 0
        while low <= high:            # «префикс присутствует» монотонно по длине
            middle = (low + high) // 2
            if variant[:middle] in text:
                best, low = middle, middle + 1
            else:
                high = middle - 1
        if not best:
            return text
        # MASK короче MIN_PARTIAL_LEN, поэтому замена не порождает новых совпадений.
        text = text.replace(variant[:best], MASK)


class Redactor:
    """Единственная дверь, через которую данные страницы уходят наружу.

    Хранит значения секретов, которые рантайм сам подставлял в страницу. Наружу
    их не отдаёт — они нужны только чтобы вычеркнуть их из всего остального.
    """

    __slots__ = ("_values", "_variants")

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._values: set[str] = set()
        self._variants: tuple[str, ...] = ()
        for value in values:
            self.remember(value)

    def remember(self, value: str) -> None:
        """Запомнить значение, которое мы подставили в страницу.

        Очень короткие значения не запоминаются: вычёркивание строки из четырёх
        символов изуродовало бы весь остальной текст и спрятало бы от владельца
        то, ради чего он смотрит на снимок.
        """
        if value and len(value) >= 4 and value not in self._values:
            self._values.add(value)
            # Варианты считаются один раз на секрет, а не на каждую строку:
            # снимок — это сотни полей.
            self._variants = tuple(sorted(
                {v for secret in self._values for v in secret_variants(secret)},
                key=len, reverse=True))

    @property
    def count(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Redactor(known={self.count})"

    def text(self, value: str) -> str:
        for variant in self._variants:
            value = _mask_variant(value, variant)
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
                if (isinstance(key, str) and looks_like_secret_name(key)
                        and not isinstance(item, bool)):
                    # Имя поля говорит, что там секрет. Значение не смотрим:
                    # оно может быть чужим и нам неизвестным.
                    #
                    # Исключение — `bool`, и оно не про удобство. В снимке есть
                    # поле `secret: true|false`: это ПРИЗНАК, что значение
                    # спрятано, а не само значение. Замазав его, редакция
                    # превращала «обычная кнопка» в «здесь что-то скрыто» и
                    # лгала о каждом элементе страницы. Один бит секрета не
                    # несёт.
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


__all__ = ["MASK", "MIN_PARTIAL_LEN", "SECRET_FIELD_NAMES", "MappingSecretResolver",
           "Redactor", "SecretNotFound", "SecretRef", "SecretResolver",
           "looks_like_secret_name", "redact_secrets", "secret_variants"]
