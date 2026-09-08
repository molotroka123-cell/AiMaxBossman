"""Наблюдения для маршрутизации медиа выходят из мира состояния, а не из воздуха.

Проверяется ровно одно свойство, и оно одностороннее: **несвежее не становится
числом**. Свежий факт публикуется как измерение с отметкой времени; всё
остальное — как «не измерено». Получателю этого достаточно, чтобы закрыть путь,
и недостаточно, чтобы его открыть.
"""
from __future__ import annotations

import time

import pytest

from bcc.reality import media_routing, world
from bossman_shared.objective_world_state import WorldFact

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def svc(tmp_path):
    from bcc.api import create_app
    from bcc.config import Settings
    settings = Settings(data_dir=tmp_path / "data",
                        database_url=f"sqlite+aiosqlite:///{tmp_path / 'data' / 'g.db'}",
                        ui_dir=tmp_path / "no-ui")
    app = create_app(settings, announce_token=False, start_workers=False)
    service = app.state.svc
    await service.start()
    try:
        yield service
    finally:
        await service.stop()


def ingest(svc, key: str, value: dict, *, age: float = 0.0,
           max_age: float = 60.0) -> None:
    now = time.time()
    world.projection(svc).ingest(WorldFact(
        scope_id=world.AMBIENT_SCOPE, key=key, value=value,
        observed_at=now - age, max_age_seconds=max_age,
        source_ref="test", provenance_ref="test"), now=now)


async def test_a_fresh_host_fact_becomes_a_timestamped_measurement(svc):
    ingest(svc, media_routing.HOST_KEY, {"ram_available_mb": 16_384.0})
    reading = media_routing.memory_observation(svc)
    assert reading["value"] == 16_384.0
    assert reading["observed_at_epoch_s"] > 0
    assert reading["source"] == "observer:process"


async def test_a_stale_host_fact_publishes_no_number_at_all(svc):
    """Просроченное наблюдение — не «чуть хуже», а «больше не про этот мир»."""
    ingest(svc, media_routing.HOST_KEY, {"ram_available_mb": 65_536.0},
           age=600.0, max_age=60.0)
    reading = media_routing.memory_observation(svc)
    assert reading["value"] is None
    assert "не свеж" in reading["source"]
    assert "observed_at_epoch_s" not in reading


async def test_a_missing_host_fact_is_not_measured(svc):
    reading = media_routing.memory_observation(svc)
    assert reading["value"] is None


async def test_a_host_fact_without_the_field_is_not_measured(svc):
    ingest(svc, media_routing.HOST_KEY, {"cpu_percent": 12.0})
    assert media_routing.memory_observation(svc)["value"] is None


async def test_provider_health_keeps_unmeasured_separate_from_healthy(svc):
    assert media_routing.provider_observation(svc) == media_routing.UNKNOWN

    ingest(svc, media_routing.PROVIDER_KEY,
           {"healthy": 0, "unhealthy": 0, "unmeasured": 3})
    assert media_routing.provider_observation(svc) == media_routing.UNKNOWN


async def test_provider_health_reports_degraded_when_some_are_down(svc):
    ingest(svc, media_routing.PROVIDER_KEY,
           {"healthy": 2, "unhealthy": 1, "unmeasured": 0})
    assert media_routing.provider_observation(svc) == media_routing.DEGRADED

    ingest(svc, media_routing.PROVIDER_KEY,
           {"healthy": 0, "unhealthy": 2, "unmeasured": 0})
    assert media_routing.provider_observation(svc) == media_routing.DOWN


async def test_facts_this_plane_cannot_measure_default_to_unmeasured(svc):
    """Браузерная сессия живёт в другом сервисе. Догадываться о ней нельзя."""
    body = media_routing.observations(svc)
    assert body["browser"] == media_routing.UNKNOWN
    assert body["local_generator"] == media_routing.UNKNOWN
    assert body["buffer"] is None


async def test_the_endpoint_publishes_observations_and_says_they_are_not_permits(svc):
    from httpx import ASGITransport, AsyncClient
    ingest(svc, media_routing.HOST_KEY, {"ram_available_mb": 8_192.0})
    from bcc.features import reality as feature
    body = await feature.generation_media_observations(
        _Request(svc), refresh=False, browser="ready")
    assert body["free_memory_mb"]["value"] == 8_192.0
    assert body["browser"] == "ready"
    assert "не разрешения" in body["note"]


class _Request:
    """Минимальный объект запроса: маршруту нужен только `app.state.svc`."""

    def __init__(self, svc):
        self.app = type("App", (), {"state": type("State", (), {"svc": svc})()})()
