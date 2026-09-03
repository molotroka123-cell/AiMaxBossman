"""V2 closure — главная отвечает на «где я нужен» за 10 секунд.

Блок «Нужно ваше внимание» и карточка «Сейчас в работе» строятся из настоящего
состояния сервера (очередь подтверждений, упавшие задачи, живые задачи), а не из
украшений: тест создаёт состояние в базе живого сервера и проверяет, что владелец
видит именно его, а клик ведёт на нужную страницу."""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from bcc.db import task_runs as runs_t, tasks as tasks_t, utcnow

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _insert_task(srv, *, status: str, title: str, run: dict | None = None) -> int:
    """Создаёт задачу (и при необходимости её прогон) в базе живого сервера."""
    async def go():
        async with srv.svc.db.session() as s:
            res = await s.execute(sa.insert(tasks_t).values(
                title=title, prompt="проверка главной", status=status, priority=5, max_retries=0,
                created_at=utcnow(), updated_at=utcnow()))
            tid = int(res.inserted_primary_key[0])
            if run is not None:
                await s.execute(sa.insert(runs_t).values(task_id=tid, attempt=1, status=status, **run))
            await s.commit()
            return tid

    return asyncio.run_coroutine_threadsafe(go(), srv.loop).result(timeout=10)


def _create_approval(srv, kind: str, preview: str, task_id: int | None = None) -> int:
    return asyncio.run_coroutine_threadsafe(
        srv.svc.approvals.create(kind, preview, task_id=task_id), srv.loop).result(timeout=10)["id"]


def test_home_attention_reflects_real_backend_state(live):  # noqa: F811
    from playwright.sync_api import sync_playwright

    errors: list[str] = []

    # состояние ДО открытия страницы: упавшая задача, живая задача, решение в очереди
    failed_id = _insert_task(live, title="Ночной отчёт", status="failed",
                             run={"error": "провайдер недоступен", "finished_at": utcnow()})
    running_id = _insert_task(live, title="Разбор почты", status="running",
                              run={"started_at": utcnow(), "model_alias": "qwen-14b",
                                   "checkpoint": {"step": 2, "note": "", "messages": []}})
    _create_approval(live, "terminal.run", "rm -rf ./tmp-artifacts", task_id=running_id)

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        page.goto(live.url + "/#/home-v3", wait_until="domcontentloaded")
        page.wait_for_selector("#attention", timeout=20000)

        attn = page.locator("#attention")
        assert "нужно ваше внимание" in attn.inner_text().lower()
        kinds = attn.locator(".bx-attn-row").evaluate_all("rows => rows.map(r => r.dataset.kind)")
        assert "approval" in kinds, kinds          # очередь подтверждений
        assert "task-failed" in kinds, kinds       # упавшая задача
        assert "Ночной отчёт" in attn.inner_text()
        # блокирующее идёт выше ожидания
        assert kinds.index("task-failed") < kinds.index("approval")

        # карточка «Сейчас в работе» показывает живую задачу и тикающее время
        now = page.locator("#now-card")
        assert "Разбор почты" in now.inner_text()
        assert "выполняется" in now.inner_text()
        assert "qwen-14b" in now.inner_text()      # модель берётся из реального прогона
        first = now.locator(".bx-now-elapsed").inner_text()
        page.wait_for_timeout(1400)
        assert now.locator(".bx-now-elapsed").inner_text() != first, "таймер не идёт"

        # клик по строке ведёт на страницу, где решают
        attn.locator('.bx-attn-row[data-kind="approval"]').click()
        page.wait_for_function("location.hash.includes('approvals')", timeout=10000)

        browser.close()

    assert errors == [], errors
    assert failed_id and running_id


def test_home_attention_is_calm_without_real_problems(live):  # noqa: F811
    """Пустая система — блок молчит, а не выдумывает строки."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, live)
        page.goto(live.url + "/#/home-v3", wait_until="domcontentloaded")
        page.wait_for_selector("#attention", timeout=20000)
        assert page.locator("#attention.is-calm").count() == 1
        assert "ничего не ждёт вашего решения" in page.locator("#attention").inner_text().lower()
        assert page.locator("#attention .bx-attn-row").count() == 0
        assert "сейчас ничего не выполняется" in page.locator("#now-card").inner_text().lower()
        browser.close()
