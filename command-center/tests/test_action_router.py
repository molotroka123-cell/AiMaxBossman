"""BCC-V2-UNIVERSAL-ACTION-EXECUTION-P1-001 — Action Capability Router (MODULE 1: browser).

Продолжение регрессии BCC-V2-SESSION-20783913FA36-P1-FIX-001. action_gate.py
(предыдущий патч) не даёт ложному текстовому «успеху» превратиться в completed,
но сам по себе не заставляет систему РЕАЛЬНО выполнить действие: агент из
сессии 20783913fa36 отвечал текстом именно потому, что `agents.tools` пуст —
запрошенное действие никогда не попадало в tool_schemas модели.

TEST-CLASSIFY  — детерминированный классификатор намерения (без движка/БД):
                 та же формулировка, что в реальной сессии, классифицируется
                 как BROWSER_ACTION с доменом youtube.com; информационный
                 запрос — нет.
TEST-ATTACH    — before_run прикрепляет уже готовые инструменты
                 tools_browser.* к задаче ДАЖЕ когда у агента (как в MVP по
                 умолчанию) инструментов вообще нет — без этого «Модель как
                 планировщик, не исполнитель» (п.2 спецификации) невозможно:
                 исполнять нечем.
TEST-NO-FALSE-SUCCESS — сильный инвариант: реальный tool-loop, реальный
                 Chromium, модель РЕАЛЬНО вызывает browser.open/type/click —
                 но финальный текст утверждает успех о YouTube, а наблюдаемая
                 (тестовая, локальная) страница домен youtube.com не содержит.
                 Задача НЕ должна стать completed: `_has_any_tool_call` в
                 action_gate.py — намеренно слабая проверка («хоть что-то из
                 инструментального пути»), а не доказательство, что ИМЕННО
                 запрошенное состояние достигнуто; здесь это видно только
                 благодаря review_gate + verification (F-012), которые
                 action_router подключает автоматически.
TEST-VERIFIED-COMPLETION — тот же реальный tool-loop, но домен, выведенный из
                 текста задачи, СОВПАДАЕТ с реально достигнутой (тестовой)
                 страницей — задача становится completed только ПОСЛЕ того,
                 как свежий снимок браузера подтвердил URL.
"""
from __future__ import annotations

import functools
import http.server
import socketserver
import threading

import pytest
import sqlalchemy as sa

from bcc import db as dbm
from bcc.features import action_router
from bcc.providers import ChatResult, ToolCall

from .conftest import FakeAdapter
from .helpers import make_stack


# --------------------------------------------------------------- TEST-CLASSIFY

REAL_PROMPT = "Открой на моём компьютере в браузере YouTube и включи Never Gonna Give You Up"


def test_real_session_prompt_classifies_as_browser_action():
    assert action_router.classify(REAL_PROMPT) == action_router.CAPABILITY_BROWSER
    assert action_router.target_domain(REAL_PROMPT) == "youtube.com"


def test_english_explicit_domain_classifies_and_extracts_it():
    assert action_router.classify("Open example.com in the browser") == \
        action_router.CAPABILITY_BROWSER
    assert action_router.target_domain("Open example.com in the browser") == "example.com"


def test_informational_prompt_is_not_classified_as_action():
    # Страховка от гиперкоррекции (тот же принцип, что TEST2 в test_action_gate.py):
    # обычный информационный запрос не должен получать браузерные инструменты.
    assert action_router.classify("Сделай краткое содержание статьи") is None
    assert action_router.classify("Explain what this function does") is None


# ----------------------------------------------------------- helpers (engine)

async def _run_once(env):
    for _ in range(10):
        run_id = await env.svc.engine.claim()
        if run_id is None:
            return
        await env.svc.engine.execute(run_id)


class ToolAdapter(FakeAdapter):
    """Тот же харнесс, что tests/test_v21_tool_loop.py: скриптованная модель,
    которая на очередном шаге либо просит инструмент, либо отвечает текстом."""

    def __init__(self, script, **kw):
        super().__init__(**kw)
        self.script = list(script)

    async def chat(self, model, messages, **kw):
        self.calls += 1
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if step[0] == "tool":
            import json as _json
            return ChatResult(text="", tokens_in=5, tokens_out=2, finish="tool_calls",
                              model=model,
                              tool_calls=[ToolCall(
                                  id=f"call_{self.calls}", name=step[1], arguments=step[2],
                                  raw_arguments=_json.dumps(step[2], ensure_ascii=False))])
        return ChatResult(text=step[1], tokens_in=5, tokens_out=3, model=model)


# ------------------------------------------------------------ TEST-ATTACH

async def test_before_run_attaches_browser_tools_when_agent_has_none(env):
    """Точный сценарий 20783913fa36: agent.tools=[] (дефолт MVP). Раньше
    модель не видела ни одного инструмента вообще; теперь before_run
    прикрепляет tools_browser.* к ЭТОМУ run'у по классификации текста задачи."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("не важно для этого теста")
    stack = await make_stack(env.client, prompt=REAL_PROMPT)
    # agent.tools пуст по умолчанию — явно проверим это допущение теста.
    assert not (stack["agent"].get("tools") or [])

    run_id = await env.svc.engine.claim()
    assert run_id is not None
    async with env.svc.db.session() as s:
        task = dict((await s.execute(sa.select(dbm.tasks).where(
            dbm.tasks.c.id == stack["task"]["id"]))).first()._mapping)
        run = dict((await s.execute(sa.select(dbm.task_runs).where(
            dbm.task_runs.c.id == run_id))).first()._mapping)

    hook = await action_router._before_run(env.svc)
    await hook(task, run)

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(dbm.tasks.c.meta).where(
            dbm.tasks.c.id == stack["task"]["id"]))).first()
    meta = row._mapping["meta"]
    assert set(action_router.BROWSER_TOOLS) <= set(meta["allowed_tools"])
    assert meta["review"]["evidence"] == [
        {"kind": "browser", "target": "session", "expect": {"url_contains": "youtube.com"}}]
    assert meta["action_router"]["capability"] == "BROWSER_ACTION"


async def test_explicit_allowed_tools_are_not_overridden(env):
    """Владелец/скилл уже сконфигурировал задачу явно — роутер не должен
    молча переписывать её решение (тот же принцип приоритета, что
    bcc.tools.allowed_tools_for уже соблюдает)."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("не важно")
    stack = await make_stack(env.client, prompt=REAL_PROMPT)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(dbm.tasks).where(dbm.tasks.c.id == stack["task"]["id"]).values(
            meta={"allowed_tools": ["terminal.run"]}))
        await s.commit()
        task = dict((await s.execute(sa.select(dbm.tasks).where(
            dbm.tasks.c.id == stack["task"]["id"]))).first()._mapping)
        run = dict((await s.execute(sa.select(dbm.task_runs).where(
            dbm.task_runs.c.task_id == stack["task"]["id"]))).first()._mapping)

    hook = await action_router._before_run(env.svc)
    await hook(task, run)

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(dbm.tasks.c.meta).where(
            dbm.tasks.c.id == stack["task"]["id"]))).first()
    assert row._mapping["meta"]["allowed_tools"] == ["terminal.run"]


# ------------------------------------------------- real Chromium E2E (skip if absent)

def _has_chromium():
    from .browser_support import chromium_available
    return chromium_available()


@pytest.fixture
def _search_site(tmp_path):
    """Мини-сайт с полем поиска и «результатом», который ведёт на watch-страницу —
    тот же наблюдаемый паттерн (search box → submit/click → страница результата),
    что описан в спецификации (OBSERVE→ACT→OBSERVE→VERIFY), без сети наружу."""
    (tmp_path / "p1.html").write_text(
        "<html><body><input id='q'><a id='go' href='watch.html'>Найти</a></body></html>",
        encoding="utf-8")
    (tmp_path / "watch.html").write_text(
        "<html><body><h1>Never Gonna Give You Up</h1><p id='player'>playing</p></body></html>",
        encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()


@pytest.fixture(autouse=True)
def _allow_private_browser_targets(monkeypatch):
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")


def _script(port):
    return [
        ("tool", "browser_open", {"url": f"http://127.0.0.1:{port}/p1.html"}),
        ("tool", "browser_type", {"selector": "#q", "text": "Never Gonna Give You Up"}),
        ("tool", "browser_click", {"selector": "#go"}),
        ("text", "Готово: открыл YouTube и включил Never Gonna Give You Up."),
    ]


@pytest.mark.skipif(not _has_chromium(), reason="Chromium не предустановлен")
async def test_real_tool_calls_without_matching_verification_do_not_complete(
        env, _search_site):
    """Сильный инвариант спецификации:
    SIDE_EFFECT_REQUIRED && VERIFIED_SIDE_EFFECT == false → COMPLETED == false.

    Модель РЕАЛЬНО прошла open→type→click в настоящем Chromium (не заглушка) и
    текстом заявила успех про YouTube — но реально достигнутая страница
    youtube.com не содержит (это тестовый локальный сайт), а домен выведен
    роутером из текста задачи как youtube.com. `_has_any_tool_call` дал бы
    NOT_APPLICABLE (инструмент реально вызывался), но review_gate поверх
    свежего снимка браузера обязан вернуть FAILED/UNVERIFIED — задача не
    должна стать completed."""
    port = _search_site
    adapter = ToolAdapter(_script(port))
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await make_stack(env.client, prompt=REAL_PROMPT, max_steps=6)
    # Владелец заранее выдал агенту право управлять браузером (browser.control) —
    # штатный путь AUTO вместо ASK на каждый клик/ввод; сама политика ASK/DENY
    # проверяется отдельно в tests/test_v21_tool_loop.py и здесь не дублируется.
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"browser.control": True}})

    await _run_once(env)

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] != "completed"

    async with env.svc.db.session() as s:
        calls = (await s.execute(sa.select(dbm.tool_calls).where(
            dbm.tool_calls.c.task_id == stack["task"]["id"]))).fetchall()
    executed = {c._mapping["tool"] for c in calls}
    assert {"browser.open", "browser.type", "browser.click"} <= executed


@pytest.mark.skipif(not _has_chromium(), reason="Chromium не предустановлен")
async def test_real_tool_calls_with_matching_verification_complete(
        env, _search_site, monkeypatch):
    """Позитивный путь того же инварианта: когда наблюдённое состояние
    браузера ДЕЙСТВИТЕЛЬНО совпадает с ожиданием, выведенным из текста
    задачи, задача становится completed — не раньше свежей проверки."""
    port = _search_site
    # Детерминированный, закрытый словарь известных доменов (см. докстринг
    # action_router.py) расширяем тестовой записью на локальный сайт вместо
    # похода в реальный интернет — тот же механизм, что и youtube.com в
    # проде, просто указывает на тестовую watch-страницу.
    monkeypatch.setattr(action_router, "_KNOWN_DOMAINS",
                        ((__import__("re").compile(r"(?iu)смотритест"),
                          f"127.0.0.1:{port}/watch"),))

    prompt = "Открой в браузере смотритест и включи ролик"
    assert action_router.classify(prompt) == action_router.CAPABILITY_BROWSER
    assert action_router.target_domain(prompt) == f"127.0.0.1:{port}/watch"

    adapter = ToolAdapter([
        ("tool", "browser_open", {"url": f"http://127.0.0.1:{port}/p1.html"}),
        ("tool", "browser_type", {"selector": "#q", "text": "ролик"}),
        ("tool", "browser_click", {"selector": "#go"}),
        ("text", "Готово."),
    ])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await make_stack(env.client, prompt=prompt, max_steps=6)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"browser.control": True}})

    await _run_once(env)

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "completed"
