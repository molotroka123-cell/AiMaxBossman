"""Stop/Pause между шагами: run завершается как stopped, checkpoint остаётся в БД."""
from __future__ import annotations

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
        assert run["checkpoint"]["step"] == 1 and run["checkpoint"]["note"] == "stopped"

        # переписка шага сохранена в БД целиком — есть с чего продолжить
        async with svc.db.session() as s:
            raw = (await s.execute(sa.select(runs_t.c.checkpoint)
                                   .where(runs_t.c.id == run["id"]))).scalar_one()
        assert raw["messages"][-1] == {"role": "assistant", "content": "первый шаг"}

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
