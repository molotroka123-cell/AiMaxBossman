"""V6: память без замера — «не измерено», а не 128 ГБ из воздуха.

Находка визуального прохода (F3): Overview/Resources показывали «0.0 / 125.0 ГБ»,
пока сэмплер метрик ещё не записал ни одной строки — `_snapshot` подставлял
128 000 МБ «всего» и 0 «занято», и допуск задач считался от выдуманного запаса.
"""
from __future__ import annotations

import pytest

from bcc.features import resources as res
from bcc.v2.resource_brain import Reservation, ResourceSnapshot, plan_memory

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


# --------------------------------------------------------------------------
# BL-024: ручной «всего памяти» поверх ОТСУТСТВУЮЩЕГО замера
# --------------------------------------------------------------------------
# Раздел 20 задания перечисляет инвариант дословно: «Unknown, stale, NaN,
# negative or contradictory memory data is not safe capacity».
#
# Снимок с `measured=False` честно говорит «замера нет». Но `plan_memory` этот
# флаг НЕ ЧИТАЛ и считал допуск по числам, а числа в этом случае берутся из
# ручного `total_override_mb` (поле есть на странице «Ресурсы»). Получалось:
# psutil недоступен → замера нет → владелец когда-то вписал 128000 → планировщик
# выдаёт 112 ГБ «свободного бюджета», которого никто не измерял.
#
# Проверка идёт по ВЕРДИКТУ планировщика, а не по внутренним полям: внутренние
# числа можно поправить в одном месте и оставить дыру в другом.

def test_an_override_without_a_measurement_is_not_capacity():
    unmeasured = ResourceSnapshot(total_memory_mb=128_000, used_system_mb=0,
                                  reserve_floor_mb=16_000, measured=False)
    # available_for_new_mb по этим числам = 112 000, и раньше этого хватало.
    assert unmeasured.available_for_new_mb >= 8_000
    plan = plan_memory(unmeasured, 8_000, policy="balanced")
    assert plan.allowed is False
    assert any("замер" in line or "not measured" in line for line in plan.explanation), \
        "отказ обязан назвать причину: иначе владелец не поймёт, что чинить"


def test_unloading_idle_models_cannot_buy_capacity_that_was_never_measured():
    """Второй путь внутрь того же решения — выгрузка простаивающих.

    Первая ветка `plan_memory` сравнивает запрос со свободным бюджетом; вторая
    добавляет к нему освобождаемое выгрузкой. Проверять надо ОБЕ: правка,
    закрывающая только первую, оставила бы допуск через вторую.
    """
    snap = ResourceSnapshot(total_memory_mb=0, used_system_mb=0, reserve_floor_mb=16_000,
                            reservations=[Reservation("model:1", 40_000, idle=True)],
                            measured=False)
    assert plan_memory(snap, 8_000, policy="performance").allowed is False


def test_a_measured_snapshot_with_idle_models_still_unloads_and_starts():
    """Обратный контроль: отказ обязан быть узким.

    Без этого теста «починка» вида «всегда возвращать False» прошла бы как
    исправление, а на деле выключила бы допуск целиком.
    """
    snap = ResourceSnapshot(total_memory_mb=64_000, used_system_mb=40_000,
                            reserve_floor_mb=16_000,
                            reservations=[Reservation("model:1", 30_000, idle=True)],
                            measured=True)
    plan = plan_memory(snap, 20_000, policy="balanced")
    assert plan.allowed is True and plan.unload == ["model:1"]
