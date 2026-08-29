"""Compatibility facade: existing approvals.py can keep importing bossman.telegram.

Раньше здесь был прямой httpx-транспорт с сырыми callback_data вида
approve:<id>/reject:<id> — уязвимость (см. docs/context/... аудит пакета
cost-governor+notifications). Реальная отправка теперь идёт через
notifications.dispatcher (durable queue, retry/backoff) и
notifications.telegram_transport (opaque single-use callback-токены).
Сигнатуры enabled/notify/ask_approval сохранены, чтобы approvals.py и прочий
код ядра не переписывались."""
from __future__ import annotations

from .notifications.runtime import TELEGRAM, enqueue_approval, enqueue_text, handle_telegram_webhook


def enabled() -> bool:
    return TELEGRAM.enabled()


async def notify(text: str) -> None:
    await enqueue_text(text)


async def ask_approval(approval_id: int, preview: str) -> None:
    await enqueue_approval(approval_id, preview)


async def handle_webhook(update: dict, secret_header: str):
    return await handle_telegram_webhook(update, secret_header)
