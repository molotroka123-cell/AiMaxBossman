"""MF-031 — владелец видит исполнителя ДО отправки, и предпросмотр не врёт.

Приём и раньше был закрыт наглухо: задача без исполнителя не создавалась. Но
узнать об этом можно было только после отправки — в журнале владельца осталось
тринадцать заблокированных задач подряд, каждая новая попытка угадать, чего не
хватает.

Опасность предпросмотра ровно одна: он может разойтись с настоящим приёмом. Тогда
владельцу показывают зелёное, а создание отказывает — это хуже, чем не
показывать ничего. Поэтому здесь проверяется не «ручка отвечает», а СОГЛАСИЕ:

    предпросмотр ok=true   → создание проходит и берёт ТОГО ЖЕ агента
    предпросмотр ok=false  → создание отказывает, и задачи не появляется
    предпросмотр           → не пишет НИЧЕГО
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import tasks as tasks_t

from .helpers import make_stack

PROMPT = "Посчитай 17*23. Не используй инструменты."


async def _preflight(env, **kw):
    return await env.client.post("/api/tasks/preflight",
                                 json={"prompt": PROMPT, **kw})


async def _tasks(env):
    return (await env.client.get("/api/tasks")).json()


async def test_preflight_names_the_agent_and_model_that_would_run(env):
    stack = await make_stack(env.client)
    body = (await _preflight(env)).json()
    assert body["ok"] is True
    assert body["mode"] == "auto", "автовыбор обязан быть назван автовыбором"
    assert body["agent"]["id"] == stack["agent"]["id"]
    assert body["agent"]["name"]
    assert body["model"] and body["model"]["id"] is not None
    assert "health" in body["model"]


async def test_explicit_choice_is_reported_as_explicit(env):
    stack = await make_stack(env.client)
    body = (await _preflight(env, agent_id=stack["agent"]["id"])).json()
    assert body["ok"] is True and body["mode"] == "explicit"
    assert body["agent"]["id"] == stack["agent"]["id"]


async def test_preflight_agrees_with_creation_when_it_says_yes(env):
    """Согласие в положительную сторону: показали агента — он и выполнит."""
    await make_stack(env.client)
    preview = (await _preflight(env)).json()
    assert preview["ok"] is True
    created = await env.client.post("/api/tasks", json={"prompt": PROMPT, "run_now": True})
    assert created.status_code == 200, created.text
    assert created.json()["task"]["agent_id"] == preview["agent"]["id"]


async def test_preflight_agrees_with_creation_when_it_says_no(env):
    """Негативный контроль. Без исполнителя предпросмотр обязан отказать теми же
    словами, что и создание, — иначе владелец получит два разных ответа."""
    body = (await _preflight(env)).json()
    assert body["ok"] is False
    assert body["agent"] is None and body["model"] is None
    assert body["reason"], "отказ обязан быть назван"
    assert body["hint"], "отказ обязан говорить, что делать"

    created = await env.client.post("/api/tasks", json={"prompt": PROMPT, "run_now": True})
    assert created.status_code == 409, created.text
    assert created.json()["error"]["message"] == body["reason"]


async def test_a_disabled_agent_is_refused_by_the_preview_too(env):
    stack = await make_stack(env.client)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"enabled": False})
    body = (await _preflight(env, agent_id=stack["agent"]["id"])).json()
    assert body["ok"] is False and body["reason"]


async def test_preflight_writes_nothing(env):
    """Предпросмотр — это чтение. Задача, созданная «на посмотреть», и есть тот
    самый заблокированный хвост в журнале, ради которого всё это делается."""
    await make_stack(env.client)
    before = await _tasks(env)
    for _ in range(3):
        await _preflight(env)
    await _preflight(env, agent_id=999999)
    assert await _tasks(env) == before
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(sa.func.count()).select_from(tasks_t))).scalar()
    assert rows == len(before)


async def test_preflight_does_not_edit_the_agent_it_picks(env):
    """Выбор не выдаёт прав и не правит агента — ни одного побочного эффекта."""
    await make_stack(env.client)
    before = (await env.client.get("/api/agents")).json()
    assert (await _preflight(env)).json()["ok"] is True
    assert (await env.client.get("/api/agents")).json() == before
