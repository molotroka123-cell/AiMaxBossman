"""P1-находки аудита Command Center UX (audit-07-cc-ux.md).

A7-01: командная строка «Главной» теряла набранное поручение, когда агентов
       не ровно один: тост «Выберите агента» + уход на страницу «Агенты».
A7-02: ghost-approval после остановки: остановленная задача оставляла
       подтверждение в очереди, а решение по нему поднимало мёртвую задачу
       обратно в `queued` — воркер её уже никогда не берёт.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import approvals as approvals_t, tasks as tasks_t, tool_calls as tool_calls_t
from bcc.providers import ChatResult, ToolCall
from bcc.tools import REGISTRY, ToolResult, ToolSpec

from .browser_support import chromium_available, reason as browser_reason
from .conftest import FakeAdapter, wait_for
from .helpers import make_stack
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401


# ------------------------------------------------------------------ A7-02

def _install_ask_tool(calls: list) -> None:
    """Инструмент, который всегда просит подтверждения владельца."""
    async def handler(args, ctx):
        calls.append(args)
        return ToolResult(content="exit_code=0", one_line="terminal: ok")
    REGISTRY.register(ToolSpec(name="terminal.run", description="тестовый инструмент",
                               handler=handler, input_schema={"command": {"type": "string"}},
                               permission="terminal.run", default_effect="ask"))


class _AskThenText(FakeAdapter):
    """Первый шаг — вызов инструмента, дальше обычный текст."""

    async def chat(self, model, messages, **kw):
        self.calls += 1
        if self.calls == 1:
            return ChatResult(text="", tokens_in=5, tokens_out=2, finish="tool_calls",
                              model=model,
                              tool_calls=[ToolCall(id="call_1", name="terminal_run",
                                                   arguments={"command": "git push"},
                                                   raw_arguments='{"command": "git push"}')])
        return ChatResult(text="готово", tokens_in=5, tokens_out=3, model=model)


@pytest.fixture
def ask_tool():
    seen: list = []
    before = set(REGISTRY.names())
    _install_ask_tool(seen)
    yield seen
    for name in set(REGISTRY.names()) - before:
        REGISTRY.unregister(name)


async def _park_on_approval(env, adapter=None):
    """Довести задачу до `waiting_approval` с припаркованным вызовом инструмента."""
    adapter = adapter or _AskThenText()
    stack = await make_stack(env.client, max_steps=4)
    env.svc.registry.adapter_factory = lambda m, p: adapter
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"tools": ["terminal.run"]})
    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    try:
        async def parked():
            t = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()
            return t if t["task"]["status"] == "waiting_approval" else None
        await wait_for(parked, timeout=6.0)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    return stack["task"]["id"]


async def _rows(env, table, **where):
    async with env.svc.db.session() as s:
        stmt = sa.select(table)
        for key, value in where.items():
            stmt = stmt.where(table.c[key] == value)
        return [dict(r._mapping) for r in (await s.execute(stmt)).fetchall()]


async def test_stop_closes_the_parked_approval(env, ask_tool):
    """Репродукция A7-02: остановленная задача оставляла решение в очереди."""
    task_id = await _park_on_approval(env)
    assert (await env.client.get("/api/approvals")).json()

    await env.client.post(f"/api/tasks/{task_id}/stop")

    assert (await env.client.get("/api/approvals")).json() == []
    calls = await _rows(env, tool_calls_t, task_id=task_id)
    assert [c["status"] for c in calls] == ["rejected"]
    approvals = await _rows(env, approvals_t, task_id=task_id)
    assert [a["status"] for a in approvals] == ["rejected"]


async def test_deciding_an_approval_does_not_revive_a_stopped_task(env, ask_tool):
    """Вторая половина A7-02: решение поднимало остановленную задачу в очередь."""
    task_id = await _park_on_approval(env)
    await env.client.post(f"/api/tasks/{task_id}/stop")

    # Гонка «решение уже в полёте, пока stop() гасит задачу»: подтверждение
    # припарковано заново вручную, потому что штатный stop его уже закрыл.
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tool_calls_t).where(tool_calls_t.c.task_id == task_id)
                        .values(status="pending_approval"))
        await s.execute(sa.update(approvals_t).where(approvals_t.c.task_id == task_id)
                        .values(status="approved"))
        await s.commit()
    approval_id = (await _rows(env, approvals_t, task_id=task_id))[0]["id"]

    await env.svc.engine.on_approval_decided(int(approval_id))

    task = (await _rows(env, tasks_t, id=task_id))[0]
    assert task["status"] == "stopped"
    assert ask_tool == []


async def test_an_approved_tool_still_runs_on_a_live_task(env, ask_tool):
    """Положительный контроль: обычный путь подтверждения не сломан."""
    task_id = await _park_on_approval(env)
    approval = (await env.client.get("/api/approvals")).json()[0]
    await env.client.post(f"/api/approvals/{approval['id']}",
                          json={"approve": True, "by": "тест"})

    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    try:
        async def done():
            t = (await env.client.get(f"/api/tasks/{task_id}")).json()
            return t if t["task"]["status"] in ("completed", "failed") else None
        task = await wait_for(done, timeout=8.0)
    finally:
        worker.cancel(); watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)

    assert task["task"]["status"] == "completed"
    assert ask_tool == [{"command": "git push"}]


async def test_stopping_a_mission_closes_the_approvals_of_its_children(env, ask_tool):
    """Тот же путь через «Остановить» миссию (missions.py зовёт engine.stop)."""
    task_id = await _park_on_approval(env)
    mission = (await env.client.post("/api/missions", json={"title": "м"})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id)
                        .values(mission_id=mission["id"]))
        await s.commit()

    await env.client.post(f"/api/missions/{mission['id']}/stop")

    assert (await env.client.get("/api/approvals")).json() == []
    assert (await _rows(env, tasks_t, id=task_id))[0]["status"] == "stopped"


# ------------------------------------------------------------------ A7-01

needs_browser = pytest.mark.skipif(not chromium_available(), reason=browser_reason())

# Текст заведомо не про видео: routeVideoRequest не должен перехватить поручение.
ORDER = ('Собери сводку по последним ошибкам в журнале и предложи, '
         'что чинить первым — это длинное поручение, которое обидно набирать заново')


def _srv_client(srv) -> httpx.Client:
    """Прямой клиент к живому серверу мимо прокси из окружения (см. loopback_get)."""
    return httpx.Client(trust_env=False, timeout=10.0, base_url=srv.url,
                        headers={"X-BCC-Token": srv.svc.auth.token})


def _agents(srv, names: list[str]) -> list[int]:
    with _srv_client(srv) as api:
        # MF-031: executable owner submissions now validate model configuration.
        # These are UI selection tests (workers off), not model-backed acceptance.
        provider = api.post("/api/providers", json={
            "name": "UI selection fixture", "kind": "openai_compat",
            "base_url": "http://127.0.0.1:1/v1"}).json()
        model = api.post("/api/models", json={
            "provider_id": provider["id"], "name": "ui-selection-fixture"}).json()
        return [api.post("/api/agents", json={"name": n, "model_id": model["id"]}).json()["id"]
                for n in names]


def _home(pw, srv, errors: list[str]):
    browser = _launch(pw)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    _login(page, srv)
    page.evaluate("() => { location.hash = '#/home-v3'; }")
    page.wait_for_selector(".bx-command-input", timeout=15000)
    return browser, page


def _type_order(page, text: str = ORDER) -> None:
    page.fill(".bx-command-input", text)
    page.dispatch_event(".bx-command-input", "input")


def _start(page) -> None:
    page.click(".bx-command .bx-btn-primary")


@pytest.mark.timeout(180)
@needs_browser
def test_two_agents_keep_the_typed_order_on_the_home_page(live):  # noqa: F811
    """Репродукция A7-01: при двух агентах ЗАПУСТИТЬ уводил со страницы,
    а набранное поручение исчезало вместе с пересозданной textarea."""
    from playwright.sync_api import sync_playwright

    ids = _agents(live, ["первый", "второй"])
    errors: list[str] = []
    with sync_playwright() as pw:
        browser, page = _home(pw, live, errors)
        try:
            _type_order(page)
            _start(page)
            page.wait_for_selector(".toast-warn .toast-msg", timeout=20000)

            assert "home-v3" in page.evaluate("() => location.hash")
            assert page.input_value(".bx-command-input") == ORDER

            # Выбор агента есть прямо в командной строке — уходить некуда.
            page.select_option(".bx-command select", str(ids[1]))
            _start(page)
            page.wait_for_function(
                "() => document.querySelector('.bx-command-input').value === ''",
                timeout=15000)
        finally:
            browser.close()

    with _srv_client(live) as api:
        tasks = api.get("/api/tasks").json()
    rows = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
    assert [t["agent_id"] for t in rows] == [ids[1]]
    assert errors == []


@pytest.mark.timeout(180)
@needs_browser
def test_without_agents_the_draft_survives_the_trip_to_the_agents_page(live):  # noqa: F811
    """Ноль агентов: уход на «Агенты» оправдан, но черновик обязан вернуться."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser, page = _home(pw, live, errors)
        try:
            _type_order(page)
            _start(page)
            page.wait_for_function("() => location.hash.includes('agents')", timeout=20000)

            page.evaluate("() => { location.hash = '#/home-v3'; }")
            page.wait_for_selector(".bx-command-input", timeout=15000)
            assert page.input_value(".bx-command-input") == ORDER
        finally:
            browser.close()
    assert errors == []


@pytest.mark.timeout(180)
@needs_browser
def test_a_single_agent_still_starts_without_any_picking(live):  # noqa: F811
    """Положительный контроль: единственный агент подставляется как и раньше."""
    from playwright.sync_api import sync_playwright

    ids = _agents(live, ["единственный"])
    errors: list[str] = []
    with sync_playwright() as pw:
        browser, page = _home(pw, live, errors)
        try:
            assert page.query_selector(".bx-command select") is None
            _type_order(page)
            _start(page)
            page.wait_for_function(
                "() => document.querySelector('.bx-command-input').value === ''",
                timeout=15000)
        finally:
            browser.close()

    with _srv_client(live) as api:
        tasks = api.get("/api/tasks").json()
    rows = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
    assert [t["agent_id"] for t in rows] == [ids[0]]
    assert errors == []
