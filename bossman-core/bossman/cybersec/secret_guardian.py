"""Secret Guardian — секреты не утекают ни в контекст, ни в логи, ни наружу.

ВАЖНО: собственного скраббера здесь НЕТ. Канонический редактор — `bossman.obs`
(`redact`/`redact_obj`), он уже используется логированием. Второй редактор =
второй источник правды о том, что считается секретом. Guardian лишь добавляет
то, чего в obs нет: обнаружение ЗАПРОСА на эксфильтрацию и проверку исходящих
полезных нагрузок.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..obs import redact, redact_obj

__all__ = ["redact", "redact_obj", "ExfilVerdict", "detect_exfil_request",
           "assert_no_secret_egress", "SecretEgressBlocked"]


class SecretEgressBlocked(PermissionError):
    """Исходящая нагрузка содержала секрет — отправка запрещена."""


@dataclass(frozen=True)
class ExfilVerdict:
    is_request: bool
    reason: str = ""


# `\b` перед `\.env` НИКОГДА не совпадает (пробел->точка не граница слова),
# поэтому секретные пути с ведущей точкой выделены в отдельную ветку.
_SECRET_NOUN = (r"(?:\b(?:api[_\s-]?key|secret|secrets|token|password|credential|credentials|"
                r"private\s+key|id_rsa|vault)\b|(?<![\w.])\.(?:env|npmrc|netrc|pgpass)\b)")

_EXFIL = re.compile(
    r"\b(?:send|post|upload|exfiltrate|leak|email|transmit|curl|webhook)\b[^.\n]{0,40}" + _SECRET_NOUN
    + r"|\b(?:cat|type|print|show|reveal|dump|read|open)\b[^.\n]{0,20}" + _SECRET_NOUN,
    re.I)


def detect_exfil_request(text: str) -> ExfilVerdict:
    """Обнаружить ПРОСЬБУ вынести секрет наружу (не сам секрет)."""
    if not text:
        return ExfilVerdict(False)
    m = _EXFIL.search(text)
    return ExfilVerdict(bool(m), m.group(0)[:120] if m else "")


def _contains_secret(payload: str) -> bool:
    """Секрет считается найденным, если канонический redact что-то изменил."""
    return bool(payload) and redact(payload) != payload


def assert_no_secret_egress(payload: object, *, destination: str = "external") -> None:
    """Fail-closed проверка ПЕРЕД отправкой наружу.

    Бросает SecretEgressBlocked, если канонический редактор находит секрет.
    Само значение секрета в исключение НЕ попадает.
    """
    text = payload if isinstance(payload, str) else repr(redact_obj(payload))
    if _contains_secret(text if isinstance(payload, str) else str(payload)):
        raise SecretEgressBlocked(
            f"secret detected in payload destined for {destination!r}; egress blocked")
