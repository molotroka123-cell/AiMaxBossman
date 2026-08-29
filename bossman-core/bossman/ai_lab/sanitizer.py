"""Stage 11 — AI Lab sanitizer: секреты/PII-подобные идентификаторы.

Поверх bossman.obs.redact_obj (Bearer/api_key/token) добавляем PII-подобное:
email, IPv4, длинные hex/base64-подобные строки, ключ=значение секретов.
Версия санитайзера входит в provenance каждого сэмпла.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .. import obs

SANITIZER_VERSION = "ailab-sanitize-1"

# PII-подобные шаблоны. Заменяем ЦЕЛИКОМ на плейсхолдеры, не пытаясь «частично
# скрыть»: частичное скрытие — источник утечек.
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RE_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")            # sha/token-подобные
_RE_LONG_B64 = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")    # base64-подобные
_RE_PHONE = re.compile(r"\b\+?\d[\d\s()-]{8,}\b")

_EMAIL_SUB = "[EMAIL_REDACTED]"
_IPV4_SUB = "[IP_REDACTED]"
_HEX_SUB = "[HEX_REDACTED]"
_B64_SUB = "[B64_REDACTED]"
_PHONE_SUB = "[PHONE_REDACTED]"


def sanitize_text(text: str) -> str:
    """PII-подобное → плейсхолдеры. Порядок: длинные до коротких."""
    if not isinstance(text, str):
        return text
    out = text
    for pattern, sub in ((_RE_LONG_B64, _B64_SUB), (_RE_LONG_HEX, _HEX_SUB),
                         (_RE_EMAIL, _EMAIL_SUB), (_RE_IPV4, _IPV4_SUB),
                         (_RE_PHONE, _PHONE_SUB)):
        out = pattern.sub(sub, out)
    return out


def sanitize_obj(obj: Any) -> Any:
    """Рекурсивная зачистка: сначала obs (секреты), затем PII-подобное в строках.
    Ключ, похожий на секрет, заменяет значение целиком."""
    clean = obs.redact_obj(obj)

    def walk(x: Any) -> Any:
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if isinstance(k, str) and re.search(
                        r"secret|password|token|api_key|authorization|cookie", k, re.I):
                    out[k] = obs.REDACTED
                else:
                    out[k] = walk(v)
            return out
        if isinstance(x, list):
            return [walk(i) for i in x]
        if isinstance(x, str):
            return sanitize_text(x)
        return x

    return walk(clean)


def content_sha256(payload: Any) -> str:
    """Стабильный хеш содержимого (для provenance и детекта дублей)."""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
