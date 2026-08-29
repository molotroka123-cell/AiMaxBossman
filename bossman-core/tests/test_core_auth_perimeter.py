"""Адверсариальный периметр ядра (Stage 6 скоупы на его HTTP/WS маршрутах).

Дыра, которую это закрывает: маршруты самого ядра (задачи, проекты, approvals,
песочница, dev-factory, AI Lab, смена cloud_policy) были доступны БЕЗ
аутентификации — процесс на localhost решал consequential-подтверждение без
всякого credential. Теперь каждый защищён `require_scope` из Stage 6 — второго
auth-стека нет, тот же DeviceService, что у /remote/*.

Проверяем ПОВЕДЕНИЕ: аноним/не тот скоуп/отзыв/блокировка → отказ ДО обработчика.
Для approvals дополнительно доказываем, что `approvals.decide` НЕ вызывается.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from bossman import approvals as approvals_mod
from bossman import errors, events as core_events
from bossman.remote_client import (
    DeviceService,
    InMemoryDeviceStore,
    reset_service,
    set_service,
)
from bossman.remote_client.auth import (
    KNOWN_SCOPES,
    SCOPE_ADMIN,
    SCOPE_APPROVE,
    SCOPE_CHAT,
    SCOPE_EVENTS,
)

pytestmark = pytest.mark.anyio


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


def _app() -> FastAPI:
    """Полное приложение ядра со всеми роутерами подсистем."""
    import importlib

    import bossman.api as api
    importlib.reload(api)      # пере-собрать роуты на свежем модуле
    return api.app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def _token(svc, scopes) -> str:
    _, raw = await svc.enroll("dev", set(scopes))
    return raw


# ---------- матрица: путь × требуемый скоуп ----------
# (метод, путь, требуемый_скоуп)  — по одному представителю на группу
PROTECTED = [
    ("GET", "/tasks", SCOPE_CHAT),
    ("GET", "/projects", SCOPE_CHAT),
    ("GET", "/approvals", SCOPE_APPROVE),
    ("GET", "/sandbox/status", SCOPE_ADMIN),
    ("GET", "/resource/snapshot", SCOPE_ADMIN),
    ("GET", "/dev-factory/jobs", SCOPE_ADMIN),
    ("GET", "/api/lab/candidates", SCOPE_ADMIN),
    ("GET", "/search?q=x", SCOPE_CHAT),
    ("GET", "/video/jobs", SCOPE_CHAT),
]


@pytest.mark.parametrize("method,path,scope", PROTECTED)
async def test_anonymous_denied_everywhere(svc, method, path, scope):
    app = _app()
    async with _client(app) as c:
        r = await c.request(method, path)
    assert r.status_code in (401, 403), f"{path} пустил анонима: {r.status_code}"


@pytest.mark.parametrize("method,path,scope", PROTECTED)
async def test_wrong_scope_denied(svc, method, path, scope):
    # устройство с ЕДИНСТВЕННЫМ скоупом, которого этому маршруту не хватает
    other = SCOPE_EVENTS if scope != SCOPE_EVENTS else SCOPE_CHAT
    tok = await _token(svc, {other})
    app = _app()
    async with _client(app) as c:
        r = await c.request(method, path, headers=_bearer(tok))
    assert r.status_code == 403, f"{path} пустил чужой скоуп {other}: {r.status_code}"


async def test_admin_reaches_sandbox_status(svc):
    tok = await _token(svc, {SCOPE_ADMIN})
    app = _app()
    async with _client(app) as c:
        r = await c.get("/sandbox/status", headers=_bearer(tok))
    assert r.status_code == 200
    assert "enabled" in r.json()


async def test_chat_reaches_tasks_but_not_sandbox(svc):
    tok = await _token(svc, {SCOPE_CHAT})
    app = _app()
    async with _client(app) as c:
        ok = await c.get("/tasks", headers=_bearer(tok))
        denied = await c.get("/sandbox/status", headers=_bearer(tok))
    # /tasks дойдёт до обработчика (503 только из-за отсутствия БД — не 401/403)
    assert ok.status_code not in (401, 403)
    assert denied.status_code == 403


# ---------- approvals: доказать, что decide НЕ вызывается ----------

@pytest.mark.parametrize("headers_factory,expect_call", [
    (lambda t: {}, False),                      # аноним
    (lambda t: _bearer(t["chat"]), False),      # chat — мимо
    (lambda t: _bearer(t["events"]), False),    # events — мимо
    (lambda t: _bearer(t["approve"]), True),    # approve — доходит
])
async def test_approval_decide_reached_only_with_approve_scope(
        svc, monkeypatch, headers_factory, expect_call):
    calls = {"n": 0}

    async def spy_decide(aid, approve, by):
        calls["n"] += 1
        return {"id": aid, "status": "approved" if approve else "rejected"}

    monkeypatch.setattr(approvals_mod, "decide", spy_decide)

    toks = {
        "chat": await _token(svc, {SCOPE_CHAT}),
        "events": await _token(svc, {SCOPE_EVENTS}),
        "approve": await _token(svc, {SCOPE_APPROVE}),
    }
    app = _app()
    async with _client(app) as c:
        r = await c.post("/approvals/1", json={"approve": True, "by": "x"},
                         headers=headers_factory(toks))
    if expect_call:
        assert calls["n"] == 1, "approve-устройство не дошло до decide"
        assert r.status_code == 200
    else:
        assert calls["n"] == 0, f"decide вызван без approve-скоупа ({r.status_code})!"
        assert r.status_code in (401, 403)


async def test_revoked_device_denied_on_approvals(svc, monkeypatch):
    calls = {"n": 0}

    async def spy_decide(aid, approve, by):
        calls["n"] += 1
        return {"id": aid}

    monkeypatch.setattr(approvals_mod, "decide", spy_decide)
    dev_id, tok = await svc.enroll("phone", {SCOPE_APPROVE})
    await svc.revoke_device(dev_id)
    app = _app()
    async with _client(app) as c:
        r = await c.post("/approvals/1", json={"approve": True}, headers=_bearer(tok))
    assert r.status_code in (401, 403)
    assert calls["n"] == 0


async def test_global_lock_denies_valid_approve_device(svc, monkeypatch):
    calls = {"n": 0}

    async def spy_decide(aid, approve, by):
        calls["n"] += 1
        return {"id": aid}

    monkeypatch.setattr(approvals_mod, "decide", spy_decide)
    _, tok = await svc.enroll("phone", {SCOPE_APPROVE})
    await svc.lock_all(True)      # экстренная блокировка гасит даже валидные токены
    app = _app()
    async with _client(app) as c:
        r = await c.post("/approvals/1", json={"approve": True}, headers=_bearer(tok))
    assert r.status_code in (401, 403)
    assert calls["n"] == 0


# ---------- битые/подделанные заголовки ----------

@pytest.mark.parametrize("hdr", [
    {"Authorization": "Bearer rcd_forged_nonexistent_token_value"},
    {"Authorization": "Bearer "},
    {"Authorization": "Basic abc"},
    {"Authorization": "rcd_no_scheme"},
])
async def test_forged_or_malformed_bearer_denied(svc, hdr):
    app = _app()
    async with _client(app) as c:
        r = await c.get("/sandbox/status", headers=hdr)
    assert r.status_code in (401, 403)


# ---------- WS /events: подписка только со скоупом events ----------

async def test_ws_events_requires_events_scope(svc):
    from starlette.testclient import TestClient

    app = _app()
    tok = await _token(svc, {SCOPE_CHAT})   # НЕ events
    client = TestClient(app)
    with pytest.raises(Exception):
        # chat-устройство: рукопожатие закрывается 1008 до подписки
        with client.websocket_connect(
                "/events", subprotocols=[f"bossman.bearer.{tok}"]):
            pass


async def test_ws_events_anonymous_denied(svc):
    from starlette.testclient import TestClient

    app = _app()
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/events"):
            pass
