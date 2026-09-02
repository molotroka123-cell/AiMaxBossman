"""Stage 13 — SECURITY RED TEAM: try to break the "harden pre-dispatch
perimeter: core auth" claim (commit 68a9626) instead of trusting it.

What this file proves adversarially:
  A. ROUTE MATRIX — built programmatically from the real app (including
     lazily-included subsystem routers): every data/consequential route carries
     an auth dependency (require_scope/authenticate_request/require_device_token);
     only static shells, schema docs and the secret-gated telegram webhook are
     anonymous. Con consequential routes are pinned to their elevated scopes.
     No credential is accepted via query string anywhere.
  B. NEGATIVE CASES — anonymous/invalid/revoked/locked are denied on core
     routes; cross-device IDOR on /remote/tasks is 404 for foreign devices but
     explicit for admin; malformed/duplicate Authorization headers never crash;
     WS /events closes 1008 pre-accept for anonymous/wrong-scope/revoked and
     IGNORES token-in-URL; a chat-only device has no route and no store API to
     widen its own scopes.
  C. APPROVAL BOUNDARY — approvals.decide is single-shot (pending filter),
     wait() resolves only on decided status, and the computer-operator manager
     refuses to execute on rejected/expired approvals, stale pending_action,
     pause/state flips between approval and execution, and emergency lock.
"""
from __future__ import annotations

import asyncio
import httpx
import pytest
from fastapi import FastAPI

from bossman import approvals as approvals_mod
from bossman import db as db_mod
from bossman import events as core_events
from bossman import runner as runner_mod
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

# ===========================================================================
# fixtures / helpers
# ===========================================================================


@pytest.fixture(autouse=True)
async def _db_pool_per_loop():
    """BUG-004: pytest-asyncio 1.x даёт КАЖДОМУ тесту свой event loop, а
    `bossman.db._pool` — процессный singleton. Пул, созданный первым DB-запросом
    (b3 → /tasks) на loop теста N, переиспользовался тестами N+1… на других loop
    → «Task got Future attached to a different loop». Закрываем пул на выходе
    из теста на том же loop, где он создан (graceful close); защита от чужого
    loop живёт в самом db.pool() (loop-identity + terminate) и покрыта
    tests/test_secrem_f018_wiring.py."""
    yield
    await db_mod.close()


@pytest.fixture
def svc():
    s = DeviceService(InMemoryDeviceStore())
    set_service(s)
    try:
        yield s
    finally:
        reset_service()


@pytest.fixture(scope="module")
def core_app() -> FastAPI:
    """Реальное приложение ядра со всеми подсистемными роутерами."""
    import bossman.api as api

    return api.app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def _token(svc, scopes) -> str:
    _, raw = await svc.enroll("redteam", set(scopes))
    return raw


def _dep_name(d):
    c = getattr(d, "dependency", None)
    return c.__name__ if hasattr(c, "__name__") else repr(c)


def _flat_deps(dependant, acc):
    acc.append(dependant.call.__name__)
    for sub in dependant.dependencies:
        _flat_deps(sub, acc)
    return acc


def _effective_routes(app: FastAPI):
    """(methods, path, dep_names, dependant) для ВСЕХ эффективных маршрутов,
    включая лениво включённые подсистемные роутеры (_IncludedRouter)."""
    out = []

    def dep_names(dependant, extra_deps):
        deps = _flat_deps(dependant, []) if dependant is not None else []
        deps += [_dep_name(d) for d in (extra_deps or [])]
        return deps

    def walk_included(w):
        for ctx in w.effective_candidates():
            if type(ctx).__name__ == "_IncludedRouter":  # вложенный include (mobile)
                yield from walk_included(ctx)
                continue
            yield ((",".join(sorted(ctx.methods or [])).replace(",HEAD", "")),
                   ctx.path, dep_names(ctx.dependant, ctx.dependencies), ctx.dependant)

    for r in app.router.routes:
        cls = type(r).__name__
        if cls == "_IncludedRouter":
            out.extend(walk_included(r))
        elif cls == "APIRoute":
            out.append((",".join(sorted(r.methods)).replace(",HEAD", ""),
                        r.path, _flat_deps(r.dependant, []), r.dependant))
        elif cls == "APIWebSocketRoute":
            out.append(("WS", r.path, [], None))
        elif cls == "Mount":
            out.append(("MOUNT", r.path, [], None))
        else:  # starlette Route: /docs, /openapi.json ...
            out.append((",".join(sorted(getattr(r, "methods", []) or [])).replace(",HEAD", ""),
                        getattr(r, "path", ""), [], None))
    return out


AUTH_DEP_EXACT = {"authenticate_request", "require_device_token"}


def _has_auth_dep(dep_names) -> bool:
    return any(n in AUTH_DEP_EXACT or n.startswith("require_scope[") for n in dep_names)


# ===========================================================================
# TASK A — контракт матрицы маршрутов
# ===========================================================================

# маршруты БЕЗ auth-зависимости и их обоснование (каждый проверен вручную)
UNAUTH_JUSTIFIED = {
    ("GET", "/"): "static UI shell, no credentials inside",
    ("GET", "/openapi.json"): "schema docs (surface disclosure only)",
    ("GET", "/docs"): "schema docs",
    ("GET", "/docs/oauth2-redirect"): "schema docs",
    ("GET", "/redoc"): "schema docs",
    ("POST", "/telegram/webhook"): "external webhook, X-Telegram-Bot-Api-Secret-Token constant-time gate",
    ("GET", "/remote/app"): "static PWA shell",
    ("GET", "/remote/app/{asset}"): "static PWA asset (fixed server-side allowlist)",
}


def test_route_matrix_every_data_route_requires_auth(core_app):
    """Ни один data/consequential маршрут не отвечает анониму: у каждого либо
    auth-зависимость, либо место в обоснованном allowlist (статика/схема/
    секрет-gated вебхук)."""
    violators = []
    seen_justified = set()
    for methods, path, dep_names, _ in _effective_routes(core_app):
        if methods == "MOUNT":
            assert path == "/ui", f"неожиданный mount: {path}"  # статика UI
            continue
        if methods == "WS":
            assert path == "/events"
            continue  # auth внутри хендлера ДО accept — поведенческие тесты ниже
        for m in methods.split(","):
            if _has_auth_dep(dep_names):
                continue
            key = (m, path)
            if key in UNAUTH_JUSTIFIED:
                seen_justified.add(key)
                continue
            violators.append(f"{m} {path} deps={dep_names}")
    assert not violators, f"маршруты без auth-зависимости: {violators}"
    assert seen_justified == set(UNAUTH_JUSTIFIED), (
        f"allowlist разошёлся с реальностью: {set(UNAUTH_JUSTIFIED) ^ seen_justified}")


def test_route_matrix_consequential_routes_pinned_to_elevated_scopes(core_app):
    """Каждое консеквентное действие требует повышенный скоуп, а не просто auth."""
    expected = {
        # approve — необратимые решения
        ("POST", "/approvals/{approval_id}"): SCOPE_APPROVE,
        ("GET", "/approvals"): SCOPE_APPROVE,
        ("POST", "/remote/approvals/{approval_id}"): SCOPE_APPROVE,
        ("GET", "/remote/approvals"): SCOPE_APPROVE,
        ("POST", "/projects/{slug}/approve"): SCOPE_APPROVE,
        # admin — провижининг/экстренные ручки/ресурсы
        ("PATCH", "/agents/{name}"): SCOPE_ADMIN,
        ("POST", "/models/{alias}/load"): SCOPE_ADMIN,
        ("POST", "/models/{alias}/unload"): SCOPE_ADMIN,
        ("POST", "/remote/devices"): SCOPE_ADMIN,
        ("POST", "/remote/lock"): SCOPE_ADMIN,
        ("POST", "/remote/devices/{device_id}/revoke"): SCOPE_ADMIN,
        ("POST", "/computer/emergency-lock"): SCOPE_ADMIN,
        ("GET", "/sandbox/status"): SCOPE_ADMIN,
        ("GET", "/sandbox/sessions"): SCOPE_ADMIN,
        ("GET", "/resource/snapshot"): SCOPE_ADMIN,
        ("GET", "/resource/leases"): SCOPE_ADMIN,
        ("GET", "/resource/pressure"): SCOPE_ADMIN,
        ("GET", "/dev-factory/jobs"): SCOPE_ADMIN,
        ("GET", "/dev-factory/jobs/{job_id}/patch"): SCOPE_ADMIN,
        ("POST", "/api/lab/evals/run"): SCOPE_ADMIN,
        ("POST", "/api/lab/candidates/{candidate_id}/decide"): SCOPE_ADMIN,
        ("POST", "/api/lab/exports/{candidate_id}/launch_training"): SCOPE_ADMIN,
    }
    actual = {}
    for methods, path, dep_names, _ in _effective_routes(core_app):
        for m in methods.split(","):
            if (m, path) in expected:
                scopes = {n[len("require_scope["):-1] for n in dep_names
                          if n.startswith("require_scope[")}
                actual[(m, path)] = scopes
    missing = set(expected) - set(actual)
    assert not missing, f"консеквентные маршруты не найдены в матрице: {missing}"
    wrong = {k: v for k, v in actual.items() if expected[k] not in v}
    assert not wrong, f"консеквентные маршруты без нужного скоупа: {wrong}"


def test_route_matrix_scopes_are_from_known_set(core_app):
    """require_scope[X] использует только известную таксономию скоупов."""
    for _m, _p, dep_names, _d in _effective_routes(core_app):
        for n in dep_names:
            if n.startswith("require_scope["):
                assert n[len("require_scope["):-1] in KNOWN_SCOPES, n


def test_route_matrix_no_credential_in_query_or_path_params(core_app):
    """Токен НИКОГДА не принимается через URL (query/path) — только Authorization
    заголовок или WS-субпротокол."""
    bad = {"token", "secret", "api_key", "apikey", "access_token", "authorization",
           "auth", "password"}
    offenders = []
    for _m, _p, _deps, dependant in _effective_routes(core_app):
        if dependant is None:
            continue
        for qp in getattr(dependant, "query_params", []):
            if qp.name.lower() in bad:
                offenders.append(f"query:{qp.name}")
        for pp in getattr(dependant, "path_params", []):
            if pp.name.lower() in bad:
                offenders.append(f"path:{pp.name}")
    assert not offenders, f"креденшл в URL: {offenders}"


def test_route_matrix_enroll_is_the_only_scope_issuing_route(core_app):
    """Единственный маршрут, выдающий скоупы, — POST /remote/devices (admin).
    Никаких route-путей расширить собственные права не существует."""
    issuing = []
    for methods, path, _deps, _d in _effective_routes(core_app):
        low = path.lower()
        if "scope" in low or "cloud_policy" in low.replace("-", "_"):
            issuing.append((methods, path))
        if path == "/remote/devices":
            assert methods == "POST"
    assert issuing == [], f"найдены маршруты мутации прав: {issuing}"


# ===========================================================================
# TASK B — негативные кейсы на реальном приложении
# ===========================================================================


async def test_b1_anonymous_denied_on_core_routes(core_app):
    async with _client(core_app) as c:
        for m, p in (("GET", "/tasks"), ("POST", "/tasks"), ("GET", "/spend"),
                     ("GET", "/projects"), ("GET", "/approvals"), ("GET", "/search?q=x"),
                     ("GET", "/computer/tasks"), ("GET", "/resource/snapshot")):
            r = await c.request(m, p)
            assert r.status_code in (401, 403), f"{p} пустил анонима: {r.status_code}"
            assert r.json()["error"]["code"] in ("AUTH_DENIED", "SCOPE_DENIED")


async def test_b2_invalid_token_denied(core_app):
    async with _client(core_app) as c:
        for hdr in (_bearer("rcd_totally-wrong-secret"),
                    _bearer("rcs_bogus-session-token"),
                    {"Authorization": "Bearer"},
                    {"Authorization": "Bearer   "},
                    {"Authorization": "Basic dXNlcjpwYXNz"},
                    {"Authorization": "rcd_no_scheme_at_all"}):
            r = await c.get("/tasks", headers=hdr)
            assert r.status_code == 401, f"{hdr} → {r.status_code}"
            assert r.json()["error"]["code"] == "AUTH_DENIED"


async def test_b3_revoked_session_denied_immediately(core_app, svc):
    did, dev_tok = await svc.enroll("phone", {SCOPE_CHAT, SCOPE_EVENTS})
    sid, ses_tok = await svc.open_session(did)
    async with _client(core_app) as c:
        ok = await c.get("/tasks", headers=_bearer(ses_tok))
        assert ok.status_code != 401 and ok.status_code != 403  # сессия жива
        await svc.revoke_session(sid)                 # отзыв ДО следующего запроса
        r = await c.get("/tasks", headers=_bearer(ses_tok))
        r2 = await c.get("/remote/whoami", headers=_bearer(ses_tok))
    assert r.status_code == 403 and r.json()["error"]["code"] == "DEVICE_REVOKED"
    assert r2.status_code == 403
    # device-токен того же устройства жив
    async with _client(core_app) as c:
        r3 = await c.get("/remote/whoami", headers=_bearer(dev_tok))
    assert r3.status_code == 200 and r3.json()["device_id"] == did


async def test_b4_revoked_device_denied(core_app, svc):
    did, tok = await svc.enroll("phone", KNOWN_SCOPES)
    async with _client(core_app) as c:
        assert (await c.get("/spend", headers=_bearer(tok))).status_code not in (401, 403)
        await svc.revoke_device(did)
        r = await c.get("/spend", headers=_bearer(tok))
        r2 = await c.post("/remote/auth", headers=_bearer(tok))
    assert r.status_code == 403 and r.json()["error"]["code"] == "DEVICE_REVOKED"
    assert r2.status_code == 403          # отозванное устройство не откроет и сессию


async def test_b5_lock_all_denies_consequential_action(core_app, svc, monkeypatch):
    calls = {"n": 0}

    async def spy_decide(aid, approve, by):
        calls["n"] += 1
        return {"id": aid, "status": "approved"}

    monkeypatch.setattr(approvals_mod, "decide", spy_decide)
    _, tok = await svc.enroll("approver", {SCOPE_APPROVE})
    await svc.lock_all(True)      # экстренная блокировка ВСЕХ устройств
    async with _client(core_app) as c:
        r = await c.post("/approvals/123", json={"approve": True}, headers=_bearer(tok))
    assert r.status_code == 403 and r.json()["error"]["code"] == "DEVICE_REVOKED"
    assert calls["n"] == 0, "decide выполнен во время lock-all!"


def _fake_tasks_db():
    """In-memory таблица tasks с SQL-семантикой фильтра по source."""
    class _FakeTasksDB:
        def __init__(self):
            self.rows = {}
            self.next_id = 1

        async def fetchrow(self, sql, *args):
            if "INSERT INTO tasks" in sql:
                tid = self.next_id
                self.next_id += 1
                row = {"id": tid, "agent": args[0], "source": args[1], "text": args[2],
                       "status": "queued", "result": None, "error": None}
                self.rows[tid] = row
                return dict(row)
            if "SELECT * FROM tasks WHERE id=$1 AND source=$2" in sql:
                row = self.rows.get(args[0])
                return dict(row) if row and row["source"] == args[1] else None
            if "SELECT * FROM tasks WHERE id=$1" in sql:
                row = self.rows.get(args[0])
                return dict(row) if row else None
            return None

        async def fetch(self, sql, *args):
            rows = list(self.rows.values())
            if "WHERE source=$1 AND status=$2" in sql:
                rows = [r for r in rows if r["source"] == args[0] and r["status"] == args[1]]
            elif "WHERE source=$1" in sql:
                rows = [r for r in rows if r["source"] == args[0]]
            elif "WHERE status=$1" in sql:
                rows = [r for r in rows if r["status"] == args[0]]
            return [dict(r) for r in rows]

    return _FakeTasksDB()


async def test_b6_cross_device_idor_on_mobile_tasks(core_app, svc, monkeypatch):
    """Устройство A не видит задачу устройства B ни по id (404, без утечки
    существования), ни в списке; admin видит явно."""
    fake = _fake_tasks_db()
    monkeypatch.setattr(db_mod, "fetchrow", fake.fetchrow)
    monkeypatch.setattr(db_mod, "fetch", fake.fetch)
    enqueued = []

    async def fake_enqueue(task_id):
        enqueued.append(task_id)

    monkeypatch.setattr(runner_mod, "enqueue", fake_enqueue)

    tok_a = await _token(svc, {SCOPE_CHAT})
    _, tok_b = await svc.enroll("device-B", {SCOPE_CHAT})
    admin_tok = await _token(svc, KNOWN_SCOPES)

    async with _client(core_app) as c:
        created = await c.post("/remote/tasks", json={"text": "b-secret-plan"},
                               headers=_bearer(tok_b))
        assert created.status_code == 200, created.text
        tid = created.json()["id"]

        # A → задача B: 404 и по списку пусто
        r = await c.get(f"/remote/tasks/{tid}", headers=_bearer(tok_a))
        assert r.status_code == 404
        lst = await c.get("/remote/tasks", headers=_bearer(tok_a))
        assert lst.status_code == 200
        assert all(row["id"] != tid for row in lst.json())

        # B видит свою задачу
        own = await c.get(f"/remote/tasks/{tid}", headers=_bearer(tok_b))
        assert own.status_code == 200 and own.json()["id"] == tid

        # admin читает чужую задачу ЯВНО (единственная эскалация видимости)
        adm = await c.get(f"/remote/tasks/{tid}", headers=_bearer(admin_tok))
        assert adm.status_code == 200 and adm.json()["text"] == "b-secret-plan"
        adm_lst = await c.get("/remote/tasks", headers=_bearer(admin_tok))
        assert any(row["id"] == tid for row in adm_lst.json())
    assert enqueued == [tid]


async def test_b7_malformed_and_duplicate_authorization_no_crash(core_app, svc):
    tok = await _token(svc, {SCOPE_CHAT})
    async with _client(core_app) as c:
        invalid_cases = [
            _bearer("Bearer " + tok),                       # двойной префикс
            _bearer("x" * 8192),                            # гигантский мусор
            _bearer("rcs_"),                                # пустой префикс сессии
            _bearer("!@#$%^&*(){[]}|\\<>"),
            [("Authorization", "Bearer garbage1"), ("Authorization", "Bearer garbage2")],
            [("Authorization", "Bearer garbage"), ("Authorization", f"Bearer {tok}")],
        ]
        for hdr in invalid_cases:
            r = await c.get("/tasks", headers=hdr)
            assert r.status_code == 401, f"ожидался 401 на {hdr!r}: {r.status_code}"
            assert r.json()["error"]["code"] == "AUTH_DENIED"
        # дубликаты: побеждает ПЕРВЫЙ заголовок (стандарт HTTP) — валидный первым
        # проходит auth (дальше 503 без БД — это обработанный отказ, не crash)
        r_valid_first = await c.get("/tasks",
                                    headers=[("Authorization", f"Bearer {tok}"),
                                             ("Authorization", "Bearer garbage")])
        assert r_valid_first.status_code not in (401, 403, 500), r_valid_first.status_code


# ---------- WS /events ----------

def _ws_client(app: FastAPI):
    from starlette.testclient import TestClient

    return TestClient(app)


def _tok_sync(coro):
    return asyncio.run(coro)


def test_b8a_ws_anonymous_closed_1008(core_app):
    client = _ws_client(core_app)
    with pytest.raises(Exception) as ei:
        with client.websocket_connect("/events"):
            pass
    code = getattr(ei.value, "code", None)
    assert code is None or code == 1008, f"анонимный WS открылся (code={code})"


def test_b8b_ws_wrong_scope_closed(core_app, svc):
    tok = _tok_sync(_token(svc, {SCOPE_CHAT}))               # НЕ events
    client = _ws_client(core_app)
    with pytest.raises(Exception) as ei:
        with client.websocket_connect("/events",
                                      subprotocols=[f"bossman.bearer.{tok}"]):
            pass
    code = getattr(ei.value, "code", None)
    assert code is None or code == 1008


def test_b8c_ws_revoked_session_closed(core_app, svc):
    async def setup():
        did, _ = await svc.enroll("phone", {SCOPE_EVENTS})
        sid, ses_tok = await svc.open_session(did)
        await svc.revoke_session(sid)
        return ses_tok

    tok = _tok_sync(setup())
    client = _ws_client(core_app)
    with pytest.raises(Exception) as ei:
        with client.websocket_connect("/events",
                                      subprotocols=[f"bossman.bearer.{tok}"]):
            pass
    code = getattr(ei.value, "code", None)
    assert code is None or code == 1008


def test_b8d_ws_token_in_url_ignored(core_app, svc):
    """Контракт «токен не в URL»: query-параметр token игнорируется — соединение
    с валидным events-токеном в query всё равно закрывается."""
    tok = _tok_sync(_token(svc, {SCOPE_EVENTS}))
    client = _ws_client(core_app)
    with pytest.raises(Exception) as ei:
        with client.websocket_connect(f"/events?token={tok}"):
            pass
    code = getattr(ei.value, "code", None)
    assert code is None or code == 1008
    # структурно: websocket_token читает ТОЛЬКО заголовок и субпротокол
    from bossman.perimeter import websocket_token
    src = __import__("inspect").getsource(websocket_token)
    assert "query" not in src.lower()
    assert "authorization" in src.lower() and "subprotocols" in src.lower()


def test_b8e_ws_valid_events_token_connects(core_app, svc):
    """Позитивный контроль: валидный events-токен субпротоколом — открытие."""
    tok = _tok_sync(_token(svc, {SCOPE_EVENTS}))
    client = _ws_client(core_app)
    with client.websocket_connect("/events",
                                  subprotocols=[f"bossman.bearer.{tok}"]):
        pass  # рукопожатие прошло (иначе было бы исключение) — соединение открыто


# ---------- эскалация скоупов ----------

async def test_b9_chat_only_device_cannot_self_grant(core_app, svc, monkeypatch):
    """admin выпускает chat-only устройство; дальше это устройство НЕ имеет
    никакого маршрута и никакого API хранилища расширить собственные скоупы."""
    admin_tok = await _token(svc, KNOWN_SCOPES)
    async with _client(core_app) as c:
        r = await c.post("/remote/devices", json={"name": "phone", "scopes": ["chat"]},
                         headers=_bearer(admin_tok))
        assert r.status_code == 200
        phone_tok = r.json()["token"]
        phone_id = r.json()["device_id"]

    # 1) сам себя пере-enroll'ить с расширением не может (нет admin)
    async with _client(core_app) as c:
        r1 = await c.post("/remote/devices",
                          json={"name": "evil", "scopes": ["chat", "approve", "admin"]},
                          headers=_bearer(phone_tok))
        assert r1.status_code == 403 and r1.json()["error"]["code"] == "SCOPE_DENIED"

    # 2) сервис/хранилище не имеют API мутации скоупов существующего устройства
    assert not any("scope" in m.lower() for m in dir(svc) if callable(getattr(svc, m))), \
        f"у DeviceService появился API мутации скоупов: " \
        f"{[m for m in dir(svc) if 'scope' in m.lower()]}"
    store_proto = [m for m in dir(InMemoryDeviceStore) if not m.startswith("_")]
    assert not any("scope" in m.lower() for m in store_proto), store_proto

    # 3) скоупы устройства в хранилище не изменились
    dev = await svc.store.get_device(phone_id)
    assert dev.scopes == frozenset({SCOPE_CHAT})

    # 4) approve-ручки остаются недостижимы для chat-only токена
    calls = {"n": 0}

    async def spy_decide(aid, approve, by):
        calls["n"] += 1
        return {"id": aid}

    monkeypatch.setattr(approvals_mod, "decide", spy_decide)
    async with _client(core_app) as c:
        r2 = await c.post("/approvals/1", json={"approve": True},
                          headers=_bearer(phone_tok))
        r3 = await c.post("/remote/approvals/1", json={"approve": True},
                          headers=_bearer(phone_tok))
    assert r2.status_code == 403 and r3.status_code == 403
    assert calls["n"] == 0


async def test_b9b_admin_without_approve_cannot_mint_approve_device(core_app, svc):
    """Анти-эскалация: admin БЕЗ approve не может выпустить устройство с approve."""
    _, tok = await svc.enroll("limited-admin", {SCOPE_ADMIN, SCOPE_CHAT})
    async with _client(core_app) as c:
        r = await c.post("/remote/devices", json={"name": "x", "scopes": ["approve"]},
                         headers=_bearer(tok))
    assert r.status_code == 403 and r.json()["error"]["code"] == "SCOPE_DENIED"


async def test_b9b2_session_token_cannot_be_used_as_device_credential(core_app, svc):
    """Сессия не порождает сессию: session-токен мимо require_device_token."""
    did, _ = await svc.enroll("phone", {SCOPE_CHAT})
    _, ses_tok = await svc.open_session(did)
    async with _client(core_app) as c:
        r = await c.post("/remote/auth", headers=_bearer(ses_tok))
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_DENIED"


# ===========================================================================
# TASK C — граница подтверждений
# ===========================================================================


def _fake_approvals_db():
    class _FakeApprovalsDB:
        """Таблица approvals с семантикой decide(): UPDATE ... WHERE status='pending'."""

        def __init__(self):
            self.rows = {}

        def add(self, aid, status="pending"):
            self.rows[aid] = {"id": aid, "status": status, "kind": "computer_action",
                              "preview": "x", "decided_by": None, "payload": {}}
            return self.rows[aid]

        async def fetchrow(self, sql, *args):
            if "UPDATE approvals" in sql:            # decide()
                aid, status, by = args
                r = self.rows.get(aid)
                if r is None or r["status"] != "pending":
                    return None
                r["status"] = status
                r["decided_by"] = by
                return dict(r)
            if "SELECT * FROM approvals WHERE id=$1" in sql:  # wait()
                r = self.rows.get(args[0])
                return dict(r) if r else None
            return None

        async def execute(self, sql, *args):          # expire
            if "SET status='expired'" in sql:
                r = self.rows.get(args[0])
                if r and r["status"] == "pending":
                    r["status"] = "expired"

    return _FakeApprovalsDB()


async def test_c1_decide_is_single_shot(core_app, svc, monkeypatch):
    """decide() по уже решённому → None (фильтр status='pending'); flip
    approved→rejected невозможен; HTTP повтор → 409."""
    fake = _fake_approvals_db()
    monkeypatch.setattr(db_mod, "fetchrow", fake.fetchrow)
    monkeypatch.setattr(db_mod, "execute", fake.execute)
    aid = 501
    fake.add(aid)

    tok = await _token(svc, {SCOPE_APPROVE})
    async with _client(core_app) as c:
        r1 = await c.post(f"/approvals/{aid}", json={"approve": True},
                          headers=_bearer(tok))
        assert r1.status_code == 200 and r1.json()["status"] == "approved"
        # повторное решение (и «перерешать» в reject) — 409, статус не меняется
        for body in ({"approve": True}, {"approve": False}):
            r2 = await c.post(f"/approvals/{aid}", json=body, headers=_bearer(tok))
            assert r2.status_code == 409
    assert fake.rows[aid]["status"] == "approved"

    # сервисный уровень: уже решённое и несуществующее → None
    assert await approvals_mod.decide(aid, True, "x") is None
    assert await approvals_mod.decide(999_999, True, "x") is None


async def test_c2_decide_after_expiry_or_reject_returns_none(monkeypatch):
    fake = _fake_approvals_db()
    monkeypatch.setattr(db_mod, "fetchrow", fake.fetchrow)
    fake.add(1)
    fake.add(2, status="rejected")
    fake.add(3, status="expired")
    assert await approvals_mod.decide(1, True, "x") is not None  # pending решается
    fake.add(4)
    fake.rows[4]["status"] = "expired"
    assert await approvals_mod.decide(4, True, "x") is None
    assert await approvals_mod.decide(2, True, "x") is None
    assert await approvals_mod.decide(3, True, "x") is None


async def test_c3_wait_resolves_only_on_decided_status(monkeypatch):
    fake = _fake_approvals_db()
    monkeypatch.setattr(db_mod, "fetchrow", fake.fetchrow)
    monkeypatch.setattr(db_mod, "execute", fake.execute)

    async def _nosleep(_):
        return None

    monkeypatch.setattr(approvals_mod.asyncio, "sleep", _nosleep)

    # pending навсегда → НЕ резолвится, истекает (execute помечает expired)
    fake.add(10)
    out = await approvals_mod.wait(10, timeout_s=2)
    assert out["status"] == "expired"
    assert fake.rows[10]["status"] == "expired"

    # решённое (approved/rejected) резолвится сразу, с самим статусом
    fake.add(11)
    fake.rows[11]["status"] = "approved"
    out = await approvals_mod.wait(11, timeout_s=100)
    assert out["status"] == "approved"
    fake.add(12)
    fake.rows[12]["status"] = "rejected"
    out = await approvals_mod.wait(12, timeout_s=100)
    assert out["status"] == "rejected"

    # pending → решённое в процессе ожидания: резолвится ТОЛЬКО после решения
    fake.add(13)
    state = {"calls": 0}

    real_fetchrow = fake.fetchrow

    async def flip_after_first(sql, *args):
        row = await real_fetchrow(sql, *args)
        if "SELECT" in sql:
            state["calls"] += 1
            if state["calls"] == 1:
                fake.rows[13]["status"] = "rejected"
        return row

    monkeypatch.setattr(db_mod, "fetchrow", flip_after_first)
    out = await approvals_mod.wait(13, timeout_s=100)
    assert out["status"] == "rejected" and state["calls"] >= 2


# ---------- computer_operator: approval → execution граница ----------

def _manager(tmp_path, actions, wait_hook):
    """Реальный ComputerOperatorManager на фейках: planner отдаёт actions,
    approval_wait управляется wait_hook(result_dict, side_effect)."""
    from bossman.computer_operator.manager import ComputerOperatorManager
    from bossman.computer_operator.models import Observation
    from bossman.computer_operator.store import JsonTaskStore

    class _Planner:
        def __init__(self, actions):
            self.actions = list(actions)

        async def next_action(self, **kw):
            if not self.actions:
                from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState
                return ComputerAction.make(ActionKind.FAIL, expected=ExpectedState(),
                                           text="planner exhausted")
            return self.actions.pop(0)

    class _Observer:
        async def observe(self, **kw):
            return Observation(id="obs", created_at=0.0, foreground={}, summary="ok paid")

    class _Router:
        def __init__(self):
            self.executed = []

        async def execute(self, action, obs):
            self.executed.append(action.id)
            return "fake-backend"

    created_ids = []

    async def approval_create(kind, preview, *, tool=None, payload=None):
        created_ids.append(kind)
        return len(created_ids)

    class _Wait:
        def __init__(self, result, side_effect):
            self.result = result
            self.side_effect = side_effect

        async def __call__(self, aid, *a, **kw):
            if self.side_effect is not None:
                await self.side_effect()
            return dict(self.result)

    mgr = ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "tasks.json"),
        planner=_Planner(actions), observer=_Observer(), action_router=_Router(),
        approval_create=approval_create, approval_wait=_Wait(*wait_hook),
        event_emit=lambda *a, **k: None)
    return mgr, mgr.action_router, created_ids


def _pay_action():
    from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState

    return ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="ok"),
                               args={"semantic": "pay"})   # consequential → approval


def _complete_action():
    from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState

    return ComputerAction.make(ActionKind.COMPLETE, expected=ExpectedState())


async def test_c4_rejected_or_expired_approval_never_executes(tmp_path):
    from bossman.computer_operator.models import TaskState

    for status in ("rejected", "expired", "unknown-status"):
        mgr, router, created = _manager(tmp_path / status, [_pay_action()],
                                        ({"status": status}, None))
        t = mgr.create_task("pay the invoice")
        await mgr.run(t.id)
        t2 = mgr.store.get(t.id)
        assert router.executed == [], f"{status}: действие выполнено!"
        assert t2.state is TaskState.FAILED
        assert status.split("-")[0] in (t2.last_error or "")
        assert created == ["computer_pay"], "подтверждение не запрашивалось"


async def test_c5_pause_between_approval_and_execution_blocks_action(tmp_path):
    """approve→pause в окне между решением и исполнением: stale-защита."""
    from bossman.computer_operator.models import TaskState

    mgr, router, _ = _manager(tmp_path, [_pay_action()], ({"status": "approved"}, None))
    t = mgr.create_task("pay the invoice")
    orig_wait = mgr.approval_wait

    async def wait_and_pause(aid, *a, **kw):
        mgr.pause(t.id)                      # пауза В ОКНЕ после approve
        return await orig_wait(aid)

    mgr.approval_wait = wait_and_pause
    await mgr.run(t.id)
    t2 = mgr.store.get(t.id)
    assert router.executed == []
    assert t2.state is TaskState.FAILED and "stale" in (t2.last_error or "").lower()


async def test_c6_stale_pending_action_id_mismatch_blocks_action(tmp_path):
    """pending_action.id не совпал с одобренным (заменён между approve и run) → отказ."""
    from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState, TaskState

    mgr, router, _ = _manager(tmp_path, [_pay_action()], ({"status": "approved"}, None))
    t = mgr.create_task("pay the invoice")
    orig_wait = mgr.approval_wait

    async def wait_and_swap(aid, *a, **kw):
        fresh = mgr.store.get(t.id)
        fresh.pending_action = ComputerAction.make(          # другая акция (другой id)
            ActionKind.TYPE, expected=ExpectedState(), text="evil")
        mgr.store.save(fresh)
        return await orig_wait(aid)

    mgr.approval_wait = wait_and_swap
    await mgr.run(t.id)
    t2 = mgr.store.get(t.id)
    assert router.executed == []
    assert t2.state is TaskState.FAILED and "stale" in (t2.last_error or "").lower()


@pytest.mark.parametrize("op", ["take_control", "stop"])
async def test_c7_operator_invalidation_between_approval_and_execution(tmp_path, op):
    """Любая операторская инвалидация (take_control/stop — они bump'ают
    generation и чистят pending) в окне между approve и исполнением блокирует
    акцию: generation теперь токен инвалидации."""
    from bossman.computer_operator.models import TaskState

    mgr, router, _ = _manager(tmp_path, [_pay_action()], ({"status": "approved"}, None))
    t = mgr.create_task("pay the invoice")
    orig_wait = mgr.approval_wait

    async def wait_and_invalidate(aid, *a, **kw):
        getattr(mgr, op)(t.id)               # оператор бьёт по задаче В ОКНЕ
        return await orig_wait(aid)

    mgr.approval_wait = wait_and_invalidate
    await mgr.run(t.id)
    t2 = mgr.store.get(t.id)
    assert router.executed == []
    assert t2.state is TaskState.FAILED
    assert "stale" in (t2.last_error or "").lower()


async def test_c8_emergency_lock_between_approval_and_execution_blocks_action(tmp_path):
    """approve при global_locked (emergency_lock в окне одобрения): акция НЕ исполняется."""
    from bossman.computer_operator.models import TaskState

    mgr, router, _ = _manager(tmp_path, [_pay_action()], ({"status": "approved"}, None))
    t = mgr.create_task("pay the invoice")
    orig_wait = mgr.approval_wait

    async def wait_and_lock(aid, *a, **kw):
        mgr.emergency_lock()                  # emergency_lock ПОСЛЕ нажатия approve
        return await orig_wait(aid)

    mgr.approval_wait = wait_and_lock
    await mgr.run(t.id)
    t2 = mgr.store.get(t.id)
    assert router.executed == [], "экстренная блокировка не остановила исполнение!"
    assert t2.state in (TaskState.FAILED, TaskState.LOCKED)
    assert mgr.global_locked is True


async def test_c9_approve_while_globally_locked_before_run_blocks_action(tmp_path):
    """global_locked ещё до запуска: run() не начинает цикл, approve не помогает."""
    from bossman.computer_operator.models import TaskState

    mgr, router, created = _manager(tmp_path, [_pay_action()],
                                    ({"status": "approved"}, None))
    t = mgr.create_task("pay the invoice")
    mgr.emergency_lock()                      # замок ДО запуска
    await mgr.run(t.id)
    t2 = mgr.store.get(t.id)
    assert router.executed == []
    assert created == [], "approval_create не должен был вызываться под замком"
    assert t2.state in (TaskState.LOCKED, TaskState.FAILED)


async def test_c10_positive_control_approved_action_executes(tmp_path):
    """Контроль харнесса: чистый approve → акция исполняется и верифицируется."""
    from bossman.computer_operator.models import TaskState

    pay = _pay_action()
    mgr, router, _ = _manager(tmp_path, [pay, _complete_action()],
                              ({"status": "approved"}, None))
    t = mgr.create_task("pay the invoice")
    await mgr.run(t.id)
    t2 = mgr.store.get(t.id)
    assert router.executed == [pay.id], "одобренная акция не исполнилась"
    assert t2.state is TaskState.COMPLETED
