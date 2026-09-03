"""Жизненный цикл Services: фоновые задачи фич должны переживать старт worker'ов.

Фичи регистрируют свои подписки в `svc._tasks` внутри `setup()` (так делают
`missions`, `benchlab`, `failure_to_case`), а `start()` затем заводит собственные
петли. Пока список присваивался заново, ручки фич терялись, и `stop()` их не
отменял: подписка продолжала жить после остановки. В тестах дефект не виден —
там `start_workers=False`, поэтому проверка нужна именно с включёнными worker'ами.
"""
from __future__ import annotations

import asyncio

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
