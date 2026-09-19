"""Общая работа переживает уход ожидающего — и её отказ никуда не «протекает».

Здесь проверяется ровно то, на чём `asyncio.shield` ломается на Python 3.14:
когда внешний future отменён, shield вешает на внутреннюю задачу
`_log_on_exception`, и тот БЕЗУСЛОВНО зовёт обработчик исключений петли. В CI
это красило `pytest (py3.14)` в красное (job 105833218232), а у владельца
означало бы ERROR с трассой в журнале на каждый отменённый запрос, за которым
общая работа потом упала.

Проверка не слепая: отдельный канареечный тест показывает, что стенд ВИДИТ
сообщение петли, а контрольный — что на 3.14 `asyncio.shield` его действительно
порождает. Без этих двух «пусто» в отчёте ничего бы не доказывало.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import gc
import sys
from pathlib import Path

import pytest

from bcc import single_flight

BCC = Path(single_flight.__file__).resolve().parent


async def _settle(turns: int = 8) -> None:
    for _ in range(turns):
        await asyncio.sleep(0)


async def _fails_after(release: asyncio.Event) -> None:
    await release.wait()
    raise OSError("общая работа упала, когда её уже никто не ждал")


async def _returns_after(release: asyncio.Event, value):
    await release.wait()
    return value


class _Loop:
    """Ловит всё, о чём петля сообщает обработчику исключений."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.reports: list[dict] = []
        self._previous = self.loop.get_exception_handler()
        self.loop.set_exception_handler(lambda _loop, context: self.reports.append(context))

    def restore(self) -> None:
        self.loop.set_exception_handler(self._previous)

    @property
    def messages(self) -> list[str]:
        return [str(context.get("message")) for context in self.reports]


async def _orphaned_failure(join) -> list[str]:
    """Единственный ожидающий отменён, общая задача ПОСЛЕ этого падает.

    Исключение не забирается тестом нигде: забрать его обязан продукт.
    """
    watch = _Loop()
    try:
        release = asyncio.Event()
        shared = asyncio.ensure_future(_fails_after(release))
        finished = asyncio.Event()
        shared.add_done_callback(lambda _done: finished.set())
        waiter = asyncio.ensure_future(join(shared))
        await _settle()
        assert not waiter.done(), "ожидающий не дождался начала общей работы"
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter
        release.set()
        await asyncio.wait_for(finished.wait(), 5)
        await _settle()
        del shared, waiter
        gc.collect()
        await _settle()
        return watch.messages
    finally:
        watch.restore()


def test_the_harness_sees_an_unretrieved_failure():
    """Канарейка: если стенд слеп, «пусто» в остальных тестах ничего не значит."""
    async def run():
        watch = _Loop()
        try:
            orphan = watch.loop.create_future()
            orphan.set_exception(OSError("никем не забранная ошибка"))
            del orphan
            gc.collect()
            await _settle()
        finally:
            watch.restore()
        assert watch.reports, "стенд не видит даже незабранную ошибку — проверки слепы"
    asyncio.run(run())


def test_an_orphaned_failure_is_reported_nowhere():
    async def run():
        assert await _orphaned_failure(single_flight.await_shared) == []
    asyncio.run(run())


def test_asyncio_shield_is_the_thing_that_was_wrong():
    """Контроль: тот же сценарий через shield не молчит НИ НА ОДНОЙ версии.

    До 3.14 shield снимает свой колбэк с внутренней задачи (`_outer_done_callback`),
    и осиротевший отказ всплывает у сборщика мусора как «Task exception was never
    retrieved». На 3.14 вместо снятого колбэка вешается `_log_on_exception`, и
    сообщение приходит сразу и безусловно — даже если владелец задачи отказ уже
    забрал своим колбэком. Второе и покрасило CI: забрать его стало нечем.
    """
    async def run():
        messages = await _orphaned_failure(asyncio.shield)
        expected = ("OSError exception in shielded future" if sys.version_info >= (3, 14)
                    else "Task exception was never retrieved")
        assert messages == [expected], (
            "поведение asyncio.shield изменилось; bcc/single_flight.py объясняет, "
            f"почему он здесь не используется — перечитайте: {messages}")
    asyncio.run(run())


def test_cancelling_one_waiter_does_not_cancel_the_shared_work():
    async def run():
        release = asyncio.Event()
        shared = asyncio.ensure_future(_returns_after(release, "итог"))
        first = asyncio.ensure_future(single_flight.await_shared(shared))
        second = asyncio.ensure_future(single_flight.await_shared(shared))
        await _settle()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not shared.done() and not second.done()
        release.set()
        assert await second == "итог"
        assert shared.result() == "итог"
    asyncio.run(run())


def test_every_waiter_receives_the_same_outcome():
    async def run():
        release = asyncio.Event()
        shared = asyncio.ensure_future(_returns_after(release, {"общий": "снимок"}))
        waiters = [asyncio.ensure_future(single_flight.await_shared(shared)) for _ in range(8)]
        await _settle()
        release.set()
        results = await asyncio.gather(*waiters)
        assert all(r is shared.result() for r in results), "ожидающие получили разные объекты"
    asyncio.run(run())


def test_a_failure_reaches_every_waiter():
    async def run():
        release = asyncio.Event()
        shared = asyncio.ensure_future(_fails_after(release))
        waiters = [asyncio.ensure_future(single_flight.await_shared(shared)) for _ in range(4)]
        await _settle()
        release.set()
        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        assert [type(o) for o in outcomes] == [OSError] * 4
    asyncio.run(run())


def test_cancelling_the_shared_task_cancels_its_waiters():
    async def run():
        release = asyncio.Event()
        shared = asyncio.ensure_future(_returns_after(release, "не дойдёт"))
        waiter = asyncio.ensure_future(single_flight.await_shared(shared))
        await _settle()
        shared.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    asyncio.run(run())


def test_a_finished_task_is_delivered_without_a_second_wait():
    async def run():
        release = asyncio.Event()
        release.set()
        shared = asyncio.ensure_future(_returns_after(release, 42))
        await shared
        assert await single_flight.await_shared(shared) == 42
    asyncio.run(run())


# ---------- охрана: приём не должен вернуться обратно к shield ----------

def _shield_calls(source: str) -> int:
    """Настоящие вызовы asyncio.shield(...), а не упоминания в комментариях."""
    found = 0
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "shield"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "asyncio"):
            found += 1
    return found


def test_the_scanner_tells_a_call_from_a_mention():
    assert _shield_calls("import asyncio\nasync def f(t):\n    return await asyncio.shield(t)\n") == 1
    assert _shield_calls("# не asyncio.shield(): см. bcc/single_flight.py\nx = 1\n") == 0


def test_no_module_went_back_to_asyncio_shield():
    offenders = sorted(path.relative_to(BCC.parent).as_posix()
                       for path in BCC.rglob("*.py")
                       if _shield_calls(path.read_text(encoding="utf-8")))
    assert offenders == [], (
        "asyncio.shield на Python 3.14 сообщает об отказе общей работы в петлю, "
        "даже когда владелец задачи его уже забрал; здесь используется "
        "bcc.single_flight.await_shared. Если shield всё же нужен, объясните "
        f"почему прямо здесь: {offenders}")
