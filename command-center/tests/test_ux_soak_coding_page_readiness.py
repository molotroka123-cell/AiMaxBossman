"""RC 1.9 soak (workstream F): «Coding-сессии» stood on its loading skeleton for > 25 s.

Repro (60-min soak, owner window): after a backend restart the page's render awaited
GET /api/coding-tasks/readiness, which performs a live handshake with the coding sidecar
(a real model call, up to 150 s on a cold local model). Until it answered the owner saw
only grey skeletons — the rest of the page (sessions, tasks, buttons) was already known.

The page now renders after a short wait and fills the readiness line in place when the
handshake answers; «Новая задача агенту» stays disabled until readiness says available.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

SLOW_S = 8.0


def test_coding_page_renders_before_a_slow_readiness_handshake(live, monkeypatch):  # noqa: F811
    from playwright.sync_api import sync_playwright

    from bcc.features import coding_tasks

    async def slow_readiness(svc):
        await asyncio.sleep(SLOW_S)
        return {"available": False, "runtime": True, "sidecar_command": True, "roots": [],
                "reason": "сайдкар ответил: тестовая причина", "handshake": None}

    monkeypatch.setattr(coding_tasks, "readiness", slow_readiness)
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.wait_for_selector("#view[data-rendered]", timeout=20000)
        t = time.monotonic()
        page.goto(live.url + "/#/coding")
        page.wait_for_selector("#view[data-rendered='coding']", timeout=SLOW_S * 1000)
        rendered_after = time.monotonic() - t
        early = page.evaluate("() => ({ line: document.getElementById('coding-readiness').innerText, "
                              "disabled: document.getElementById('coding-new-task').disabled })")
        page.wait_for_function("() => document.getElementById('coding-readiness').innerText.includes('тестовая причина')",
                               timeout=(SLOW_S + 10) * 1000)
        late = page.evaluate("() => ({ line: document.getElementById('coding-readiness').innerText, "
                             "title: document.getElementById('coding-new-task').title, "
                             "disabled: document.getElementById('coding-new-task').disabled, "
                             "skeletons: document.querySelectorAll('#view .skeleton').length })")
        browser.close()

    assert rendered_after < SLOW_S - 2, rendered_after
    assert "проверяю готовность" in early["line"] and early["disabled"] is True
    assert "OpenHands сейчас недоступен" in late["line"] and "тестовая причина" in late["title"]
    assert late["disabled"] is True and late["skeletons"] == 0
