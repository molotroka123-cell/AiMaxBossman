"""Жизненный цикл Services: фоновые задачи фич должны переживать старт worker'ов.

Фичи регистрируют свои подписки в `svc._tasks` внутри `setup()` (так делают
`missions`, `benchlab`, `failure_to_case`), а `start()` затем заводит собственные
петли. Пока список присваивался заново, ручки фич терялись, и `stop()` их не
отменял: подписка продолжала жить после остановки. В тестах дефект не виден —
там `start_workers=False`, поэтому проверка нужна именно с включёнными worker'ами.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from .conftest import make_settings, start_app


async def settled_pool_balance(balance: list[int], *, deadline_s: float = 5.0) -> int:
    """Сколько соединений НЕ ВЕРНУЛОСЬ в пул, когда всё улеглось.

    Разница между «занято прямо сейчас» и «потеряно» — это вся суть проверки.
    Утечка, ради которой тест написан, ПОСТОЯННА: соединение, оборванное
    отменой посреди запроса, не вернётся никогда, сколько его ни жди. А вот
    возврат соединения петлёй, вышедшей самостоятельно, происходит в
    `__aexit__` сессии и может завершиться на СЛЕДУЮЩЕМ обороте цикла событий.
    Мгновенный замер сразу после `stop()` не различает эти два случая.

    На спокойной машине разницы не видно, и тест был зелёным 25 раз подряд
    локально. На загруженном раннере видно: один и тот же коммит `0c59982d`
    дал два прогона Command Center CI — один красный ровно здесь, второй
    зелёный целиком, причём петли в красном НЕ были оборваны (`severed` пуст),
    то есть терялось не соединение, а оборот цикла.

    Поэтому ждём, пока баланс УЛЯЖЕТСЯ, и возвращаем то, на чём он замер.
    Настоящую утечку это не прощает: она не уляжется ни за пять секунд, ни за
    пять минут — что и проверяет
    `test_a_connection_that_never_returns_is_still_reported` ниже.
    """
    deadline = time.monotonic() + deadline_s
    while balance[0] != 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    return balance[0]


@pytest.mark.anyio
async def test_feature_background_tasks_survive_worker_start_and_are_cancelled(tmp_path):
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        started = asyncio.Event()

        async def _subscription() -> None:
            started.set()
            while True:
                await asyncio.sleep(3600)

        task = asyncio.create_task(_subscription(), name="feature-subscription")
        svc._tasks.append(task)          # так это делает setup() настоящей фичи
        await asyncio.wait_for(started.wait(), timeout=2)

        svc.start_workers = True
        await svc.start()                # старт worker'ов не имеет права терять ручку

        assert task in svc._tasks, "ручка фоновой задачи фичи потеряна при старте worker'ов"
    finally:
        await svc.stop()
    assert task.cancelled() or task.done(), "подписка фичи пережила stop() — утечка"


@pytest.mark.anyio
async def test_stop_leaves_no_tasks_behind(tmp_path):
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    svc.start_workers = True
    await svc.start()
    assert svc._tasks, "worker'ы должны быть заведены"
    await svc.stop()
    assert svc._tasks == []


@pytest.mark.anyio
async def test_stop_does_not_hang_on_an_uncancellable_task(tmp_path, monkeypatch):
    """Остановка обязана завершаться, даже если задача отмену игнорирует.

    Раньше stop() ждал каждую отменённую задачу без предела. Задача, которая
    проглотила CancelledError и продолжила работу, вешала остановку навсегда —
    а вместе с ней и завершение теста. Предел делает остановку конечной, а
    задачу, которая не умерла, — названной, а не проглоченной молча.

    Оговорка: 178-секундный teardown, замеченный в CI на py3.12, этим тестом НЕ
    воспроизведён. Здесь закрыта доказуемая опасность, а не тот конкретный случай.
    """
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    monkeypatch.setattr(type(svc), "STOP_TIMEOUT", 0.5, raising=False)
    release = threading.Event()

    async def _stubborn() -> None:
        while not release.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue          # именно так выглядит задача, игнорирующая отмену

    task = asyncio.create_task(_stubborn(), name="stubborn")
    svc._tasks.append(task)
    await asyncio.sleep(0.05)

    started = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(svc.stop(), timeout=8)
    finally:
        release.set()
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 5, f"остановка заняла {elapsed:.1f} c — предел не сработал"
    assert "stubborn" in getattr(svc, "stop_stragglers", ""), \
        "незавершившаяся задача должна быть названа, а не проглочена"
    assert svc._tasks == []


@pytest.mark.anyio
async def test_stop_still_awaits_tasks_that_cancel_properly(tmp_path):
    """Предел не превращает остановку в «бросить и уйти»: послушные задачи дожидаются."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    finished = asyncio.Event()

    async def _polite() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            finished.set()
            raise

    task = asyncio.create_task(_polite(), name="polite")
    svc._tasks.append(task)
    await asyncio.sleep(0.05)

    await svc.stop()

    assert finished.is_set() and task.cancelled()
    assert not getattr(svc, "stop_stragglers", "")


@pytest.mark.anyio
async def test_stop_lets_background_loops_finish_instead_of_severing_them(tmp_path):
    """Петли выходят сами, а не обрываются посреди запроса к базе.

    Отмена во время запроса рвёт соединение внутри драйвера, и вернуть его в
    пул после этого нельзя ничем: SQLAlchemy само не знает, в каком оно
    состоянии, а `dispose()` до выданных соединений не достаёт. Так за каждую
    остановку терялось по паре соединений на петлю — планировщик, сэмплер
    метрик и шесть тиков фич, — а сборщик мусора потом ругался
    «deleted before being closed».

    Проверяется именно это: после остановки петли ЗАВЕРШИЛИСЬ, а не отменены.
    Ждать здесь самой утечки нельзя — она случается, только если отмена попала
    ровно в запрос, то есть тест был бы «иногда красный». Отсутствие отмены —
    тот же факт, но проверяемый всегда. Баланс пула рядом ловит остаток.
    """
    import sqlalchemy as sa

    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    balance = [0]
    sa.event.listen(svc.db.engine.sync_engine, "checkout",
                    lambda con, rec, proxy: balance.__setitem__(0, balance[0] + 1))
    sa.event.listen(svc.db.engine.sync_engine, "checkin",
                    lambda con, rec: balance.__setitem__(0, balance[0] - 1))

    svc.start_workers = True
    await svc.start()
    # Ровно те петли, что теряли соединения: планировщик, сэмплер метрик и
    # тики фич. Подписки, которые фичи заводят сами в setup(), сюда не входят:
    # они висят на шине, а не на базе, и отмена им ничего не рвёт.
    governed = {f"bcc-{f.name}" for f in svc.features if f.tick and f.tick_seconds > 0}
    governed |= {"bcc-scheduler", "bcc-metrics"}
    watched = {t.get_name(): t for t in svc._tasks if t.get_name() in governed}
    assert "bcc-scheduler" in watched and "bcc-metrics" in watched, sorted(watched)
    assert len(watched) > 2, f"тики фич не заведены: {sorted(watched)}"
    await asyncio.sleep(0.2)             # дать петлям дойти до работы с базой

    await svc.stop()

    severed = sorted(name for name, t in watched.items() if t.cancelled())
    assert not severed, (
        f"петли оборваны отменой, а не вышли сами: {severed}. "
        f"Соединение, отменённое посреди запроса, в пул уже не вернётся")
    lost = await settled_pool_balance(balance)
    assert lost == 0, (
        f"после остановки {lost} соединение(й) не вернулось в пул")


@pytest.mark.anyio
async def test_a_connection_that_never_returns_is_still_reported(tmp_path):
    """Обратный контроль к ожиданию в `settled_pool_balance`.

    Без этого теста ожидание было бы неотличимо от «подождать, пока станет
    зелено»: проверка, которая терпит, обязана доказать, что она НЕ терпит
    настоящую потерю. Соединение удерживается намеренно и не возвращается —
    баланс обязан остаться ненулевым и после того, как всё улеглось.
    """
    import sqlalchemy as sa

    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    balance = [0]
    sa.event.listen(svc.db.engine.sync_engine, "checkout",
                    lambda con, rec, proxy: balance.__setitem__(0, balance[0] + 1))
    sa.event.listen(svc.db.engine.sync_engine, "checkin",
                    lambda con, rec: balance.__setitem__(0, balance[0] - 1))

    held = await svc.db.engine.connect()          # взято и НЕ возвращено
    try:
        assert balance[0] == 1, balance
        # Короткий срок: тест про то, что ожидание не прощает потерю, а не про
        # то, сколько мы готовы ждать.
        assert await settled_pool_balance(balance, deadline_s=0.5) == 1
    finally:
        await held.close()
    # И ровно то же измерение видит возврат, когда соединение действительно
    # вернули: иначе проверка выше означала бы «всегда ненулевой».
    assert await settled_pool_balance(balance, deadline_s=0.5) == 0
