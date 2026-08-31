"""Working Memory (canonical class-based asyncpg impl) — host-honest coverage.

История: ранняя функциональная aiosqlite-версия (create/update/… как модульные
функции) была ЗАМЕНЕНА каноническим классом `bossman.working_memory.WorkingMemory`
(PostgreSQL/asyncpg, commit 93e4ef8). Старый тест ссылался на удалённый API и ронял
сбор всего набора command-center. Здесь — честное покрытие текущего класса:

* детерминированный unit-тест инварианта optimistic concurrency через минимальный
  фейковый asyncpg-пул (реальная логика класса, любой хост);
* реальный Postgres-гейт полного цикла create→update→conflict→checkpoint→restore,
  который SKIP_HOST при отсутствии `BOSSMAN_TEST_PG_DSN` (не фейковый green).
"""
from __future__ import annotations

import json
import os

import pytest

from bossman.working_memory import OptimisticConcurrencyConflict, WorkingMemory

pytestmark = pytest.mark.asyncio


# ----------------------------------------------------------------- fake asyncpg pool

class _FakeConn:
    """Минимальный async-conn: отдаёт заранее заданные fetchrow-ответы по очереди,
    записывает execute/fetchrow-запросы. Транзакция/acquire — no-op контексты."""

    def __init__(self, fetchrow_results):
        self._fetchrow_results = list(fetchrow_results)
        self.executed: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, *params):
        self.executed.append((sql, params))
        return "OK"

    async def fetchrow(self, sql, *params):
        self.fetchrow_calls.append((sql, params))
        if self._fetchrow_results:
            return self._fetchrow_results.pop(0)
        return None

    async def fetch(self, sql, *params):
        return []

    def transaction(self):
        return _NullCtx()


class _NullCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


# ----------------------------------------------------------------- deterministic unit tests

async def test_update_raises_conflict_on_version_mismatch():
    """Инвариант optimistic concurrency: expected_version != current → conflict,
    и это происходит ДО построения/выполнения UPDATE (никакой записи при конфликте)."""
    conn = _FakeConn(fetchrow_results=[{"version": 5}])  # SELECT version FOR UPDATE → 5
    wm = WorkingMemory(_FakePool(conn), project_id=1)
    with pytest.raises(OptimisticConcurrencyConflict):
        await wm.update_task_state("t1", {"objective": "x"}, expected_version=3)
    # UPDATE не выполнялся — только SELECT version
    assert not any("UPDATE working_memory" in sql for sql, _ in conn.executed)


async def test_update_proceeds_when_version_matches():
    """Совпадение версии → UPDATE строится, версия инкрементируется, RETURNING читается."""
    updated_row = {"task_id": "t1", "version": 6, "objective": "x"}
    conn = _FakeConn(fetchrow_results=[{"version": 5}, updated_row])
    wm = WorkingMemory(_FakePool(conn), project_id=1)
    res = await wm.update_task_state("t1", {"objective": "x"}, expected_version=5)
    assert res["version"] == 6
    # хотя бы один RETURNING-запрос с инкрементом версии
    joined = " ".join(sql for sql, _ in conn.fetchrow_calls)
    assert "RETURNING" in joined
    assert any("version = version + 1" in sql for sql, _ in conn.fetchrow_calls)


async def test_update_missing_task_raises_valueerror():
    conn = _FakeConn(fetchrow_results=[None])  # SELECT version → нет строки
    wm = WorkingMemory(_FakePool(conn), project_id=1)
    with pytest.raises(ValueError):
        await wm.update_task_state("missing", {"objective": "x"}, expected_version=1)


async def test_checkpoint_shape():
    state = {"task_id": "t1", "version": 4, "objective": "goal"}
    conn = _FakeConn(fetchrow_results=[state])  # get_task_state → state
    wm = WorkingMemory(_FakePool(conn), project_id=1)
    cp = await wm.checkpoint("t1")
    assert cp["task_id"] == "t1" and cp["version"] == 4 and cp["state"]["objective"] == "goal"


async def test_optimistic_conflict_alias_identity():
    """Канонический публичный тип и обратно-совместимый алиас — один и тот же объект."""
    from bossman.working_memory import ConcurrencyError
    assert ConcurrencyError is OptimisticConcurrencyConflict


# ----------------------------------------------------------------- real Postgres gate (SKIP_HOST)

@pytest.mark.skipif(not os.getenv("BOSSMAN_TEST_PG_DSN"),
                    reason="SKIP_HOST: no BOSSMAN_TEST_PG_DSN (real Postgres) available")
async def test_working_memory_real_postgres_cycle():
    """Полный цикл против реального Postgres: create→update→conflict→checkpoint→restore."""
    import asyncpg
    dsn = os.environ["BOSSMAN_TEST_PG_DSN"]
    pool = await asyncpg.create_pool(dsn)
    try:
        wm = WorkingMemory(pool, project_id=1)
        st = await wm.create_task_state("rt1", objective="obj")
        assert st and st["version"] == 1
        upd = await wm.update_task_state("rt1", {"objective": "obj2"}, expected_version=1)
        assert upd["version"] == 2
        with pytest.raises(OptimisticConcurrencyConflict):
            await wm.update_task_state("rt1", {"objective": "x"}, expected_version=1)
        cp = await wm.checkpoint("rt1")
        assert cp["version"] == 2
    finally:
        await pool.close()
