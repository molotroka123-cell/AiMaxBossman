"""`pult.report`: an agent (DeepSeek, Kimi, local) sends a short TEXT report to the owner's Пульт (owner 29.10).

The recipient is fixed: the single owner of the companion config (`direct_gen.pult.load_target`) — the agent cannot name
a chat. Text is capped, secrets are redacted before sending, at most MAX_PER_HOUR reports per hour, and the call is not
replayed automatically (non-idempotent), so a restart cannot send the same report twice.
"""
from __future__ import annotations

import html
import re
import time

import httpx
from fastapi import APIRouter

from ..direct_gen import pult as _pult
from ..plugin_security import redact_text as _ps_redact_text
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter()
MAX_CHARS = 3500
MAX_PER_HOUR = 12
_sent: list[float] = []

_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*KEY-----[\s\S]*?-----END [A-Z0-9 ]*KEY-----|"
    r"-----BEGIN [A-Z0-9 ]*KEY-----"
)
_KV_SECRET = re.compile(
    r"""(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|client[_-]?secret|private[_-]?key|access[_-]?key|auth[_-]?token|pairing[_-]?token)"""
    r"""(\s*["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}"']+)"""
)
_BEARER_SECRET = re.compile(
    r"(?i)(\bAuthorization\s*:\s*Bearer\s+|\bBearer\s+)([A-Za-z0-9._~+/=-]{8,})"
)
_TOKEN_PATTERNS = re.compile(
    r"(?:"
    r"sk-ant-[A-Za-z0-9_-]{8,}|"
    r"sk-proj-[A-Za-z0-9_-]{8,}|"
    r"sk_live_[A-Za-z0-9_-]{8,}|"
    r"sk_test_[A-Za-z0-9_-]{8,}|"
    r"sk-[A-Za-z0-9_-]{12,}|"
    r"nvapi-[A-Za-z0-9_-]{12,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"ASIA[0-9A-Z]{16}|"
    r"gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"github_pat_[A-Za-z0-9_]{12,}|"
    r"glpat-[A-Za-z0-9_-]{10,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"stdl_[A-Za-z0-9_-]{10,}|"
    r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|"
    r"\b[A-Fa-f0-9]{40,}\b"
    r")"
)


def contains_secret(text: str) -> bool:
    if not text:
        return False
    return redact(text) != str(text)


def redact(text: str) -> str:
    if not text:
        return ""
    cleaned = _PEM_BLOCK.sub("[скрыто]", str(text))
    cleaned = _BEARER_SECRET.sub(r"\1[скрыто]", cleaned)
    cleaned = _KV_SECRET.sub(r"\1\2[скрыто]", cleaned)
    cleaned = _TOKEN_PATTERNS.sub("[скрыто]", cleaned)
    cleaned = _ps_redact_text(cleaned)
    return cleaned.replace("***REDACTED***", "[скрыто]")


def _inline_markup(escaped_line: str) -> str:
    s = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped_line)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    return s


def format_telegram_html(
    title: str,
    body: str = "",
    agent: str | None = None,
    *,
    summary: str = "",
    findings: list[str] | None = None,
    metrics: dict[str, object] | None = None,
    quotes: list[str] | None = None,
    signature: str | None = None,
    max_chars: int = MAX_CHARS,
) -> str:
    """Format an audit/status report using Telegram HTML (<b>, <blockquote>, <code>) and sign as Argon by default.

    Long reports are truncated BEFORE wrapping lines in HTML tags so every <b>, <code>, and <blockquote> tag is
    guaranteed closed and valid for Telegram's HTML parser, and the signature footer is always preserved.
    """
    clean_title = redact(title or "").strip() or "Отчёт Аргона"
    body_parts: list[str] = []
    if body:
        body_parts.append(str(body))
    if summary:
        body_parts.append(str(summary))
    if findings:
        for item in findings:
            body_parts.append(f"• {item}")
    if metrics:
        for k, v in metrics.items():
            body_parts.append(f"• `{k}`: `{v}`")
    if quotes:
        for q in quotes:
            body_parts.append(f"> {q}")
    clean_body = redact("\n".join(body_parts)).strip()
    clean_agent = redact(signature or agent or "Аргон").strip() or "Аргон"

    safe_title = html.escape(clean_title[:80])
    sig_name = html.escape(clean_agent[:60])

    header = f"<b>📋 {safe_title}</b>"
    footer = f"<blockquote>🤖 <b>Подпись:</b> {sig_name}</blockquote>"
    overhead = len(header) + len(footer) + 4  # "\n\n" * 2
    budget = max(64, max_chars - overhead)

    raw_lines = clean_body.splitlines()
    blocks: list[tuple[bool, str]] = []  # (is_quote, rendered_line)
    for raw_line in raw_lines:
        stripped = raw_line.lstrip()
        if stripped.startswith(">"):
            content = stripped[1:].lstrip()
            blocks.append((True, _inline_markup(html.escape(content))))
        else:
            blocks.append((False, _inline_markup(html.escape(raw_line))))

    # Group consecutive quote lines into <blockquote>...</blockquote> blocks, respecting budget
    out_parts: list[str] = []
    used = 0
    truncated = False
    i = 0
    while i < len(blocks):
        is_quote, rendered = blocks[i]
        if is_quote:
            q_lines = [rendered]
            i += 1
            while i < len(blocks) and blocks[i][0]:
                q_lines.append(blocks[i][1])
                i += 1
            candidate = "<blockquote>" + "\n".join(q_lines) + "</blockquote>"
        else:
            candidate = rendered
            i += 1

        sep = 1 if out_parts else 0
        if used + sep + len(candidate) <= budget:
            out_parts.append(candidate)
            used += sep + len(candidate)
        else:
            remaining = budget - used - sep
            if remaining > 30:
                # Truncate plain text safely without leaving unclosed HTML tags
                plain_fallback = re.sub(r"</?(?:b|code|blockquote)>", "", html.unescape(candidate))
                cut_len = max(1, remaining - 28)
                safe_tail = html.escape(plain_fallback[:cut_len].rstrip()) + "…"
                if is_quote:
                    safe_tail = f"<blockquote>{safe_tail}</blockquote>"
                if len(safe_tail) <= remaining:
                    out_parts.append(safe_tail)
            truncated = True
            break

    if truncated and not out_parts:
        plain = clean_body[: max(1, budget - 4)].rstrip()
        out_parts.append(html.escape(plain) + "…")

    formatted_body = "\n".join(out_parts)
    return f"{header}\n\n{formatted_body}\n\n{footer}"


async def _send_text(token: str, chat_id: int, text: str, proxy: str = "", transport=None) -> int | None:
    from ..telegram_companion.adapters import bot_api_url
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), follow_redirects=False, trust_env=False,
                                     proxy=(proxy or None) if transport is None else None, transport=transport) as c:
            r = await c.post(bot_api_url(token, "sendMessage"),
                             data={"chat_id": str(chat_id), "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"})
        body = r.json()
    except (httpx.HTTPError, OSError, TimeoutError, ValueError):
        raise _pult.PultError(502, "pult_unreachable", "Telegram недоступен с этого ПК — отчёт не отправлен") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise _pult.PultError(502, "pult_rejected", "Telegram не принял отчёт")
    return (body.get("result") or {}).get("message_id")


async def _report(args: dict, ctx: ToolContext) -> ToolResult:
    title = redact(str(args.get("title") or "Отчёт Аргона").strip())[:80]
    body = redact(str(args.get("text") or "").strip())
    if not body:
        return ToolResult(content="REFUSED: text is empty", error=True, one_line="pult.report: empty")
    now = time.time()
    _sent[:] = [t for t in _sent if now - t < 3600]
    if len(_sent) >= MAX_PER_HOUR:
        return ToolResult(content=f"REFUSED: at most {MAX_PER_HOUR} reports per hour", error=True,
                          one_line="pult.report: rate limit")
    agent = (ctx.agent or {}).get("name") if isinstance(getattr(ctx, "agent", None), dict) else None
    text = format_telegram_html(title, body, agent=agent or "Аргон")
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
