"""Структурированное логирование + вычистка секретов (общий шов этапов 4–7).

Ядро строит заголовки `Bearer …` (llm.py, gateway/backends.py), а
`cloud_calls.prompt_preview` хранит превью запроса — ни один секрет не должен
попасть в лог-строку. Единый JSON-логгер с фильтром-редактором гарантирует, что
значения Authorization/Bearer/token/cookie/api_key/secret/password не всплывут в
логах, даже если их случайно передали в сообщение или в extra.

Именование модуля `obs.py` (не `logging.py`) выбрано намеренно: файл с именем
`logging.py` в пакете сбивает с толку при чтении `import logging`.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from . import correlation

REDACTED = "«REDACTED»"

# Ключи, значения которых считаем секретами (в JSON, query, key=value, заголовках).
_SENSITIVE_KEYS = (
    "authorization", "bearer", "cookie", "set-cookie",
    "api_key", "api-key", "apikey", "x-api-key", "key", "keys",
    "access_token", "refresh_token", "token",
    "secret", "client_secret", "password", "passwd", "pwd",
)

# 1) "Bearer <token>" / "Basic <token>" в любом тексте.
_RE_BEARER = re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9._\-+/=]{6,}", re.IGNORECASE)

# 2) key: value  /  key = value  /  "key": "value"  для чувствительных ключей.
#    Значение — до кавычки/запятой/переноса/закрывающей скобки/амперсанда.
_KEY_ALT = "|".join(re.escape(k) for k in _SENSITIVE_KEYS)
_RE_KV = re.compile(
    rf'(?i)(["\']?(?:{_KEY_ALT})["\']?\s*[:=]\s*)(["\']?)([^\s"\',&}}\)]+)(\2)'
)

# 3) sk-/ghp_/xoxb- и подобные длинные токены провайдеров как «голый» секрет.
# Токены провайдеров содержат дефисы и подчёркивания ВНУТРИ (sk-proj-…, sk-or-v1-…),
# поэтому класс символов не может быть только [A-Za-z0-9]: red-team показал, что
# «sk-LEAK-abcdef0123456789» проходил мимо фильтра целиком.  # ci-secret-scan: allow
_RE_TOKENLIKE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}"          # OpenAI/OpenRouter и производные
    r"|ghp_[A-Za-z0-9_-]{12,}"         # GitHub PAT
    r"|gho_[A-Za-z0-9_-]{12,}|ghs_[A-Za-z0-9_-]{12,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"   # Slack
    r"|AKIA[0-9A-Z]{16}"               # AWS access key id
    r"|AIza[0-9A-Za-z_-]{30,}"         # Google API key
    r"|hf_[A-Za-z0-9]{16,})")          # HuggingFace


def redact(text: str) -> str:
    """Вернуть текст, в котором значения секретов заменены на «REDACTED».
    Идемпотентно (повторный проход ничего не меняет)."""
    if not text:
        return text
    text = _RE_BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _RE_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(4)}", text)
    text = _RE_TOKENLIKE.sub(REDACTED, text)
    return text


def redact_obj(obj: Any) -> Any:
    """Рекурсивно вычистить секреты из dict/list/str по имени ключа И по значению."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = REDACTED
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_obj(v) for v in obj)
    if isinstance(obj, str):
        return redact(obj)
    return obj


class RedactionFilter(logging.Filter):
    """Вычищает секреты из уже отформатированного сообщения ДО эмиссии."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — кривой формат не должен ронять лог
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = ()
        # extra-поля тоже чистим
        for k, v in list(record.__dict__.items()):
            if k in _RESERVED or not isinstance(v, str):
                continue
            record.__dict__[k] = redact(v)
        return True


_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """JSON-строка на запись: ts, level, logger, msg + бандл correlation + extra."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(correlation.current())
        for k, v in record.__dict__.items():
            if k in _RESERVED:
                continue
            payload.setdefault(k, v)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Навесить JSON-хендлер с редактором на root. Идемпотентно."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Логгер с гарантированным фильтром-редактором (на случай, если
    configure_logging ещё не звали — например, в тестах)."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactionFilter) for f in logger.filters):
        logger.addFilter(RedactionFilter())
    return logger
