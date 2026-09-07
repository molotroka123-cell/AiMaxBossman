"""V6 §A: старт процесса измерен по фазам, а не описан словами.

`/api/system.startup` отдаёт список фаз `Services.start` с длительностями по
монотонным часам. Правила честности: до завершения старта `ready` = False и
`total_ms` = None (нет измерения — нет числа); повторный старт заводит новую
трассу; упавшая фаза остаётся в трассе с ошибкой.
"""
from __future__ import annotations

import asyncio

import pytest

from bcc.auth import HEADER
from bcc.lifecycle import StartupTrace

from .conftest import make_settings, start_app


@pytest.mark.anyio
async def test_system_reports_measured_startup_phases(tmp_path):
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               headers={HEADER: svc.auth.token}) as client:
            body = (await client.get("/api/system")).json()
        startup = body["startup"]
        assert startup["ready"] is True
        assert isinstance(startup["total_ms"], (int, float)) and startup["total_ms"] >= 0
        assert startup["ready_at"]
        names = [p["name"] for p in startup["phases"]]
        assert names[0] == "db.create_all"
        assert names[-1] == "engine.recover"
        expected_setups = [f"feature.setup:{f.name}" for f in svc.features if f.setup]
        assert [n for n in names if n.startswith("feature.setup:")] == expected_setups
        assert all(isinstance(p["ms"], (int, float)) and p["ms"] >= 0 for p in startup["phases"])
        assert not any("error" in p for p in startup["phases"])
        # сумма фаз не может превышать общее время старта
        assert sum(p["ms"] for p in startup["phases"]) <= startup["total_ms"] + 1.0
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_restart_produces_a_fresh_trace_not_an_appended_one(tmp_path):
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        first = svc.startup
        n = len(first.phases)
        await svc.stop()
        await svc.start()
        assert svc.startup is not first
        assert len(svc.startup.phases) == n
        assert len(first.phases) == n, "старая трасса не должна дописываться"
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_unfinished_trace_reports_no_numbers():
    trace = StartupTrace()
    assert trace.to_dict() == {"ready": False, "total_ms": None, "ready_at": None, "phases": []}
    trace.begin()
    async with trace.phase("x"):
        await asyncio.sleep(0.01)
    d = trace.to_dict()
    assert d["ready"] is False and d["total_ms"] is None
    assert d["phases"][0]["name"] == "x" and d["phases"][0]["ms"] >= 5


@pytest.mark.anyio
async def test_failed_phase_is_recorded_with_its_error_and_trace_is_immutable_after_finish():
    trace = StartupTrace()
    trace.begin()
    with pytest.raises(RuntimeError, match="boom"):
        async with trace.phase("broken"):
            raise RuntimeError("boom")
    assert trace.phases[0]["error"] == "RuntimeError: boom"
    trace.finish()
    assert trace.ready is True
    with pytest.raises(RuntimeError):
        trace.phase("late")
    total = trace.total_ms
    trace.finish()                       # повторный finish ничего не меняет
    assert trace.total_ms == total
