"""V6: память без замера — «не измерено», а не 128 ГБ из воздуха.

Находка визуального прохода (F3): Overview/Resources показывали «0.0 / 125.0 ГБ»,
пока сэмплер метрик ещё не записал ни одной строки — `_snapshot` подставлял
128 000 МБ «всего» и 0 «занято», и допуск задач считался от выдуманного запаса.
"""
from __future__ import annotations

import pytest

from bcc.features import resources as res
from bcc.v2.resource_brain import ResourceSnapshot, plan_memory

from .conftest import make_settings, start_app, client_for


@pytest.mark.anyio
async def test_without_a_sample_the_api_reports_a_live_reading_not_128gb(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with client_for(app, svc) as client:
            body = (await client.get("/api/resources")).json()
        live = svc.metrics.read()
        assert body["measured"] is True
        assert body["total_mb"] == pytest.approx(live["ram_total_mb"], rel=0.05)
        assert body["total_mb"] != 128000 or live["ram_total_mb"] == 128000
        assert body["used_mb"] > 0
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_unmeasured_memory_is_null_in_the_api_and_refuses_admission(tmp_path, monkeypatch):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        monkeypatch.setattr(svc.metrics, "read", lambda: {})      # psutil недоступен
        async with client_for(app, svc) as client:
            body = (await client.get("/api/resources")).json()
        assert body["measured"] is False
        assert body["total_mb"] is None and body["used_mb"] is None and body["available_mb"] is None

        snap = await res._snapshot(svc, {"reserve_floor_mb": 16000, "policy": "balanced"})
        assert snap.measured is False and snap.available_for_new_mb == 0
        assert plan_memory(snap, 1, policy="balanced").allowed is False
    finally:
        await svc.stop()


def test_a_measured_snapshot_still_admits_what_fits():
    snap = ResourceSnapshot(total_memory_mb=64000, used_system_mb=10000, reserve_floor_mb=16000)
    assert snap.measured is True
    assert plan_memory(snap, 8000, policy="balanced").allowed is True
