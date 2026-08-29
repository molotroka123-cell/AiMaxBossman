"""Сквозные идентификаторы (общий шов для этапов 4–7).

Одно действие проходит через HTTP/WS-запрос → задачу → run → инструмент →
модель → видео-джобу → устройство. Чтобы связать всё это в логах и в шине
событий, держим бандл id в contextvar. `events.emit` подмешивает текущий бандл
в каждый payload, JSON-логгер читает тот же contextvar — и лог-строка, и
WS-событие несут одинаковые id.

Модуль зависит только от stdlib (никаких импортов events/llm), чтобы не
создавать циклов: его импортируют и events.py, и obs.py.
"""
from __future__ import annotations

import contextlib
import contextvars
import uuid
from typing import Iterator

# Известные ключи бандла. Пустые не подмешиваются в события/логи.
_KEYS = ("request_id", "task_id", "run_id", "job_id", "device_id")

_cid: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("bossman_cid", default={})


def new_id(prefix: str = "") -> str:
    h = uuid.uuid4().hex[:16]
    return f"{prefix}{h}" if prefix else h


def current() -> dict[str, str]:
    """Копия текущего бандла (только непустые значения)."""
    return dict(_cid.get())


def get(key: str) -> str | None:
    return _cid.get().get(key)


def bind(**ids: str | None) -> contextvars.Token:
    """Слить переданные id в текущий бандл; вернуть token для reset().
    None-значения игнорируются (не затирают уже установленное)."""
    merged = dict(_cid.get())
    for k, v in ids.items():
        if k not in _KEYS:
            raise KeyError(f"неизвестный correlation-ключ: {k}")
        if v:
            merged[k] = str(v)
    return _cid.set(merged)


def reset(token: contextvars.Token) -> None:
    _cid.reset(token)


@contextlib.contextmanager
def scope(**ids: str | None) -> Iterator[dict[str, str]]:
    """Контекст-менеджер: временно привязать id, автоматически откатить.
    Если request_id не задан явно и его ещё нет — сгенерировать."""
    if "request_id" in ids and not ids.get("request_id"):
        ids["request_id"] = None
    if not _cid.get().get("request_id") and not ids.get("request_id"):
        ids["request_id"] = new_id("req_")
    token = bind(**ids)
    try:
        yield current()
    finally:
        reset(token)
