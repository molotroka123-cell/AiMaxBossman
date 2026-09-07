"""Терминальный прогон неизменяем — на КАЖДОЙ границе, а не в отдельных вызовах.

Канареечная улика рождается ровно один раз: в момент, когда член когорты дошёл
до терминального исхода. Вся власть этой улики держится на одном допущении —
что терминальный исход больше не меняется. Если прогон, завершившийся
`completed`, можно позже переписать в `failed` (или наоборот, или вернуть в
`running`), то улику можно рассогласовать с фактом задним числом, и «проверено»
перестаёт что-либо значить.

Запрет поэтому стоит в базе, а не в вызывающем коде: `runs.status` пишется из
множества мест движка, планировщика, ресурсов и организации, и инвариант обязан
пережить появление ещё одного такого места. Эти тесты бьют по САМОЙ НИЖНЕЙ
границе — прямому UPDATE, — потому что путь, который обходит ORM и сервисный
слой, обязан упереться в тот же отказ, что и обычный вызов.

Позитивные контроли здесь не декорация: запрет, который заодно ломает
идемпотентную финализацию или дозапись улик после исхода, — это не инвариант,
а поломка.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import task_runs as runs_t, tasks as tasks_t, utcnow


async def _run(env, status: str) -> int:
    now = utcnow()
    async with env.svc.db.session() as s:
        tid = int((await s.execute(sa.insert(tasks_t).values(
            title="прогон", prompt="x", status=status, meta={},
            created_at=now, updated_at=now))).inserted_primary_key[0])
        rid = int((await s.execute(sa.insert(runs_t).values(
            task_id=tid, attempt=1, status=status,
            started_at=now))).inserted_primary_key[0])
        await s.commit()
    return rid


async def _status(env, run_id: int) -> str:
    async with env.svc.db.session() as s:
        return str((await s.execute(sa.select(runs_t.c.status)
                                    .where(runs_t.c.id == run_id))).first()[0])


async def _force(env, run_id: int, status: str) -> None:
    """Самая грубая граница из возможных: прямая запись в строку прогона."""
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id)
                        .values(status=status))
        await s.commit()


@pytest.mark.parametrize(("start", "attempt"), [
    ("completed", "failed"),
    ("failed", "completed"),
    ("completed", "running"),
    ("failed", "running"),
    ("completed", "queued"),
    ("failed", "leased"),
])
async def test_a_terminal_run_cannot_change_status(env, start, attempt):
    """Четыре перехода из задания владельца плюс два соседних по смыслу."""
    rid = await _run(env, start)
    with pytest.raises(Exception) as caught:
        await _force(env, rid, attempt)
    assert "terminal run status is immutable" in str(caught.value)
    assert await _status(env, rid) == start          # откат, а не частичная запись


async def test_writing_the_same_terminal_status_is_not_a_transition(env):
    """Позитивный контроль: повторная финализация обязана остаться идемпотентной.

    Без этого запрет ломал бы штатный путь — повторный `completed` поверх
    `completed` не переход и не попытка что-либо переписать.
    """
    rid = await _run(env, "completed")
    await _force(env, rid, "completed")
    assert await _status(env, rid) == "completed"


async def test_other_columns_of_a_terminal_run_stay_writable(env):
    """Позитивный контроль: после исхода дописывают улики, ссылки и времена.

    Запрет держит ровно `status`, а не строку целиком: иначе финализация не
    смогла бы записать собственный результат.
    """
    rid = await _run(env, "completed")
    finished = utcnow()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == rid)
                        .values(finished_at=finished, error="что-то на память"))
        await s.commit()
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t.c.status, runs_t.c.error)
                               .where(runs_t.c.id == rid))).first()
    assert row[0] == "completed" and row[1] == "что-то на память"


async def test_a_live_run_still_reaches_a_terminal_outcome(env):
    """Позитивный контроль: обычный жизненный цикл не задет.

    running -> completed и running -> failed обязаны проходить, иначе запрет
    остановил бы не подделку, а работу.
    """
    ok = await _run(env, "running")
    await _force(env, ok, "completed")
    assert await _status(env, ok) == "completed"

    bad = await _run(env, "running")
    await _force(env, bad, "failed")
    assert await _status(env, bad) == "failed"


async def test_a_retry_is_a_new_run_rather_than_a_revived_one(env):
    """Повтор — НОВЫЙ прогон с новым `attempt`, а не воскрешение прежнего.

    Это и есть причина, по которой запрет ничего не ломает: легальный путь
    никогда не нуждался в возврате терминального прогона в живое состояние.
    """
    first = await _run(env, "failed")
    async with env.svc.db.session() as s:
        task_id = int((await s.execute(sa.select(runs_t.c.task_id)
                                       .where(runs_t.c.id == first))).first()[0])
        second = int((await s.execute(sa.insert(runs_t).values(
            task_id=task_id, attempt=2, status="queued",
            started_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
    assert await _status(env, first) == "failed"
    assert await _status(env, second) == "queued"
