"""Owner STOP keeps Studio jobs visible until the worker actually exits."""
from __future__ import annotations

import asyncio

import httpx
import pytest
import sqlalchemy as sa

from bcc.pit.studio_image_edit import StudioImageEditBroker, StudioImageEditConfig
from bcc.studio import runtime as studio_runtime
from bcc.studio.tables import jobs as studio_jobs
from bcc.terminal_cli.cli import Out, global_stop
from bcc.v2.images_tables import image_jobs


async def test_owner_stop_reports_live_studio_worker_after_queue_cancel(env):
    async with env.svc.db.session() as s:
        result = await s.execute(sa.insert(image_jobs).values(
            prompt="held test job", model_alias="mock-image", status="running"))
        job_id = int(result.inserted_primary_key[0])
        await s.execute(sa.insert(studio_jobs).values(
            job_id=job_id, plane={"model": "mock-image"}, surface="image"))
        await s.commit()

    release = asyncio.Event()
    worker = asyncio.create_task(release.wait())
    env.svc._studio_active_tasks = {job_id: worker}
    try:
        response = (await env.client.post("/api/control-plane/stop-all")).json()
        assert response["ok"] is False
        assert response["requested"]["studio"] == [job_id]
        assert response["remaining"]["studio"] == [job_id]
        async with env.svc.db.session() as s:
            status = (await s.execute(sa.select(image_jobs.c.status).where(
                image_jobs.c.id == job_id))).scalar_one()
        assert status == "cancelled"

        release.set()
        await worker
        env.svc._studio_active_tasks.pop(job_id)
        again = (await env.client.post("/api/control-plane/stop-all")).json()
        assert again["remaining"]["studio"] == []
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_owner_stop_waits_for_studio_worker_cancellation(env, monkeypatch):
    async with env.svc.db.session() as s:
        result = await s.execute(sa.insert(image_jobs).values(
            prompt="held provider", model_alias="mock:image", status="running"))
        job_id = int(result.inserted_primary_key[0])
        await s.execute(sa.insert(studio_jobs).values(
            job_id=job_id, plane={"model": "mock:image"}, surface="image"))
        await s.commit()

    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def held_generate(svc, job, ext):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(studio_runtime, "_generate", held_generate)
    worker = asyncio.create_task(studio_runtime.process_claimed(
        env.svc, {"id": job_id}))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        response = (await env.client.post("/api/control-plane/stop-all")).json()
        await asyncio.wait_for(worker, 3)
        assert cancelled.is_set()
        assert response["remaining"]["studio"] == []
        assert response["stopped"]["studio"] == [job_id]
        assert response["ok"] is True, response
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_owner_stop_confirms_queued_but_not_external_provider_outcome(env):
    async with env.svc.db.session() as s:
        queued = await s.execute(sa.insert(image_jobs).values(
            prompt="not submitted", model_alias="openrouter:test", status="queued"))
        queued_id = int(queued.inserted_primary_key[0])
        remote = await s.execute(sa.insert(image_jobs).values(
            prompt="already submitted", model_alias="openrouter:test", status="running"))
        remote_id = int(remote.inserted_primary_key[0])
        for jid in (queued_id, remote_id):
            await s.execute(sa.insert(studio_jobs).values(
                job_id=jid, plane={"model": "openrouter:test"}, surface="image"))
        await s.commit()

    release = asyncio.Event()
    worker = asyncio.create_task(release.wait())
    env.svc._studio_active_tasks = {remote_id: worker}
    asyncio.get_running_loop().call_later(0.1, release.set)
    try:
        response = (await env.client.post("/api/control-plane/stop-all")).json()
        assert response["stopped"]["studio"] == [queued_id]
        assert response["requested"]["studio"] == [remote_id]
        assert response["provider_outcome_unknown"] == [remote_id]
        assert response["remaining"]["studio"] == []
        assert response["ok"] is False
        async with env.svc.db.session() as s:
            marker = (await s.execute(sa.select(
                studio_jobs.c.reason, studio_jobs.c.verdict).where(
                studio_jobs.c.job_id == remote_id))).one()
        assert tuple(marker) == ("owner_stop_provider_unknown", "OWNER_REQUIRED")
        later = (await env.client.post("/api/control-plane/stop-all")).json()
        assert later["ok"] is False
        assert later["remaining"]["studio_provider_unknown"] == [remote_id]
        assert later["provider_outcome_unknown"] == [remote_id]
        retry = await env.client.post(f"/api/studio/jobs/{remote_id}/retry")
        assert retry.status_code == 409
        assert "Inspect external request" in retry.text
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_pit_cancellation_requests_existing_studio_job_cancel():
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    held = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/studio/models":
            return httpx.Response(200, json={"items": [{
                "id": "local:test", "available": True, "free": True, "provider": "local"}]})
        if path == "/api/studio/jobs" and request.method == "POST":
            return httpx.Response(200, json={"id": 42})
        if path == "/api/studio/jobs/42" and request.method == "GET":
            entered.set()
            await held.wait()
            return httpx.Response(200, json={"status": "running"})
        if path == "/api/studio/jobs/42/cancel" and request.method == "POST":
            cancelled.set()
            return httpx.Response(200, json={"id": 42, "status": "cancelled"})
        return httpx.Response(404, json={"error": "unexpected route"})

    broker = StudioImageEditBroker(
        StudioImageEditConfig("http://127.0.0.1:8800", "", "local:test"),
        transport=httpx.MockTransport(handler))
    task = asyncio.create_task(broker.generate(prompt="a cat"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await broker.close()


def test_cli_stop_names_unresolved_studio_outcome(capsys):
    class Client:
        def post(self, path):
            assert path == "/api/control-plane/stop-all"
            return {"ok": False, "stopped": {"studio": []},
                    "requested": {"studio": []},
                    "remaining": {"studio_provider_unknown": [42]},
                    "provider_outcome_unknown": [42], "errors": []}

    assert global_stop(Client(), Out("text")) != 0
    output = capsys.readouterr().out
    assert "OWNER_REQUIRED" in output
    assert "job ID 42" in output
    assert "Повтор STOP не подтверждает" in output
