"""Owner 31.10: «обязуй все модели, кто работает в пульте, использовать Telegram-форматирование: удобно копировать код,
подпись внизу, жирный заголовок, чтобы не резало глаза»."""
from __future__ import annotations

import inspect

from bcc.features import pult_report
from bcc.telegram_companion import adapters, formatting, service


def test_style_guide_names_the_rendered_subset():
    g = formatting.TELEGRAM_STYLE_GUIDE
    assert "**жирным**" in g and "```" in g and "`коде`" in g and "> " in g and "подпись" in g


def test_markdown_from_a_model_renders_bold_heading_copyable_code_and_quote():
    out = formatting.to_telegram_html("## Итог\n**Готово**\n```\npip install x<1\n```\n> важно\nпуть `C:\\a`\n— Gemma")
    assert "<b>Итог</b>" in out and "<b>Готово</b>" in out
    assert "<pre>pip install x&lt;1</pre>" in out           # escaped, one tap-to-copy block
    assert "<blockquote>важно</blockquote>" in out and "<code>C:\\a</code>" in out


def test_every_pult_send_is_html_by_default():
    assert inspect.signature(adapters.Telegram.send).parameters["parse_mode"].default == "HTML"


def test_delegated_task_is_exactly_the_text_the_owner_confirmed():
    """Approval integrity beats formatting: the owner confirms a preview of the task text, so
    nothing may be appended to it afterwards (owner scenario OS-110 went red on 0d8e27db,
    which appended TELEGRAM_STYLE_GUIDE inside Core.delegate). The executor's answer is still
    rendered as Telegram HTML when it is delivered (send() defaults to parse_mode=HTML)."""
    import asyncio

    posted: list[dict] = []
    core = adapters.Core.__new__(adapters.Core)

    async def executor(person):
        return "fp"

    async def request(method, path, payload=None, timeout=10):
        posted.append(payload)
        return {"task": {"id": 7, "agent_id": 3, "prompt": payload["prompt"], "status": "queued",
                         "created_at": "2026-10-31T00:00:00+00:00", "title": payload["title"]}}

    core.executor, core._request = executor, request
    person = type("P", (), {"agent_id": 3})()
    task_id, status, _ = asyncio.run(core.delegate(person, "обычная работа", "fp"))
    assert posted[0]["prompt"] == "обычная работа"           # verbatim: nothing the owner did not see
    assert "TELEGRAM_STYLE_GUIDE" not in inspect.getsource(adapters.Core.delegate)
    assert (task_id, status) == (7, "queued")


def test_companion_chat_context_carries_the_style_guide():
    assert "TELEGRAM_STYLE_GUIDE" in inspect.getsource(service.Companion.context)


def test_pult_report_renders_code_blocks_headings_and_keeps_tags_balanced():
    html = pult_report.format_telegram_html(
        "Сборка", "# Результат\nвсё ок\n```\ngit log --oneline -1\n<tag>\n```\nконец", signature="Claude")
    assert "<b>Результат</b>" in html
    assert "<pre>git log --oneline -1\n&lt;tag&gt;</pre>" in html
    assert html.count("<pre>") == html.count("</pre>") == 1
    assert html.rstrip().endswith("Claude</blockquote>")
    assert "TELEGRAM_STYLE_GUIDE" in inspect.getsource(pult_report.setup)


def test_pult_report_truncation_never_leaves_an_open_pre():
    body = "```\n" + "x" * 9000 + "\n```"
    html = pult_report.format_telegram_html("Длинный", body, signature="Claude")
    assert html.count("<pre>") == html.count("</pre>")
