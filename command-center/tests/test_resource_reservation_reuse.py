"""Long-horizon run 30.10: a task resumed after an approval must reuse its own memory reservation, not stack another one."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import resource_reservations as res_t
from bcc.features import resources as res

from .conftest import make_settings, start_app


async def _held(svc, task_id):
    async with svc.db.session() as s:
        return [tuple(r) for r in (await s.execute(sa.select(res_t.c.id, res_t.c.status).where(
            res_t.c.holder_kind == "task", res_t.c.holder_id == task_id))).fetchall()]


@pytest.mark.anyio
async def test_own_reservation_is_reused_and_duplicates_released(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        a = await res._reserve(svc, "task", 798, 16000)
        b = await res._reserve(svc, "task", 798, 16000)           # the stacked copy the bug produced
        assert await res._renew_own(svc, 798, run_id=1) is True
        rows = dict(await _held(svc, 798))
        assert rows[b] == "held" and rows[a] == "released"       # newest kept, the extra one released
        assert await res._renew_own(svc, 798, run_id=1) is True
        assert sum(st == "held" for st in (await _held(svc, 798)) and dict(await _held(svc, 798)).values()) == 1
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_a_task_without_its_own_reservation_is_not_waved_through(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        await res._reserve(svc, "task", 1, 16000)                 # someone else's reservation
        assert await res._renew_own(svc, 2, run_id=1) is False   # task 2 must pass the normal admission
        snap = await res._snapshot(svc, {"reserve_floor_mb": 16000, "policy": "balanced"})
        assert snap.reserved_mb == 16000                         # other holders still count
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_a_stopped_tasks_reservation_is_released_by_the_tick(tmp_path):
    from bcc.db import tasks as tasks_t
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with svc.db.session() as s:
            res1 = await s.execute(sa.insert(tasks_t).values(prompt="x", title="t", status="stopped"))
            res2 = await s.execute(sa.insert(tasks_t).values(prompt="y", title="u", status="running"))
            stopped, running = int(res1.inserted_primary_key[0]), int(res2.inserted_primary_key[0])
            await s.commit()
        await res._reserve(svc, "task", stopped, 16000)
        await res._reserve(svc, "task", stopped, 16000)
        await res._reserve(svc, "task", running, 16000)
        await res._tick(svc)
        assert all(st != "held" for _, st in await _held(svc, stopped))      # both copies freed
        assert [st for _, st in await _held(svc, running)] == ["held"]       # a live task keeps its memory
    finally:
        await svc.stop()


import httpx as _httpx

_REAL_ASYNC_CLIENT = _httpx.AsyncClient


def _fake_ollama(monkeypatch, loaded, tags, fail=False):
    import httpx

    def handler(request):
        if fail:
            raise httpx.ConnectError("down")
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": loaded})
        return httpx.Response(200, json={"models": tags})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler)))


async def _model(svc, name, base_url):
    from bcc.db import models as models_t, providers as providers_t
    async with svc.db.session() as s:
        pid = int((await s.execute(sa.insert(providers_t).values(name=f"p-{name}-{base_url[-14:]}", kind="openai_compat",
                                                                   base_url=base_url))).inserted_primary_key[0])
        mid = int((await s.execute(sa.insert(models_t).values(provider_id=pid, name=name, alias=f"a-{name}-{base_url[-14:]}",
                                                                kind="local", context_window=32768))).inserted_primary_key[0])
        await s.commit()
    return mid


@pytest.mark.anyio
async def test_exact_resident_model_needs_no_second_reservation(tmp_path, monkeypatch):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        ctx32 = await _model(svc, "gemma-ctx32k:latest", "http://127.0.0.1:11434/v1")
        ctx8 = await _model(svc, "gemma:latest", "http://127.0.0.1:11434/v1")
        remote = await _model(svc, "gemma-ctx32k:latest", "https://api.example.com/v1")
        loaded = [{"name": "gemma-ctx32k:latest", "model": "gemma-ctx32k:latest", "digest": "d32"}]
        tags = [{"name": "gemma-ctx32k:latest", "digest": "d32"}, {"name": "gemma:latest", "digest": "d8"}]
        _fake_ollama(monkeypatch, loaded, tags)
        assert await res._already_resident(svc, ctx32) is True
        assert await res._already_resident(svc, ctx8) is False      # same weights, other num_ctx = another runner
        assert await res._already_resident(svc, remote) is False    # only a local Ollama is trusted
        _fake_ollama(monkeypatch, loaded, tags, fail=True)
        assert await res._already_resident(svc, ctx32) is False     # unreachable -> reserve as before
    finally:
        await svc.stop()
