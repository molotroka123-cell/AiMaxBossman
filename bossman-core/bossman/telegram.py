"""Telegram — запасной канал: уведомления и кнопки да/нет для подтверждений.
Не настроен (нет токена) — просто молчит, всё остаётся в панели «Подтверждения»."""
from __future__ import annotations

import httpx

from .config import settings

API = "https://api.telegram.org"


def enabled() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


async def notify(text: str) -> None:
    if not enabled():
        return
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{API}/bot{settings.telegram_bot_token}/sendMessage",
                          json={"chat_id": settings.telegram_chat_id, "text": text[:4000]})


async def ask_approval(approval_id: int, preview: str) -> None:
    """Кнопки «Да/Нет»; ответ приходит вебхуком или поллингом в api.py → POST /approvals/{id}."""
    if not enabled():
        return
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{API}/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id,
                  "text": f"Подтверждение #{approval_id}\n\n{preview[:3500]}",
                  "reply_markup": {"inline_keyboard": [[
                      {"text": "✅ Да", "callback_data": f"approve:{approval_id}"},
                      {"text": "❌ Нет", "callback_data": f"reject:{approval_id}"}]]}})
