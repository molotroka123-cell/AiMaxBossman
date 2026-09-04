"""Stop (теперь HARD: рвёт активный вызов модели) и Pause (мягкий, с checkpoint)."""
from __future__ import annotations

import asyncio
import contextlib

import sqlalchemy as sa

from bcc.db import task_runs as runs_t

from .conftest import FakeAdapter, client_for, make_settings, start_app, wait_for
from .helpers import make_stack

FAST_ENGINE = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01}


async def test_running_task_stops_and_keeps_checkpoint(tmp_path):
    settings = make_settings(tmp_path)
    holder: dict = {}

    async def press_stop(call: int, messages: list[dict]) -> None:
        # первый ответ модели уже получен и записан в checkpoint — жмём Stop
        if call == 1:
            await holder["client"].post(f"/api/tasks/{holder['task_id']}/stop")

    fake = FakeAdapter("первый шаг", on_chat=None)
    app, svc = await start_app(settings, start_workers=True,
                               adapter_factory=lambda m, p: fake, engine_options=FAST_ENGINE)
    async with client_for(app, svc) as client:
        holder["client"] = client
        # агент из нескольких шагов: между шагами движок и проверяет флаг остановки
        ids = await make_stack(client, max_steps=3)
        holder["task_id"] = ids["task"]["id"]
        fake.on_chat = press_stop

        async def stopped():
            data = (await client.get(f"/api/tasks/{holder['task_id']}")).json()
            done = data["task"]["status"] == "stopped" and data["runs"][-1]["finished_at"]
            return data if done else None

        data = await wait_for(stopped, timeout=10)
        run = data["runs"][-1]
        assert run["status"] == "stopped"
        assert run["finished_at"] is not None
        # hard cancel: Stop пришёл ВО ВРЕМЯ вызова модели — вызов оборван,
        # шаг в полёте честно теряется (checkpoint'а этого шага не существует);
        # сохранность checkpoint между шагами проверяет pause-тест ниже
        assert run["checkpoint"] is None or run["checkpoint"].get("step") in (0, 1)

        events = (await client.get(f"/api/runs/{run['id']}/events")).json()
        assert "run.stopped" in [e["kind"] for e in events]
    await svc.stop()


async def test_pause_returns_run_to_queue_and_resume_continues(tmp_path):
    settings = make_settings(tmp_path)
    holder: dict = {}

    async def press_pause(call: int, messages: list[dict]) -> None:
        if call == 1:
            await holder["client"].post(f"/api/tasks/{holder['task_id']}/pause")

    fake = FakeAdapter("шаг")
    app, svc = await start_app(settings, start_workers=True,
                               adapter_factory=lambda m, p: fake, engine_options=FAST_ENGINE)
    async with client_for(app, svc) as client:
        holder["client"] = client
        ids = await make_stack(client, max_steps=3)
        holder["task_id"] = task_id = ids["task"]["id"]
        fake.on_chat = press_pause

        async def paused():
            data = (await client.get(f"/api/tasks/{task_id}")).json()
            # ждём, пока worker увидит флаг и вернёт run в очередь с checkpoint
            settled = (data["task"]["status"] == "paused"
                       and data["runs"][-1]["status"] == "queued")
            return data if settled else None

        data = await wait_for(paused, timeout=10)
        run = data["runs"][-1]
        assert run["status"] == "queued"                  # ждёт Resume, не потерян
        assert run["checkpoint"]["note"] == "paused" and run["checkpoint"]["messages"] == 3

        fake.on_chat = None
        assert (await client.post(f"/api/tasks/{task_id}/resume")).json()["status"] == "queued"

        async def completed():
            data = (await client.get(f"/api/tasks/{task_id}")).json()
            return data if data["task"]["status"] == "completed" else None

        data = await wait_for(completed, timeout=10)
        # продолжили с checkpoint: сохранённый ответ дошёл до результата, модель не дёргали заново
        assert data["result"] == "шаг"
        assert data["runs"][-1]["checkpoint"]["step"] == 1
        assert fake.calls == 1
    await svc.stop()


async def test_stop_is_written_even_when_the_cancel_poisons_the_connection(tmp_path):
    """Отмена рвёт операцию SQLite в полёте — и дозапись исхода падает.

    Соединение возвращается в пул мёртвым, и первый же следующий запрос
    получает "no active connection". На этот запрос и приходится запись
    исхода остановленного прогона. Без повтора run навсегда оставался бы
    `running` без `finished_at`: задача показывает «остановлено», а прогон
    рядом с ней выглядит работающим, и само это чинилось бы только
    восстановлением аренды, то есть спустя минуты.

    Здесь та же ошибка подставляется намеренно, поэтому проверка
    детерминированная, а не «повезло с нагрузкой».
    """
    from sqlalchemy.exc import OperationalError

    settings = make_settings(tmp_path)
    holder: dict = {}

    async def press_stop(call: int, messages: list[dict]) -> None:
        if call == 1:
            await holder["client"].post(f"/api/tasks/{holder['task_id']}/stop")

    fake = FakeAdapter("первый шаг", on_chat=None)
    app, svc = await start_app(settings, start_workers=True,
                               adapter_factory=lambda m, p: fake, engine_options=FAST_ENGINE)

    poisoned: list[int] = []
    real_finish = svc.engine._finish

    async def flaky_finish(run_id, task_id, status, **kw):
        # Первая запись исхода приходит на мёртвое соединение — ровно так,
        # как это происходит после hard cancel под нагрузкой.
        if status == "stopped" and not poisoned:
            poisoned.append(run_id)
            raise OperationalError("UPDATE runs SET status=?", {},
                                   Exception("no active connection"))
        return await real_finish(run_id, task_id, status, **kw)

    svc.engine._finish = flaky_finish
    async with client_for(app, svc) as client:
        holder["client"] = client
        ids = await make_stack(client, max_steps=3)
        holder["task_id"] = ids["task"]["id"]
        fake.on_chat = press_stop

        async def stopped():
            data = (await client.get(f"/api/tasks/{holder['task_id']}")).json()
            run = data["runs"][-1] if data["runs"] else {}
            return data if run.get("status") == "stopped" and run.get("finished_at") else None

        data = await wait_for(stopped, timeout=10)
        assert poisoned, "подстановка не сработала — тест ничего не проверил"
        run = data["runs"][-1]
        assert run["status"] == "stopped" and run["finished_at"] is not None
    await svc.stop()


async def test_a_connection_broken_by_a_cancel_is_not_returned_to_the_pool(tmp_path):
    """Соединение, на котором случилась отмена, обязано уйти из пула.

    Отмена (Stop, выключение) рвёт операцию SQLite в полёте. Само соединение
    остаётся непригодным, но SQLAlchemy об этом не знает: исключение пришло не
    из драйвера, а извне, — и соединение возвращается в пул как здоровое.
    Первый же следующий запрос получает "no active connection": и внутренняя
    дозапись исхода (прогон навсегда застревал в «выполняется»), и обычный
    запрос владельца из дашборда сразу после Stop — пятисоткой на ровном месте.

    Проверяется прямо контракт, а не везение с гонкой: у пула спрашивается,
    ТО ЖЕ ли соединение он выдал следующему запросу.
    """
    from bcc.db import Database

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'pool.sqlite'}")
    taken: list[int] = []
    sa.event.listen(db.engine.sync_engine, "checkout",
                    lambda dbapi_con, rec, proxy: taken.append(id(dbapi_con)))

    async with db.session() as s:
        await s.execute(sa.text("SELECT 1"))
    async with db.session() as s:
        await s.execute(sa.text("SELECT 1"))
    assert taken[0] == taken[1], "обычное закрытие обязано вернуть соединение в пул"

    with contextlib.suppress(asyncio.CancelledError):
        async with db.session() as s:
            await s.execute(sa.text("SELECT 1"))
            raise asyncio.CancelledError()
    async with db.session() as s:
        assert (await s.execute(sa.text("SELECT 1"))).scalar_one() == 1

    assert taken[-1] != taken[-2], (
        "соединение, на котором случилась отмена, вернулось в пул — "
        "следующий запрос получит мёртвое")
    await db.engine.dispose()
