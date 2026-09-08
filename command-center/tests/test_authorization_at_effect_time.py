"""Авторизация проверяется В МОМЕНТ ЭФФЕКТА, а не в момент одобрения.

Аудит воспроизвёл на default-ветке dispatch после ASK→approve, когда между
одобрением и возобновлением (1) через API добавили DENY-правило, (2) инструмент
сняли с агента, (3) одобрение отозвали в await-интервале перед dispatch. Здесь
те же три сценария — на ТЕКУЩЕМ кандидате, через настоящие Services/SQLite/
worker и настоящий API, с безвредным исполнителем-счётчиком.

Сценарий 3 (отзыв до возобновления) уже закрыт в test_fable_approval_revocation.
Здесь — его гоночный вариант: отзыв попадает в окно МЕЖДУ чтением статуса
одобрения и dispatch'ем. Барьеры детерминированные (asyncio.Event), без sleep.
"""
from __future__ import annotations

import asyncio

import sqlalchemy as sa

from bcc.db import approvals as approvals_t, tool_calls as tool_calls_t

from .test_v21_tool_loop import (FINISHED, ToolAdapter, _install, _run_task,  # noqa: F401
                                 _stack_with_tools, clean_registry)


async def _rows(env, table, **where):
    async with env.svc.db.session() as s:
        stmt = sa.select(table)
        for k, v in where.items():
            stmt = stmt.where(getattr(table.c, k) == v)
        return [dict(r._mapping) for r in (await s.execute(stmt)).fetchall()]


async def _park_on_approval(env, calls):
    """ASK-инструмент просится, run паркуется на одобрении. Возвращает (task_id, approval_id, agent_id)."""
    _install("terminal.run", calls=calls, permission="terminal.run", default_effect="ask")
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "git push"}), ("text", "ок")])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    task_id = stack["task"]["id"]
    assert await _run_task(env, task_id) == "waiting_approval"
    aid = (await _rows(env, approvals_t))[0]["id"]
    return task_id, aid, stack["agent"]["id"], adapter


async def _approve(env, aid):
    r = await env.client.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner"})
    assert r.status_code == 200 and r.json()["status"] == "approved"


# --------------------------------------------------- 1. DENY, добавленный после одобрения

async def test_a_deny_rule_added_after_approval_blocks_the_resumed_effect(env):
    calls: list = []
    task_id, aid, agent_id, adapter = await _park_on_approval(env, calls)
    await _approve(env, aid)
    # Владелец, уже одобрив, запрещает ровно это действие правилом политики.
    r = await env.client.patch(f"/api/agents/{agent_id}", json={"permissions": {
        "terminal.run": True,
        "tool_rules": [{"tool": "terminal.run", "resource": "git push*", "effect": "deny",
                        "reason": "push запрещён владельцем после одобрения"}]}})
    assert r.status_code == 200

    status = await _run_task(env, task_id, until=FINISHED)
    assert calls == [], "DENY, принятый до dispatch, обязан предотвратить эффект"
    rows = await _rows(env, tool_calls_t, task_id=task_id)
    assert [x["status"] for x in rows] == ["rejected"], rows
    assert status != "completed"


# --------------------------------------------------- 2. инструмент снят с агента

async def test_a_tool_withdrawn_from_the_agent_after_approval_is_not_executed(env):
    calls: list = []
    task_id, aid, agent_id, adapter = await _park_on_approval(env, calls)
    await _approve(env, aid)
    # Инструмент больше не выдан этому агенту.
    r = await env.client.patch(f"/api/agents/{agent_id}", json={"tools": []})
    assert r.status_code == 200

    status = await _run_task(env, task_id, until=FINISHED)
    assert calls == [], "снятый с агента инструмент не исполняется по старому одобрению"
    rows = await _rows(env, tool_calls_t, task_id=task_id)
    assert [x["status"] for x in rows] == ["rejected"], rows
    assert status != "completed"


# --------------------------------------------------- 3. отзыв внутри окна перед dispatch

async def test_a_revoke_confirmed_before_the_execution_boundary_wins_the_race(env, monkeypatch):
    """Отзыв, подтверждённый ДО границы «принято к исполнению», предотвращает эффект.

    Окно: движок уже прочитал approved, ещё не принял к исполнению. Барьеры —
    Event'ы: тест ловит движок в окне, отзывает, отпускает. effect_count == 0.
    """
    calls: list = []
    task_id, aid, agent_id, adapter = await _park_on_approval(env, calls)
    await _approve(env, aid)

    engine = env.svc.engine
    in_window = asyncio.Event()
    proceed = asyncio.Event()
    original = engine._tool_call_status          # вызывается после чтения approved, до dispatch

    async def held(run_id, call_id):
        result = await original(run_id, call_id)
        in_window.set()
        await proceed.wait()
        return result

    monkeypatch.setattr(engine, "_tool_call_status", held)

    runner = asyncio.create_task(_run_task(env, task_id, until=FINISHED, timeout=8.0))
    await asyncio.wait_for(in_window.wait(), timeout=5.0)
    # Движок стоит в окне. Отзыв подтверждён.
    r = await env.client.post(f"/api/approvals/{aid}/revoke", json={"by": "owner"})
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    proceed.set()
    status = await runner

    assert calls == [], "отзыв до границы исполнения обязан выиграть гонку: effect_count == 0"
    rows = await _rows(env, tool_calls_t, task_id=task_id)
    assert [x["status"] for x in rows] == ["rejected"], rows
    assert (await _rows(env, approvals_t, id=aid))[0]["status"] == "revoked"
    assert status != "completed"


# --------------------------------------------------- контроль: обычное одобрение всё ещё исполняется

async def test_an_unchanged_approval_still_executes_exactly_once_and_is_consumed(env):
    """Отрицательный контроль ужесточения: законный путь не сломан."""
    calls: list = []
    task_id, aid, agent_id, adapter = await _park_on_approval(env, calls)
    await _approve(env, aid)
    status = await _run_task(env, task_id, until=FINISHED)
    assert len(calls) == 1
    rows = await _rows(env, tool_calls_t, task_id=task_id)
    assert [x["status"] for x in rows] == ["executed"], rows
    # Одобрение использовано ровно один раз: повторно предъявить его нельзя.
    assert (await _rows(env, approvals_t, id=aid))[0]["status"] == "consumed"
    assert status == "completed"
