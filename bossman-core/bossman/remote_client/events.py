"""Мост событий Stage 6: подписка на ядровую шину `bossman.events` с фильтром
по скоупам устройства. Второй брокер НЕ поднимается — мы бриджим существующую
шину, добавляя правило «устройство получает событие, только если у него есть
скоуп, требуемый категорией этого события».

Правило фильтра (default-deny для неизвестных категорий):
  approval.*                 → нужен SCOPE_APPROVE (лента подтверждений чувствительна)
  agent.*, model.*           → нужен SCOPE_ADMIN   (управление ядром)
  task.*, project.*, run.*, tool.* → нужен SCOPE_CHAT (обычная активность)
  всё прочее                 → нужен SCOPE_ADMIN   (фейл-клоуз: не утекать неизвестное)
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from .. import events as core_events
from .auth import SCOPE_ADMIN, SCOPE_APPROVE, SCOPE_CHAT, Principal


def event_required_scope(kind: str) -> str:
    """Какой скоуп нужен, чтобы получить событие данного вида."""
    if kind.startswith("approval"):
        return SCOPE_APPROVE
    if kind.startswith("agent") or kind.startswith("model"):
        return SCOPE_ADMIN
    if kind.startswith(("task", "project", "run", "tool")):
        return SCOPE_CHAT
    return SCOPE_ADMIN  # неизвестное — только админу (fail-closed)


def event_allowed(kind: str, scopes) -> bool:
    """Разрешено ли устройству со скоупами `scopes` получить событие `kind`."""
    return event_required_scope(kind) in scopes


async def iter_device_events(principal: Principal, queue=None) -> AsyncIterator[str]:
    """Асинхронный поток JSON-строк событий, разрешённых скоупами устройства.

    Подписывается на ядровую шину, пропускает только те события, чью категорию
    покрывают скоупы принципала. `queue` можно передать извне (для тестов),
    иначе создаётся собственная подписка и снимается по завершении.
    """
    own = queue is None
    if queue is None:
        queue = core_events.subscribe()
    try:
        while True:
            msg = await queue.get()
            try:
                kind = json.loads(msg).get("kind", "")
            except (ValueError, TypeError):
                kind = ""
            if event_allowed(kind, principal.scopes):
                yield msg
    finally:
        if own:
            core_events.unsubscribe(queue)


async def sse_wrap(source: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """Обернуть поток строк в формат Server-Sent Events."""
    async for msg in source:
        yield f"data: {msg}\n\n".encode()
