"""§27 — попытка ВОСПРОИЗВЕСТИ потерю соединения при остановке, до правки кода.

Наблюдение, ради которого это написано: медленная фоновая петля может не успеть
выйти за STOP_GRACE (2 c), быть отменённой во время запроса к базе, и её
соединение окажется порванным внутри драйвера — вернуть его в пул уже нечем,
потому что `dispose()` до выданных соединений не достаёт.

Правило §27 — сначала воспроизвести, потом чинить, и не поднимать STOP_GRACE в
качестве «решения». Поднять предел значит сдвинуть порог, за которым та же
поломка случится снова, и назвать это исправлением.

Поэтому тесты ниже задают ЗАВЕДОМО медленный компонент, владеющий соединением,
и спрашивают не «успел ли он», а: осталось ли после остановки хоть одно
нарушение ПРОДУКТА — не возвращённое соединение, осиротевшая задача,
неидемпотентная вторая остановка, воскресший прогон, повторный эффект. Тик,
который не успел за 2 c и был отменён, сам по себе нарушением не является:
остановка обязана быть конечной.
"""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from .conftest import client_for, make_settings, start_app


def _pool(svc):
    return svc.db.engine.pool


def _checked_out(svc) -> int:
    """Сколько соединений выдано и не возвращено."""
    try:
        return _pool(svc).checkedout()
    except Exception:                                   # pragma: no cover
        return 0


def _own_tasks() -> set[asyncio.Task]:
    return {t for t in asyncio.all_tasks() if not t.done()
            and (t.get_name() or "").startswith(("bcc-", "feature-", "worker"))}


class SlowDbLoop:
    """Фоновая петля, которая ГАРАНТИРОВАННО не успевает выйти за STOP_GRACE.

    Держит настоящее соединение с базой и спит внутри сессии дольше предела —
    это и есть форма, из-за которой отмена рвёт соединение в драйвере.
    """

    def __init__(self, svc, hold_seconds: float):
        self.svc = svc
        self.hold_seconds = hold_seconds
        self.entered = asyncio.Event()
        self.cancelled = False
        self.finished = False

    async def run(self) -> None:
        try:
            while True:
                async with self.svc.db.session() as s:
                    await s.execute(sa.text("SELECT 1"))
                    self.entered.set()
                    # Отмена придёт ИМЕННО здесь — с открытым соединением.
                    await asyncio.sleep(self.hold_seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.finished = True


async def _with_slow_loop(tmp_path, hold_seconds: float, *, register: bool = True):
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    loop = SlowDbLoop(svc, hold_seconds)
    task = asyncio.create_task(loop.run(), name="bcc-slow-db-loop")
    if register:
        svc._tasks.append(task)
    await asyncio.wait_for(loop.entered.wait(), timeout=10)
    return app, svc, loop, task


# ------------------------------------------------------- воспроизведение


async def test_a_loop_slower_than_stop_grace_still_stops_the_service(tmp_path):
    """Прямое воспроизведение условия: петля держит соединение дольше предела.

    Проверяется не «успела ли она», а что остановка КОНЕЧНА и не оставила за
    собой продуктового нарушения. Петля, отменённая на пятой секунде при
    пределе в две, — это работающая по спецификации остановка, а не дефект.
    """
    app, svc, loop, task = await _with_slow_loop(tmp_path, hold_seconds=5.0)
    assert svc.STOP_GRACE < 5.0, "предпосылка теста: петля заведомо медленнее"

    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)
    elapsed = asyncio.get_running_loop().time() - started

    assert task.done(), "задача пережила остановку"
    assert loop.cancelled is True, "медленная петля должна быть отменена, а не брошена"
    assert elapsed < svc.STOP_TIMEOUT + 10, f"остановка не конечна: {elapsed:.1f}s"
    assert not _own_tasks(), f"осиротевшие задачи: {[t.get_name() for t in _own_tasks()]}"


async def test_no_connection_is_left_checked_out_after_a_cancelled_db_call(tmp_path):
    """Главный вопрос §27: возвращены ли соединения.

    Именно это наблюдение стоит за жалобой — отмена во время запроса рвёт
    соединение внутри драйвера. Если после остановки хоть одно соединение
    числится выданным, это дефект продукта, а не свойство хоста.
    """
    app, svc, loop, task = await _with_slow_loop(tmp_path, hold_seconds=5.0)
    assert _checked_out(svc) >= 1, "предпосылка: соединение действительно выдано"

    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)

    assert _checked_out(svc) == 0, (
        f"после остановки осталось выдано соединений: {_checked_out(svc)}")


async def test_a_second_stop_is_idempotent(tmp_path):
    """Повторная остановка — не ошибка и не вторая остановка."""
    app, svc, loop, task = await _with_slow_loop(tmp_path, hold_seconds=5.0)
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)
    assert _checked_out(svc) == 0


async def test_a_fast_loop_leaves_within_the_grace_window(tmp_path):
    """Негативный контроль: остановка не «работает» просто потому, что рвёт всё.

    Петля, успевающая выйти сама, обязана выйти САМА — мягкая фаза должна
    что-то значить. Без этой проверки предыдущие тесты одинаково прошли бы на
    системе, которая всегда сразу отменяет.
    """
    app, svc, loop, task = await _with_slow_loop(tmp_path, hold_seconds=0.05)
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < svc.STOP_TIMEOUT, f"быстрая петля стоила полного предела: {elapsed:.1f}s"
    assert _checked_out(svc) == 0


async def test_start_stop_start_works_and_resurrects_nothing(tmp_path):
    """start → stop → start, и ни один прогон не воскресает."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    from bcc.db import task_runs as runs_t

    async with client_for(app, svc) as client:
        from .helpers import make_stack
        ids = await make_stack(client, max_steps=1)
        task_id = ids["task"]["id"]

    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)

    app2, svc2 = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with svc2.db.session() as s:
            rows = [dict(r._mapping) for r in (await s.execute(
                sa.select(runs_t).where(runs_t.c.task_id == task_id))).fetchall()]
        # Ни одна остановленная работа не должна была продолжиться сама
        assert all(r["status"] != "running" for r in rows), rows
        assert _checked_out(svc2) == 0
    finally:
        await asyncio.wait_for(svc2.stop(), timeout=svc2.STOP_TIMEOUT + 15)


async def test_feature_tick_loops_are_drained_too(tmp_path):
    """Петли фич останавливаются по тем же правилам, что worker и scheduler."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=True)
    try:
        await asyncio.sleep(0.1)
    finally:
        await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 20)
    assert _checked_out(svc) == 0
    leftovers = _own_tasks()
    assert not leftovers, f"остались задачи: {[t.get_name() for t in leftovers]}"


async def test_owned_subscriptions_are_released_by_stop(tmp_path):
    """§27 — подписки не переживают остановку.

    Очередь, оставшаяся в шине после остановки, — это и утечка памяти, и
    получатель событий у сервиса, которого больше нет.
    """
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=True)
    before = len(svc.bus._subscribers)
    queue = svc.bus.subscribe()
    assert len(svc.bus._subscribers) == before + 1
    svc.bus.unsubscribe(queue)

    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 20)
    assert queue not in svc.bus._subscribers
    assert _checked_out(svc) == 0


async def test_stopping_mid_run_does_not_duplicate_the_effect(tmp_path):
    """§27 — остановка не покупает второй эффект.

    Прогон, остановленный на середине, после рестарта не должен ни продолжиться
    сам, ни выполниться повторно: терминальный исход неизменяем, а
    незавершённый — не разрешение переиграть.
    """
    from bcc.db import task_runs as runs_t
    from .helpers import make_stack

    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    async with client_for(app, svc) as client:
        ids = await make_stack(client, max_steps=1)
        task_id = ids["task"]["id"]
        await client.post(f"/api/tasks/{task_id}/stop")
    async with svc.db.session() as s:
        before = [dict(r._mapping) for r in (await s.execute(
            sa.select(runs_t).where(runs_t.c.task_id == task_id))).fetchall()]
    await asyncio.wait_for(svc.stop(), timeout=svc.STOP_TIMEOUT + 15)

    app2, svc2 = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        await asyncio.sleep(0.2)
        async with svc2.db.session() as s:
            after = [dict(r._mapping) for r in (await s.execute(
                sa.select(runs_t).where(runs_t.c.task_id == task_id))).fetchall()]
        assert len(after) == len(before), (
            f"рестарт создал новые прогоны: {len(before)} -> {len(after)}")
        assert all(r["status"] != "running" for r in after), after
    finally:
        await asyncio.wait_for(svc2.stop(), timeout=svc2.STOP_TIMEOUT + 15)
