"""Задача переживает «перезапуск процесса»: состояние только в БД (раздел 4)."""
from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa

from bcc.db import task_runs as runs_t, tasks as tasks_t, utcnow

from .conftest import FakeAdapter, client_for, make_settings, start_app, wait_for
from .helpers import make_stack


async def test_run_survives_restart_and_completes(tmp_path):
    settings = make_settings(tmp_path)
    fake = FakeAdapter("42")

    # ---- «первый процесс»: задача создана и поставлена в очередь
    app, svc = await start_app(settings, start_workers=False,
                               adapter_factory=lambda model, provider: fake)
    async with client_for(app, svc) as client:
        ids = await make_stack(client)
        task_id = ids["task"]["id"]
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        assert detail["task"]["status"] == "queued"
        assert len(detail["runs"]) == 1
        run_id = detail["runs"][0]["id"]

        # процесс «упал» посреди выполнения: run остался running с протухшей арендой
        async with svc.db.session() as s:
            await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
                status="running", worker_lease_until=utcnow() - timedelta(minutes=5),
                checkpoint={"messages": [{"role": "user", "content": "посчитай 2+2"}],
                            "step": 0, "note": "before-crash"}))
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                status="running"))
            await s.commit()
    await svc.stop()

    # ---- «второй процесс»: та же БД, новый движок — recovery возвращает run в очередь
    app2, svc2 = await start_app(settings, start_workers=True,
                                 adapter_factory=lambda model, provider: fake,
                                 engine_options={"poll_interval": 0.02, "recover_every": 0.2})
    async with client_for(app2, svc2) as client:
        async def completed():
            data = (await client.get(f"/api/tasks/{task_id}")).json()
            return data if data["task"]["status"] in ("completed", "failed") else None

        data = await wait_for(completed, timeout=10)
        assert data["task"]["status"] == "completed"
        assert data["result"] == "42"
        run = data["runs"][0]
        assert run["attempt"] == 1              # crash recovery увеличил попытку
        assert run["status"] == "completed"
        assert run["tokens_out"] == 3
        assert run["checkpoint"]["step"] == 1   # checkpoint дошёл до финального шага

        # лог run'а виден через API
        events = (await client.get(f"/api/runs/{run['id']}/events")).json()
        kinds = [e["kind"] for e in events]
        assert "run.recovered" in kinds and "run.completed" in kinds
    await svc2.stop()
