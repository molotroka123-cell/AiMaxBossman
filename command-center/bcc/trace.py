"""TRUTH-003 §14 — один trace_id на весь жизненный цикл действия.

ContextVar, который движок ставит на время исполнения run'а (`run-<id>`); шина
событий (`EventBus.emit`) добавляет `trace_id` в каждое событие, если он не
передан явно. Так request.classified → capability.selected → permission.checked →
action.started/result → verification.result → task.finalized связываются одним
идентификатором без правки каждого вызова emit. Без промптов и секретов —
trace_id несёт только идентификатор.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("bossman_trace_id", default=None)


def get_trace_id() -> str | None:
    return current_trace_id.get()


def run_trace_id(run_id: int) -> str:
    return f"run-{int(run_id)}"


@contextmanager
def trace(trace_id: str) -> Iterator[str]:
    token = current_trace_id.set(trace_id)
    try:
        yield trace_id
    finally:
        current_trace_id.reset(token)
