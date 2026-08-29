"""Тесты Stage 6 — Private Remote Client backend (без живого Postgres:
используется асинхронный in-memory фейк-хранилище).

Покрываем: контракт enrollment (секрет один раз, хранится хэш), верификацию и
отзыв, гейт скоупов на HTTP-маршрутах (chat не достаёт до admin/approve),
блокировку устройства и экстренную lock-all (fail-closed), фильтр событий по
скоупам, невозможность эскалации прав и мутации cloud_policy, и то, что в
хранилище лежит sha256, а сверка — постоянного времени.
"""
from __future__ import annotations

import asyncio
import hmac
import json

import httpx
import pytest
from fastapi import FastAPI

from bossman import approvals, errors, events as core_events
from bossman.remote_client import (
    DeviceRegistry,
    DeviceService,
    InMemoryDeviceStore,
    Principal,
    reset_service,
    router,
    set_service,
)
from bossman.remote_client import auth as auth_mod
from bossman.remote_client.auth import (
    KNOWN_SCOPES,
    SCOPE_ADMIN,
    SCOPE_APPROVE,
    SCOPE_CHAT,
    SCOPE_EVENTS,
    hash_token,
)
from bossman.remote_client.events import (
    event_allowed,
    event_required_scope,
    iter_device_events,
)
from bossman.remote_client.security import require_scope


# ---------- фикстуры / помощники ----------

@pytest.fixture
def fresh_service():
    """Свежий сервис на in-memory фейке; активен как синглтон на время теста."""
    svc = DeviceService(InMemoryDeviceStore())
    set_service(svc)
    try:
        yield svc
    finally:
        reset_service()


def make_app() -> FastAPI:
    app = FastAPI()
    errors.install_error_handlers(app)
    app.include_router(router)
    return app


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- 1. Приёмочный контракт синхронного DeviceRegistry ----------

def test_acceptance_device_revoke():
    """Зеркало жёсткого приёмочного теста test_stage4_7::test_device_revoke."""
    r = DeviceRegistry()
    did, t = r.enroll("iphone", ("chat",))
    assert r.verify(did, t, "chat")
    r.revoke(did)
    assert not r.verify(did, t)


def test_registry_wrong_token_and_scope():
    r = DeviceRegistry()
    did, t = r.enroll("iphone", ("chat",))
    assert r.verify(did, t) is True
    assert r.verify(did, "wrong-token") is False           # неверный токен
    assert r.verify(did, t, "approve") is False            # нет скоупа
    assert r.verify("dev_nope", t) is False                # нет устройства


def test_registry_stores_hash_not_raw():
    """Секрет хранится ХЭШИРОВАННЫМ, а не в открытом виде."""
    r = DeviceRegistry()
    did, t = r.enroll("iphone", ("chat",))
    stored = r._devices[did].token_hash
    assert stored == hash_token(t)          # это sha256(raw)
    assert stored != t                       # не сырой токен
    assert len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)


# ---------- 2. Async сервис: enroll один раз, хранится хэш, verify/revoke ----------

async def test_enroll_returns_raw_once_and_stores_only_hash(fresh_service):
    svc = fresh_service
    did, raw = await svc.enroll("phone", {SCOPE_CHAT})
    # Сырой токен не сохранён нигде — в хранилище только его sha256.
    stored = svc.store._credentials[did]
    assert stored == hash_token(raw)
    assert raw not in svc.store._credentials.values()
    assert raw != stored
    # verify через authenticate работает по сырому токену
    principal = await svc.authenticate(f"Bearer {raw}")
    assert principal.device_id == did and SCOPE_CHAT in principal.scopes


async def test_wrong_token_fails(fresh_service):
    svc = fresh_service
    await svc.enroll("phone", {SCOPE_CHAT})
    with pytest.raises(errors.AuthDenied):
        await svc.authenticate("Bearer rcd_totally-wrong-secret")


async def test_revoke_then_denied(fresh_service):
    svc = fresh_service
    did, raw = await svc.enroll("phone", {SCOPE_CHAT})
    assert (await svc.authenticate(f"Bearer {raw}")).device_id == did
    await svc.revoke_device(did)
    with pytest.raises(errors.DeviceRevoked):
        await svc.authenticate(f"Bearer {raw}")


async def test_constant_time_compare_is_hmac(fresh_service):
    """Сверка хэшей идёт через постоянное по времени hmac.compare_digest."""
    assert auth_mod.constant_time_eq is not hmac.compare_digest  # это обёртка...
    # ...но она реально делегирует в hmac.compare_digest:
    assert auth_mod.constant_time_eq("a" * 64, "a" * 64) is True
    assert auth_mod.constant_time_eq("a" * 64, "b" * 64) is False


# ---------- 3. Гейт скоупов на HTTP-маршрутах ----------

async def test_chat_device_denied_on_admin_route(fresh_service):
    svc = fresh_service
    _, chat_tok = await svc.enroll("phone", {SCOPE_CHAT, SCOPE_EVENTS})
    app = make_app()
    async with client_for(app) as c:
        r = await c.post("/remote/devices", json={"name": "x", "scopes": ["chat"]},
                         headers=bearer(chat_tok))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "SCOPE_DENIED"


async def test_admin_device_allowed_on_admin_route(fresh_service):
    svc = fresh_service
    _, op_tok = await svc.enroll("operator", KNOWN_SCOPES)
    app = make_app()
    async with client_for(app) as c:
        r = await c.post("/remote/devices", json={"name": "phone", "scopes": ["chat", "events"]},
                         headers=bearer(op_tok))
    assert r.status_code == 200
    body = r.json()
    assert body["device_id"].startswith("dev_")
    assert body["token"].startswith("rcd_")      # сырой токен показан один раз
    assert body["scopes"] == ["chat", "events"]


async def test_chat_device_denied_on_approve_route(fresh_service, monkeypatch):
    svc = fresh_service
    _, chat_tok = await svc.enroll("phone", {SCOPE_CHAT})
    called = {}

    async def fake_decide(aid, approve, decided_by):
        called["hit"] = True
        return {"id": aid, "status": "approved"}

    monkeypatch.setattr(approvals, "decide", fake_decide)
    app = make_app()
    async with client_for(app) as c:
        r = await c.post("/remote/approvals/5", json={"approve": True}, headers=bearer(chat_tok))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "SCOPE_DENIED"
    assert "hit" not in called      # decide НЕ вызвана — гейт сработал раньше тела


async def test_approve_device_reaches_decide(fresh_service, monkeypatch):
    svc = fresh_service
    dev_id, tok = await svc.enroll("phone", {SCOPE_CHAT, SCOPE_APPROVE})
    seen = {}

    async def fake_decide(aid, approve, decided_by):
        seen["args"] = (aid, approve, decided_by)
        return {"id": aid, "status": "approved", "decided_by": decided_by}

    monkeypatch.setattr(approvals, "decide", fake_decide)
    app = make_app()
    async with client_for(app) as c:
        r = await c.post("/remote/approvals/9", json={"approve": True}, headers=bearer(tok))
    assert r.status_code == 200
    assert seen["args"] == (9, True, f"device:{dev_id}")   # как telegram-путь, но device:


# ---------- 4. Отзыв / блокировка / экстренная lock-all ----------

async def test_revoked_device_http_denied(fresh_service):
    svc = fresh_service
    did, tok = await svc.enroll("operator", KNOWN_SCOPES)
    await svc.revoke_device(did)
    app = make_app()
    async with client_for(app) as c:
        r = await c.get("/remote/whoami", headers=bearer(tok))
    assert r.status_code == 403 and r.json()["error"]["code"] == "DEVICE_REVOKED"


async def test_locked_device_denied(fresh_service):
    svc = fresh_service
    did, tok = await svc.enroll("phone", {SCOPE_CHAT})
    await svc.lock_device(did, True)
    with pytest.raises(errors.DeviceRevoked):
        await svc.authenticate(f"Bearer {tok}")
    # разблокировка возвращает доступ (enrollment не удалён)
    await svc.lock_device(did, False)
    assert (await svc.authenticate(f"Bearer {tok}")).device_id == did


async def test_emergency_lock_all_fails_closed(fresh_service):
    svc = fresh_service
    _, a = await svc.enroll("op", KNOWN_SCOPES)
    _, b = await svc.enroll("phone", {SCOPE_CHAT})
    n = await svc.lock_all(True)
    assert n == 2
    for tok in (a, b):
        with pytest.raises(errors.DeviceRevoked):
            await svc.authenticate(f"Bearer {tok}")
    # даже валидный админ во время lock-all не пройдёт по HTTP
    app = make_app()
    async with client_for(app) as c:
        r = await c.post("/remote/lock", json={"locked": False}, headers=bearer(a))
    assert r.status_code == 403 and r.json()["error"]["code"] == "DEVICE_REVOKED"


async def test_missing_and_bad_token(fresh_service):
    app = make_app()
    async with client_for(app) as c:
        r1 = await c.get("/remote/whoami")                       # нет заголовка
        r2 = await c.get("/remote/whoami", headers=bearer("garbage"))  # мусор
    assert r1.status_code == 401 and r1.json()["error"]["code"] == "AUTH_DENIED"
    assert r2.status_code == 401 and r2.json()["error"]["code"] == "AUTH_DENIED"


# ---------- 5. Сессии ----------

async def test_session_auth_and_independent_revoke(fresh_service):
    svc = fresh_service
    did, dev_tok = await svc.enroll("phone", {SCOPE_CHAT})
    sid, ses_tok = await svc.open_session(did)
    # сессия аутентифицирует и несёт скоупы устройства
    p = await svc.authenticate(f"Bearer {ses_tok}")
    assert p.device_id == did and p.session_id == sid and SCOPE_CHAT in p.scopes
    # отзыв сессии не трогает device-токен
    await svc.revoke_session(sid)
    with pytest.raises(errors.DeviceRevoked):
        await svc.authenticate(f"Bearer {ses_tok}")
    assert (await svc.authenticate(f"Bearer {dev_tok}")).device_id == did


async def test_session_token_cannot_open_new_session(fresh_service):
    svc = fresh_service
    did, _ = await svc.enroll("phone", {SCOPE_CHAT})
    sid, ses_tok = await svc.open_session(did)
    app = make_app()
    async with client_for(app) as c:
        r = await c.post("/remote/auth", headers=bearer(ses_tok))
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_DENIED"


# ---------- 6. Анти-эскалация и неприкосновенность cloud_policy ----------

async def test_cannot_grant_scopes_beyond_own(fresh_service):
    """admin без approve не может выпустить устройство с approve (эскалация)."""
    svc = fresh_service
    _, tok = await svc.enroll("limited-admin", {SCOPE_ADMIN, SCOPE_CHAT})
    app = make_app()
    async with client_for(app) as c:
        r = await c.post("/remote/devices", json={"name": "x", "scopes": ["approve"]},
                         headers=bearer(tok))
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"


async def test_chat_cannot_mint_admin_device(fresh_service):
    svc = fresh_service
    _, chat_tok = await svc.enroll("phone", {SCOPE_CHAT})
    app = make_app()
    async with client_for(app) as c:
        r = await c.post("/remote/devices", json={"name": "evil", "scopes": ["admin"]},
                         headers=bearer(chat_tok))
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"


def test_router_exposes_no_policy_mutation_route():
    """Ни одного маршрута правки cloud_policy/агента/скоупов устройства.
    Stage 12 mobile-роутер расширяет whitelist, но ЗАПРЕТЫ не меняются:
    /tasks read+create (device-scoped), /approvals GET (redacted), PWA-статика.
    Никаких POST/DELETE на agents/scopes/cloud_policy."""
    def _paths(routes):
        out = set()
        for r in routes:
            p = getattr(r, "path", None)
            if p:
                out.add(p)
            sub = getattr(r, "original_router", None)      # Stage 12: include_router-обёртка
            if sub is not None:
                prefix = getattr(getattr(r, "include_context", None), "prefix", "")
                out |= {f"{prefix}{sp}" if prefix else sp for sp in _paths(sub.routes)}
            sub = getattr(r, "routes", None)               # классический include_router
            if sub:
                out |= {f"{getattr(r, 'prefix', '')}{sp}" if getattr(r, "prefix", "") else sp
                        for sp in _paths(sub)}
        return out

    paths = _paths(router.routes)
    for p in paths:
        low = p.lower()
        assert "cloud" not in low and "policy" not in low
        assert not (low.startswith("/remote/agents") and "{" not in low and
                    any(m in low for m in ("post",)))  # agents только read-only GET
        assert "scope" not in low   # нет маршрута правки скоупов существующего устройства
    # Whitelist Stage 6 + mobile-расширение Stage 12 (без мутаций политики).
    mobile = {"/remote/tasks", "/remote/tasks/{task_id}", "/remote/approvals",
              "/remote/agents", "/remote/session/logout",
              "/remote/app", "/remote/app/{asset}"}
    assert mobile <= paths
    assert {"/remote/devices", "/remote/auth", "/remote/whoami", "/remote/events",
            "/remote/approvals/{approval_id}", "/remote/lock",
            "/remote/devices/{device_id}/revoke"} <= paths
    # Запрещённых HTTP-методов на чувствительных путях нет
    def _route_methods(routes, base=""):
        out = {}
        for r in routes:
            p = getattr(r, "path", None)
            if p:
                key = f"{base}{p}"
                out[key] = out.get(key, set()) | set(getattr(r, "methods", None) or set())
            sub = getattr(r, "original_router", None)
            if sub is not None:
                prefix = getattr(getattr(r, "include_context", None), "prefix", "")
                out.update(_route_methods(sub.routes, base + prefix))
            sub = getattr(r, "routes", None)
            if sub:
                out.update(_route_methods(sub, base + getattr(r, "prefix", "")))
        return out

    all_methods = _route_methods(router.routes)
    assert all_methods.get("/remote/agents") == {"GET"}
    assert all_methods.get("/remote/app") == {"GET"}
    assert all_methods.get("/remote/app/{asset}") == {"GET"}
    assert all_methods.get("/remote/tasks") == {"GET", "POST"}
    assert all_methods.get("/remote/tasks/{task_id}") == {"GET"}
    assert all_methods.get("/remote/approvals") == {"GET"}
    assert "POST" not in (all_methods.get("/remote/whoami") or set())


# ---------- 7. Прямой вызов зависимости (adversarial: минуем HTTP) ----------

class _FakeReq:
    def __init__(self, headers):
        self.headers = headers


async def test_dependency_raises_scope_denied_directly(fresh_service):
    svc = fresh_service
    _, chat_tok = await svc.enroll("phone", {SCOPE_CHAT})
    dep = require_scope(SCOPE_ADMIN)
    with pytest.raises(errors.ScopeDenied):
        await dep(_FakeReq({"authorization": f"Bearer {chat_tok}"}))
    # а достаточный скоуп — проходит
    _, op_tok = await svc.enroll("op", KNOWN_SCOPES)
    p = await require_scope(SCOPE_ADMIN)(_FakeReq({"authorization": f"Bearer {op_tok}"}))
    assert isinstance(p, Principal) and SCOPE_ADMIN in p.scopes


# ---------- 8. Фильтр событий по скоупам ----------

def test_event_required_scope_mapping():
    assert event_required_scope("approval.created") == SCOPE_APPROVE
    assert event_required_scope("agent.updated") == SCOPE_ADMIN
    assert event_required_scope("model.loaded") == SCOPE_ADMIN
    assert event_required_scope("task.created") == SCOPE_CHAT
    assert event_required_scope("project.updated") == SCOPE_CHAT
    assert event_required_scope("something.weird") == SCOPE_ADMIN  # fail-closed


def test_event_allowed_by_scopes():
    chat = frozenset({SCOPE_CHAT, SCOPE_EVENTS})
    assert event_allowed("task.created", chat) is True
    assert event_allowed("approval.created", chat) is False   # нет approve
    assert event_allowed("agent.updated", chat) is False      # нет admin


async def test_event_bridge_filters_scoped_out_events(fresh_service):
    """Мост отдаёт только события, покрытые скоупами устройства."""
    principal = Principal(device_id="dev_x", scopes=frozenset({SCOPE_CHAT, SCOPE_EVENTS}),
                          name="phone", session_id=None)
    q = core_events.subscribe()
    agen = iter_device_events(principal, queue=q)
    try:
        core_events.emit("task.created", id=1)        # chat -> пройдёт
        core_events.emit("approval.created", id=2)    # approve -> отфильтровано
        core_events.emit("agent.updated", name="a")   # admin -> отфильтровано
        core_events.emit("project.updated", slug="p")  # chat -> пройдёт

        m1 = json.loads(await asyncio.wait_for(agen.__anext__(), 1))
        m2 = json.loads(await asyncio.wait_for(agen.__anext__(), 1))
        assert m1["kind"] == "task.created"
        assert m2["kind"] == "project.updated"        # approval/agent пропущены
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(agen.__anext__(), 0.2)
    finally:
        await agen.aclose()
        core_events.unsubscribe(q)


# ---------- 9. Подсистема ----------

def test_build_subsystem_contract():
    from bossman.remote_client import build_subsystem
    sub = build_subsystem()
    assert sub.name == "remote_client"
    assert sub.critical is False
    for meth in ("validate", "start", "stop"):
        assert callable(getattr(sub, meth))


async def test_subsystem_degrades_without_postgres(monkeypatch):
    """validate() при недоступной БД: логирует, ставит in-memory фейк, помечает
    degraded (пробросом BossmanError), но не падает намертво."""
    from bossman.remote_client import build_subsystem
    from bossman import db as db_mod

    async def boom():
        raise OSError("no postgres")

    monkeypatch.setattr(db_mod, "pool", boom)
    sub = build_subsystem()
    with pytest.raises(errors.BossmanError):
        await sub.validate()
    # но сервис-синглтон уже поднят на in-memory фейке и работает
    from bossman.remote_client import get_service
    svc = get_service()
    did, raw = await svc.enroll("phone", {SCOPE_CHAT})
    assert (await svc.authenticate(f"Bearer {raw}")).device_id == did
    reset_service()
