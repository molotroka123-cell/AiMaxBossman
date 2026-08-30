"""Pythia World Intelligence — регрессия на дефекты drop-in'а и инварианты.

Ловит то, что было сломано в 4fb8b6f и чинится здесь:
* `/world_intelligence/agent/view` падал 500 — `Any` в AgentViewOut не
  импортирован (PydanticUserError на первом запросе);
* `get_pythia_view` возвращал корутину без await;
* аннотация зависимости ссылалась на несуществующий `PythiaWorldIntelligence`;
* пакет не экспортировал `router` → все ручки молча выпадали из приложения;
* ручки были БЕЗ auth (стали видимы только после починки экспорта router).

И проверяет контрактные инварианты Pythia:
* auth — каждый маршрут требует скоуп Stage 6 (chat), аноним получает отказ;
* fail-soft — Pythia offline не роняет ядро, ручки отвечают 200 под auth;
* critical=False — validate() не бросает при недоступной Pythia;
* semantic boundary — подсистема НЕ имеет action authority.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from bossman.remote_client import (
    DeviceService,
    InMemoryDeviceStore,
    reset_service,
    set_service,
)
from bossman.remote_client.auth import SCOPE_CHAT
from bossman.world_intelligence.routes import router
from bossman.world_intelligence.subsystem import (
    PythiaWorldSubsystem,
    build_subsystem,
    get_pythia,
    get_pythia_view,
)

pytestmark = pytest.mark.anyio

ALL_PATHS = [
    "/world_intelligence/health",
    "/world_intelligence/agent/view",
    "/world_intelligence/predictions",
    "/world_intelligence/world",
    "/world_intelligence/health-score",
    "/world_intelligence/state",
    "/world_intelligence/state/stream",
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def svc():
    s = DeviceService(InMemoryDeviceStore())
    set_service(s)
    try:
        yield s
    finally:
        reset_service()


@pytest.fixture(autouse=True)
def pythia_offline(monkeypatch):
    """Pythia недоступна: любой GET к ней возвращает None (offline)."""
    async def _offline(self, path):
        return None
    monkeypatch.setattr(PythiaWorldSubsystem, "_get", _offline, raising=True)


def _app() -> FastAPI:
    from bossman import errors
    app = FastAPI()
    errors.install_error_handlers(app)   # AuthDenied → 401/403, как в ядре
    app.include_router(router)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _bearer(svc, scopes=(SCOPE_CHAT,)) -> dict:
    _, raw = await svc.enroll("dev", set(scopes))
    return {"Authorization": f"Bearer {raw}"}


# ---------- auth ----------

@pytest.mark.parametrize("path", ALL_PATHS)
async def test_every_endpoint_requires_auth(svc, path):
    """Аноним не должен получать intelligence — отказ ДО обработчика."""
    async with _client(_app()) as c:
        r = await c.get(path)
    assert r.status_code in (401, 403), f"{path} без auth -> {r.status_code}"


@pytest.mark.parametrize("path", ALL_PATHS)
async def test_every_endpoint_failsoft_200_with_scope(svc, path):
    """Под скоупом chat и с offline-Pythia все ручки отвечают 200 (fail-soft)."""
    headers = await _bearer(svc)
    async with _client(_app()) as c:
        r = await c.get(path, headers=headers)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"


async def test_agent_view_returns_valid_structure_offline(svc):
    """Главная machine-readable ручка не должна падать 500 (регресс `Any`)."""
    headers = await _bearer(svc)
    async with _client(_app()) as c:
        r = await c.get("/world_intelligence/agent/view", headers=headers)
    assert r.status_code == 200
    body = r.json()
    for key in ("summary", "domains", "events_by_domain", "event_count",
                "predictions", "market_watch", "source", "timestamp"):
        assert key in body, f"agent/view отдал без поля {key}: {body}"
    assert body["source"] == "pythia"
    assert body["event_count"] == 0
    assert body["domains"] == [] and body["predictions"] == []


async def test_agent_view_maps_real_payload(svc, monkeypatch):
    """Когда Pythia отвечает — поля пробрасываются, structure валидна."""
    payload = {
        "summary": "рынок спокоен", "domains": ["crypto"],
        "events_by_domain": {"crypto": 3}, "event_count": 3,
        "predictions": [{"p": 0.7}], "market_watch": {"btc": "flat"},
    }

    async def _fake_get(self, path):
        return payload if path == "/agent/view" else None

    monkeypatch.setattr(PythiaWorldSubsystem, "_get", _fake_get, raising=True)
    headers = await _bearer(svc)
    async with _client(_app()) as c:
        r = await c.get("/world_intelligence/agent/view", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == "рынок спокоен"
    assert body["domains"] == ["crypto"]
    assert body["event_count"] == 3
    assert body["predictions"] == [{"p": 0.7}]
    assert body["source"] == "pythia"      # источник переопределяется адаптером


async def test_get_pythia_view_awaits_not_a_coroutine(monkeypatch):
    """get_pythia_view обязан вернуть данные/None, а не корутину."""
    async def _offline(self, path):
        return None
    monkeypatch.setattr(PythiaWorldSubsystem, "_get", _offline, raising=True)
    value = await get_pythia_view()
    assert not asyncio.iscoroutine(value)
    assert value is None            # Pythia offline → None, честно


async def test_validate_is_failsoft_when_pythia_down(monkeypatch):
    """critical=False: недоступная Pythia не бросает из validate() → не рушит boot."""
    sub = build_subsystem()
    assert sub.critical is False
    assert isinstance(sub, PythiaWorldSubsystem)

    async def _boom(url):
        raise ConnectionError("pythia down")

    if sub._client is not None:
        monkeypatch.setattr(sub._client, "get", _boom, raising=True)
    await sub.validate()                    # не должно бросить
    assert sub._state["status"] in {"offline", "degraded"}
    await sub.stop()                        # идемпотентно, без исключений


def test_package_exposes_router_for_stage_registration():
    """_include_stage_routers() берёт getattr(package, 'router').

    Без этого экспорта на уровне пакета все /world_intelligence/* молча
    выпадали из приложения (router=None → include пропущен)."""
    import importlib
    pkg = importlib.import_module("bossman.world_intelligence")
    router_obj = getattr(pkg, "router", None)
    assert router_obj is not None, "пакет не отдаёт router — ручки выпадут из app"
    assert len(router_obj.routes) == 7


async def test_routes_mounted_and_gated_in_core_app(svc):
    """Сквозная проверка: реальное app ядра отдаёт ручку под auth, аноним — нет."""
    import importlib
    import bossman.api as api
    importlib.reload(api)
    async with _client(api.app) as c:
        anon = await c.get("/world_intelligence/health")
        authed = await c.get("/world_intelligence/health", headers=await _bearer(svc))
    assert anon.status_code in (401, 403)
    assert authed.status_code == 200


def test_pythia_has_no_action_authority():
    """Semantic boundary: подсистема — источник знания, НЕ орган действия."""
    forbidden = {"execute", "decide", "approve", "act", "run_action",
                 "create_approval", "apply", "commit_action"}
    present = {n for n in dir(PythiaWorldSubsystem) if not n.startswith("_")}
    assert not (forbidden & present), f"Pythia получила action authority: {forbidden & present}"
