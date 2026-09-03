"""V2 closure — перезапуск менеджера не теряет работу и не делает её дважды.

Сценарий приёмки: работа идёт → процесс останавливают на середине (аренда прогона
остаётся в базе) → процесс поднимают заново на тех же данных → он сам продолжает
ТУ ЖЕ работу (тот же task_id и тот же run_id, без «продолжить» от владельца) →
завершает её ровно один раз. Отдельно: подтверждение, уже использованное до
перезапуска, после перезапуска использовать нельзя.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa

from bcc.db import (agents as agents_t, approvals as approvals_t, task_runs as runs_t,
                    tasks as tasks_t, utcnow)

from .conftest import FakeAdapter, make_settings, start_app

pytestmark = pytest.mark.timeout(120)


async def _stack(svc):
    """Провайдер + модель + агент без сети (FakeAdapter)."""
    provider = await svc.registry.create_provider("fake", "openai_compat", "http://127.0.0.1:1", "sk-x")
    model = await svc.registry.create_model(provider_id=provider["id"], name="fake-model",
                                            alias="fake-model", kind="local")
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(agents_t).values(
            name="Исполнитель", role="worker", system_prompt="Отвечай коротко.",
            model_id=model["id"], max_steps=3, tools=[], permissions={},
            enabled=True, created_at=utcnow()))
        agent_id = int(res.inserted_primary_key[0])
        await s.commit()
    return model, {"id": agent_id}


async def _rows(svc, table, **where):
    async with svc.db.session() as s:
        stmt = sa.select(table)
        for k, v in where.items():
            stmt = stmt.where(getattr(table.c, k) == v)
        res = await s.execute(stmt)
        return [dict(r._mapping) for r in res.fetchall()]


async def _wait(fn, what: str, timeout: float = 20.0, interval: float = 0.15):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        last = await fn()
        if last:
            return last
        await asyncio.sleep(interval)
    raise AssertionError(f"не дождались: {what} (последнее значение {last})")


@pytest.mark.asyncio
async def test_restart_resumes_same_run_exactly_once(tmp_path):
    settings = make_settings(tmp_path)
    calls: list[str] = []

    # --- процесс №1: задача взята в работу и «умерла» вместе с процессом
    app1, svc1 = await start_app(settings, start_workers=False,
                                 adapter_factory=lambda *a, **k: FakeAdapter("готово"))
    try:
        model, agent = await _stack(svc1)
        async with svc1.db.session() as s:
            res = await s.execute(sa.insert(tasks_t).values(
                title="Долгая миссия", prompt="сделай отчёт", agent_id=agent["id"],
                status="running", priority=5, max_retries=2,
                created_at=utcnow(), updated_at=utcnow()))
            task_id = int(res.inserted_primary_key[0])
            res = await s.execute(sa.insert(runs_t).values(
                task_id=task_id, attempt=1, status="running",
                # аренда уже истекла — ровно то, что видит новый процесс после падения
                worker_lease_until=utcnow() - timedelta(seconds=120),
                checkpoint={"step": 2, "note": "половина сделана", "messages": []},
                model_alias="fake-model", started_at=utcnow() - timedelta(seconds=300)))
            run_id = int(res.inserted_primary_key[0])
            await s.commit()
    finally:
        await svc1.stop()

    # --- процесс №2: те же данные, тот же порт не нужен — важно состояние на диске
    app2, svc2 = await start_app(settings, start_workers=True,
                                 adapter_factory=lambda *a, **k: FakeAdapter("готово"))
    try:
        # тот же прогон вернулся в работу сам, без вмешательства владельца
        run = await _wait(lambda: _one(_rows(svc2, runs_t, id=run_id), lambda r: r["status"] != "running"),
                          "прогон не подобран после перезапуска")
        assert run["task_id"] == task_id, "перезапуск потерял привязку к задаче"
        assert run["attempt"] >= 2, "восстановление не увеличило номер попытки"

        done = await _wait(lambda: _one(_rows(svc2, tasks_t, id=task_id),
                                        lambda t: t["status"] in {"completed", "failed"}),
                           "задача не завершилась после перезапуска")
        assert done["status"] == "completed", done

        runs = await _rows(svc2, runs_t, task_id=task_id)
        assert len(runs) == 1, f"перезапуск создал дубликат прогона: {runs}"
        completed = [r for r in runs if r["status"] == "completed"]
        assert len(completed) == 1, f"задача завершена дважды: {completed}"
        assert completed[0]["id"] == run_id, "продолжен другой прогон, а не тот же"
    finally:
        await svc2.stop()

    assert calls == []  # никаких «продолжить» от владельца не понадобилось


async def _one(rows_coro, predicate):
    rows = await rows_coro
    for r in rows:
        if predicate(r):
            return r
    return None


@pytest.mark.asyncio
async def test_consumed_approval_cannot_be_replayed_after_restart(tmp_path):
    """Использованное подтверждение остаётся использованным и в новом процессе."""
    settings = make_settings(tmp_path)

    app1, svc1 = await start_app(settings, start_workers=False)
    try:
        row = await svc1.approvals.create("terminal.run", "ls ./artifacts")
        approved = await svc1.approvals.decide(row["id"], True, "human:owner")
        assert approved["status"] == "approved"
        assert await svc1.approvals.consume(row["id"], kind="terminal.run", preview="ls ./artifacts") is True
    finally:
        await svc1.stop()

    app2, svc2 = await start_app(settings, start_workers=False)
    try:
        assert await svc2.approvals.consume(row["id"], kind="terminal.run",
                                            preview="ls ./artifacts") is False
        after = await _rows(svc2, approvals_t, id=row["id"])
        assert after[0]["status"] == "consumed"
        # и решение владельца не переигрывается новым процессом
        again = await svc2.approvals.decide(row["id"], False, "attacker")
        assert again["status"] == "consumed" and again["decided_by"] == "human:owner"
    finally:
        await svc2.stop()
