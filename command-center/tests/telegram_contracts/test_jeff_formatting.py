"""Jeff reply formatting: beautiful in Telegram, never able to lose a reply.

The whitelist converter escapes everything first and only then adds the small
entity set the owner asked for (bold, code, fences, quotes, spoilers). The
sender must fall back to the plain part when Telegram refuses to parse, so a
markup mistake can never swallow an answer.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from bcc.telegram_companion.adapters import Telegram
from bcc.telegram_companion.config import Person
from bcc.telegram_companion.formatting import to_telegram_html
from bcc.telegram_companion.service import scrub  # noqa: F401 — parity with send()
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telegram_contracts.test_companion import cfg, OWNER  # noqa: E402


def test_escapes_html_before_any_entity():
    assert to_telegram_html("<b>жирно</b>") == "&lt;b&gt;жирно&lt;/b&gt;"
    assert to_telegram_html("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_whitelist_entities():
    assert "<b>жирный</b>" in to_telegram_html("**жирный** и текст")
    assert "<code>код()</code>" in to_telegram_html("вызови `код()` сейчас")
    assert "<tg-spoiler>секрет</tg-spoiler>" in to_telegram_html("тут ||секрет|| внутри")
    assert "<pre>line1\nline2</pre>" in to_telegram_html("до\n```py\nline1\nline2\n```\nпосле")


def test_blockquote_lines_merge():
    out = to_telegram_html("> первая\n> вторая\n\nпосле цитаты")
    assert "<blockquote>первая\nвторая</blockquote>" in out
    assert "&gt; первая" not in out


def test_unclosed_marker_stays_literal():
    out = to_telegram_html("незакрытое **жирное и ||спойлер")
    assert "**жирное" in out and "||спойлер" in out
    assert "<b>" not in out.replace("&lt;b&gt;", "") or "жирное" not in _tag_bodies(out)


def _tag_bodies(text: str) -> str:
    import re
    return "".join(re.findall(r"<b>(.*?)</b>", text))


def test_snake_case_and_urls_untouched():
    text = "см. snake_case_name и https://example.com/a_b?a=1||b=2"
    out = to_telegram_html(text)
    assert "snake_case_name" in out
    assert "https://example.com/a_b?a=1||b=2" in out


def test_send_uses_html_then_falls_back_plain():
    sent = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        sent.append(body)
        if body.get("parse_mode") == "HTML":
            return httpx.Response(400, json={"ok": False,
                                             "description": "can't parse entities"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    async def run():
        tg = Telegram(cfg(bot_token="test-fixture"), transport=httpx.MockTransport(handler))
        try:
            await tg.send(OWNER, "Ответ с **жирным** и `кодом`", parse_mode="HTML")
        finally:
            await tg.close()

    asyncio.run(run())
    assert sent[0]["parse_mode"] == "HTML"
    assert "<b>жирным</b>" in sent[0]["text"]
    assert "parse_mode" not in sent[1]
    assert sent[1]["text"] == "Ответ с **жирным** и `кодом`"


def test_send_without_parse_mode_stays_plain():
    sent = []

    def handler(request: httpx.Request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 8}})

    async def run():
        tg = Telegram(cfg(bot_token="test-fixture"), transport=httpx.MockTransport(handler))
        try:
            await tg.send(OWNER, "**не форматировать**")
        finally:
            await tg.close()

    asyncio.run(run())
    assert "parse_mode" not in sent[0]
    assert sent[0]["text"] == "**не форматировать**"
