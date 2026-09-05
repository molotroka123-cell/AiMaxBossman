"""Пульт владельца (ui/pages/control.js) в настоящем Chromium против живого сервера.

Проверяется обещание страницы, а не вёрстка: восемь колонок владельца на экране,
и зелёный COMPLETE не появляется раньше канонического финализатора.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _seed(srv, *, status, finalized=False):
    """Задача с агентом и прогоном прямо в БД живого сервера."""
    import asyncio
    from bcc.db import agents as agents_t, events as events_t, task_runs as runs_t, \
        tasks as tasks_t, utcnow

    async def go():
        async with srv.svc.db.session() as s:
            aid = int((await s.execute(sa.insert(agents_t).values(name="кодер"))).inserted_primary_key[0])
            tid = int((await s.execute(sa.insert(tasks_t).values(
                title="починить отчёт", prompt="секретный промпт владельца", agent_id=aid,
                status=status, created_at=utcnow(), updated_at=utcnow()))).inserted_primary_key[0])
            await s.execute(sa.insert(runs_t).values(task_id=tid, status="completed",
                                                     model_alias="glm-local", cost_usd=0.5))
            if finalized:
                await s.execute(sa.insert(events_t).values(kind="task.finalized", ts=utcnow(),
                                                           data={"task_id": tid}))
            await s.commit()
        return tid
    return asyncio.run_coroutine_threadsafe(go(), srv.loop).result(10)


def _open(page, srv):
    _login(page, srv)
    page.goto(srv.url + "/#/control")
    page.wait_for_selector("text=Пульт владельца", timeout=15000)
    return page.locator("#view").inner_text()


def test_owner_sees_all_eight_columns_and_no_prompts(live):
    from playwright.sync_api import sync_playwright

    _seed(live, status="running")
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            text = _open(page, live)
            for column in ("ЧТО", "КТО", "ГДЕ", "МОДЕЛЬ", "СОСТОЯНИЕ",
                           "ПОЧЕМУ ЗАБЛОКИРОВАНО", "ЦЕНА", "ВНИМАНИЕ"):
                assert column in text, f"нет колонки {column}: {text[:400]}"
            assert "починить отчёт" in text and "кодер" in text and "glm-local" in text
            assert "секретный промпт" not in text          # промпты владельцу не показываем
            assert errors == [], errors
        finally:
            browser.close()


def test_green_complete_only_after_the_finalizer(live):
    """Задача уже `completed` в таблице, но следа финализатора нет: экран обязан
    показать VERIFIED с причиной, а не зелёное «готово»."""
    from playwright.sync_api import sync_playwright

    _seed(live, status="completed", finalized=False)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            text = _open(page, live)
            assert "VERIFIED" in text and "финализатора" in text
            # зелёной плашки COMPLETE на экране нет (упоминание правила внизу — не плашка)
            assert page.locator("td .badge-ok").count() == 0
            assert page.locator("td .badge", has_text="COMPLETE").count() == 0

            _seed(live, status="completed", finalized=True)
            page.wait_for_timeout(2500)        # снимок control-plane кэшируется на 2 с
            page.reload()
            page.wait_for_selector("text=Пульт владельца", timeout=15000)
            page.wait_for_selector("td .badge-ok", timeout=15000)
            assert page.locator("td .badge-ok", has_text="COMPLETE").count() == 1
        finally:
            browser.close()
