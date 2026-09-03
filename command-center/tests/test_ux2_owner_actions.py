"""V2 closure — кнопка владельца обязана менять состояние сервера, а не только UI.

Для каждого критичного действия: клик в настоящем Chromium → запрос → проверка прав
→ запись в базе → свежее чтение из базы (не из ответа UI) → корректное состояние
интерфейса. Успешный тост без изменения в базе считается провалом.

Отдельно проверяется безопасность решений владельца: решение принимается один раз,
отклонённое остаётся отклонённым, использованное подтверждение нельзя предъявить
повторно."""
from __future__ import annotations

import asyncio
import time

import pytest
import sqlalchemy as sa

from bcc.db import approvals as approvals_t, task_runs as runs_t, tasks as tasks_t, utcnow

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _call(srv, coro_factory, timeout: float = 10.0):
    return asyncio.run_coroutine_threadsafe(coro_factory(), srv.loop).result(timeout=timeout)


def _row(srv, table, row_id: int) -> dict:
    """Свежее чтение из базы — единственный источник правды для этих тестов."""
    async def go():
        async with srv.svc.db.session() as s:
            res = await s.execute(sa.select(table).where(table.c.id == row_id))
            r = res.first()
            return dict(r._mapping) if r else {}
    return _call(srv, go)


def _wait_row(srv, table, row_id: int, predicate, what: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = _row(srv, table, row_id)
        if predicate(last):
            return last
        time.sleep(0.2)
    raise AssertionError(f"состояние в базе не изменилось: {what}; последняя строка={last}")


def _new_task(srv, title: str, status: str = "draft") -> int:
    async def go():
        async with srv.svc.db.session() as s:
            res = await s.execute(sa.insert(tasks_t).values(
                title=title, prompt="проверка владельческих действий", status=status,
                priority=5, max_retries=0, created_at=utcnow(), updated_at=utcnow()))
            tid = int(res.inserted_primary_key[0])
            await s.commit()
            return tid
    return _call(srv, go)


def _open_task_card(page, title: str):
    card = page.locator(".task", has=page.locator(".task-title", has_text=title)).first
    card.locator(".task-head").click()
    page.wait_for_selector(".task.open .task-body", timeout=10000)
    return card


def test_owner_actions_mutate_backend_state(live):  # noqa: F811
    """Запуск и остановка задачи из UI действительно меняют задачу и её прогон."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    task_id = _new_task(live, "Действие владельца · запуск")

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        page.goto(live.url + "/#/tasks", wait_until="domcontentloaded")
        page.wait_for_selector(".task-title", timeout=20000)

        # --- ЗАПУСТИТЬ: draft → queued + появился прогон
        _open_task_card(page, "Действие владельца · запуск")
        assert _row(live, tasks_t, task_id)["status"] == "draft"
        page.locator(".task.open .task-body button", has_text="Запустить").first.click()
        after_run = _wait_row(live, tasks_t, task_id,
                              lambda r: r.get("status") in {"queued", "running"},
                              "задача не встала в очередь после «Запустить»")
        runs = _call(live, lambda: _runs_of(live, task_id))
        assert runs, "прогон задачи не создан — кнопка ничего не изменила"

        # --- ОСТАНОВИТЬ: queued → stopped
        page.wait_for_timeout(300)
        page.locator(".task.open .task-body button", has_text="Остановить").first.click()
        stopped = _wait_row(live, tasks_t, task_id, lambda r: r.get("status") == "stopped",
                            "задача не остановилась после «Остановить»")
        assert stopped["status"] == "stopped" and after_run["status"] != "stopped"
        browser.close()

    assert errors == [], errors


async def _runs_of(srv, task_id: int):
    async with srv.svc.db.session() as s:
        res = await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task_id))
        return [dict(r._mapping) for r in res.fetchall()]


def test_owner_approval_decisions_are_durable_and_single_use(live):  # noqa: F811
    """«Разрешить» и «Отклонить» пишут решение владельца; повтор ничего не меняет."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    allow_id = _call(live, lambda: live.svc.approvals.create("terminal.run", "ls ./artifacts"))["id"]
    deny_id = _call(live, lambda: live.svc.approvals.create("browser.act", "открыть внешний сайт"))["id"]

    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        page.goto(live.url + "/#/approvals", wait_until="domcontentloaded")
        page.wait_for_selector("text=ls ./artifacts", timeout=20000)

        allow_card = page.locator("section, article, div").filter(has_text="ls ./artifacts").last
        allow_card.locator("button", has_text="Разрешить").first.click()
        approved = _wait_row(live, approvals_t, allow_id, lambda r: r.get("status") == "approved",
                             "решение «Разрешить» не записано")
        assert approved["decided_by"], "решение записано без автора"
        assert approved["decided_at"] is not None

        page.wait_for_selector("text=открыть внешний сайт", timeout=15000)
        deny_card = page.locator("section, article, div").filter(has_text="открыть внешний сайт").last
        deny_card.locator("button", has_text="Отклонить").first.click()
        rejected = _wait_row(live, approvals_t, deny_id, lambda r: r.get("status") == "rejected",
                             "решение «Отклонить» не записано")
        browser.close()

    # анти-реплей: повторное решение по уже решённым записям ничего не меняет
    again = _call(live, lambda: live.svc.approvals.decide(deny_id, True, "attacker"))
    assert again["status"] == "rejected" and again["decided_by"] == rejected["decided_by"]
    # использованное подтверждение нельзя предъявить дважды
    assert _call(live, lambda: live.svc.approvals.consume(allow_id, kind="terminal.run",
                                                          preview="ls ./artifacts")) is True
    assert _call(live, lambda: live.svc.approvals.consume(allow_id, kind="terminal.run",
                                                          preview="ls ./artifacts")) is False
    # и подтверждение одного действия не годится для другого
    assert _row(live, approvals_t, allow_id)["status"] == "consumed"
    assert errors == [], errors


def test_unauthenticated_action_is_refused(live):  # noqa: F811
    """Тот же запрос без сессии владельца не меняет ничего (не только UI прячет кнопку)."""
    import httpx

    task_id = _new_task(live, "Без сессии")
    r = httpx.post(f"{live.url}/api/tasks/{task_id}/run", timeout=10)
    assert r.status_code in (401, 403), r.text
    assert _row(live, tasks_t, task_id)["status"] == "draft", "неаутентифицированный запрос изменил состояние"
