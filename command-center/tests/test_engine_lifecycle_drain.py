"""Регресс на py3.12 teardown-hang: движок дренирует фоновые задачи ДО dispose пула.

Причина хэнга (FABLE5 lifecycle audit): `worker_loop`/`execute` отменяли
per-run и heartbeat задачи fire-and-forget, а `Services.stop` диспозил пул БД,
пока осиротевшие задачи ещё держали aiosqlite-коннекты. При закрытии event loop
под 3.12 `_cancel_all_tasks` возобновлял их на `await s.commit()` уже закрытого
пула → ~180s зависание. Фикс — строгий порядок «drain → dispose» через
`TaskEngine.aclose()`. Этот тест сторожит инвариант, не полагаясь на 3.12.
"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_aclose_cancels_and_awaits_active_run_tasks(env):
    eng = env.svc.engine

    started = asyncio.Event()

    async def _fake_run():
        started.set()
        await asyncio.sleep(3600)  # «зависшая» задача, держащая ресурс

    t = asyncio.create_task(_fake_run(), name="bcc-run-fake")
    eng._active[999999] = t
    await started.wait()

    await eng.aclose()

    assert t.cancelled() or t.done(), "aclose должен отменить и ДОЖДАТЬСЯ задачи"
    assert not eng._active, "aclose должен очистить _active"


@pytest.mark.asyncio
async def test_execute_awaits_its_heartbeat_so_no_task_leaks(env):
    """execute() должен дождаться отменённого heartbeat, а не пережить его."""
    eng = env.svc.engine
    before = {t for t in asyncio.all_tasks() if not t.done()}

    async def _noop_run(run_id):
        return None

    # подменяем тяжёлый _run на no-op: проверяем именно lifecycle heartbeat
    orig = eng._run
    eng._run = _noop_run
    try:
        await eng.execute(run_id=1)
    finally:
        eng._run = orig

    await asyncio.sleep(0)  # дать event loop финализировать
    leaked = {t for t in asyncio.all_tasks() if not t.done()} - before
    leaked = {t for t in leaked if "_heartbeat" in (t.get_coro().__qualname__ or "")}
    assert not leaked, f"heartbeat-задача пережила execute(): {leaked}"
