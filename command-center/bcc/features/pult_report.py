"""`pult.report`: an agent (DeepSeek, Kimi, local) sends a short TEXT report to the owner's Пульт (owner 29.10).

The recipient is fixed: the single owner of the companion config (`direct_gen.pult.load_target`) — the agent cannot name
a chat. Text is capped, secrets are redacted before sending, at most MAX_PER_HOUR reports per hour, and the call is not
replayed automatically (non-idempotent), so a restart cannot send the same report twice.
"""
from __future__ import annotations

import re
import time

import httpx
from fastapi import APIRouter

from ..direct_gen import pult as _pult
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter()
MAX_CHARS = 3500
MAX_PER_HOUR = 12
_sent: list[float] = []
_SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|nvapi-[A-Za-z0-9_-]{16,}|[A-Fa-f0-9]{40,}|"
                     r"(?i:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*\S+|-----BEGIN [A-Z ]*KEY-----)")


def redact(text: str) -> str:
    return _SECRET.sub("[скрыто]", text)


async def _send_text(token: str, chat_id: int, text: str, proxy: str = "", transport=None) -> int | None:
    from ..telegram_companion.adapters import bot_api_url
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), follow_redirects=False, trust_env=False,
                                     proxy=(proxy or None) if transport is None else None, transport=transport) as c:
            r = await c.post(bot_api_url(token, "sendMessage"),
                             data={"chat_id": str(chat_id), "text": text, "disable_web_page_preview": "true"})
        body = r.json()
    except (httpx.HTTPError, OSError, TimeoutError, ValueError):
        raise _pult.PultError(502, "pult_unreachable", "Telegram недоступен с этого ПК — отчёт не отправлен") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise _pult.PultError(502, "pult_rejected", "Telegram не принял отчёт")
    return (body.get("result") or {}).get("message_id")


async def _report(args: dict, ctx: ToolContext) -> ToolResult:
    title = redact(str(args.get("title") or "Отчёт агента").strip())[:80]
    body = redact(str(args.get("text") or "").strip())
    if not body:
        return ToolResult(content="REFUSED: text is empty", error=True, one_line="pult.report: empty")
    now = time.time()
    _sent[:] = [t for t in _sent if now - t < 3600]
    if len(_sent) >= MAX_PER_HOUR:
        return ToolResult(content=f"REFUSED: at most {MAX_PER_HOUR} reports per hour", error=True,
                          one_line="pult.report: rate limit")
    agent = (ctx.agent or {}).get("name") if isinstance(getattr(ctx, "agent", None), dict) else None
    text = f"📋 {title}\n— {agent or 'агент Bossman'}\n\n{body}"[:MAX_CHARS]
    try:
        token, chat_id, proxy = _pult.load_target()
        mid = await _send_text(token, chat_id, text, proxy)
    except _pult.PultError as exc:
        return ToolResult(content=f"ERROR: {exc.message}", error=True, one_line=f"pult.report: {exc.code}")
    _sent.append(now)
    return ToolResult(content="Отчёт отправлен в Пульт владельцу.", one_line="pult.report: sent",
                      data={"message_id": mid, "chars": len(text)})


async def setup(svc) -> None:
    REGISTRY.register(ToolSpec(
        name="pult.report",
        description="Отправить владельцу в Пульт короткий ТЕКСТОВЫЙ отчёт (до 3500 символов): что сделано, результат, что дальше. "
                    "Адресат один — владелец; секреты вырезаются; не больше 12 отчётов в час.",
        handler=_report, category="send", source="builtin", default_effect="auto", timeout_seconds=40, idempotent=False,
        input_schema={"title": {"type": "string", "maxLength": 80, "description": "Заголовок"},
                      "text": {"type": "string", "maxLength": MAX_CHARS, "description": "Текст отчёта"}},
        required=["text"]))


FEATURE = Feature(name="pult_report", router=router, setup=setup)
