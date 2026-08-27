"""Retries очереди: экспоненциальная пауза, attempt++, после max_retries — failed."""
from __future__ import annotations

from .conftest import FakeAdapter, client_for, make_settings, start_app, wait_for
from .helpers import make_stack

FAST_ENGINE = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01}


async def test_task_fails_after_max_retries(tmp_path):
    fake = FakeAdapter(fail_times=99, error="llama.cpp не отвечает")
    app, svc = await start_app(make_settings(tmp_path), start_workers=True,
                               adapter_factory=lambda m, p: fake, engine_options=FAST_ENGINE)
    async with client_for(app, svc) as client:
        ids = await make_stack(client, max_retries=2)
        task_id = ids["task"]["id"]

        async def failed():
            data = (await client.get(f"/api/tasks/{task_id}")).json()
            return data if data["task"]["status"] == "failed" else None

        data = await wait_for(failed, timeout=10)
        run = data["runs"][-1]
        assert run["attempt"] == 2                      # 0 → 1 → 2 = max_retries
        assert "llama.cpp не отвечает" in run["error"]
        assert data["error"] and "llama.cpp" in data["error"]
        assert fake.calls == 3                          # исходная попытка + два ретрая
    await svc.stop()


async def test_task_completes_on_second_attempt(tmp_path):
    fake = FakeAdapter("готово", fail_times=1, error="таймаут провайдера")
    app, svc = await start_app(make_settings(tmp_path), start_workers=True,
                               adapter_factory=lambda m, p: fake, engine_options=FAST_ENGINE)
    async with client_for(app, svc) as client:
        ids = await make_stack(client, max_retries=2)
        task_id = ids["task"]["id"]

        async def completed():
            data = (await client.get(f"/api/tasks/{task_id}")).json()
            return data if data["task"]["status"] == "completed" else None

        data = await wait_for(completed, timeout=10)
        assert data["result"] == "готово"
        run = data["runs"][-1]
        assert run["attempt"] == 1
        assert fake.calls == 2
    await svc.stop()
