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

import pytest

from .conftest import make_settings, start_app


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
