"""V2.1 фаза N — безопасность доступа.

Было: вечный токен в localStorage и он же в query-строке WebSocket (попадал в
логи прокси). Стало: серверная сессия + HttpOnly-cookie + CSRF на изменяющих
методах; WS берёт cookie. Legacy-заголовок остаётся позади явного флага.
"""
import httpx
import pytest
import sqlalchemy as sa

from bcc.auth import HEADER
from bcc.config import Settings
from bcc.db import sessions as sessions_t
from bcc.sessions import COOKIE_NAME, CSRF_HEADER

from .conftest import make_settings, start_app


def _anon(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _login(client, svc, **kw):
    resp = await client.post("/api/login", json={"token": svc.auth.token, **kw})
    assert resp.status_code == 200, resp.text
    return resp


# ---------------------------------------------------------------- сессия

async def test_login_sets_httponly_cookie_and_returns_csrf(env):
    async with _anon(env.app) as client:
        resp = await _login(client, env.svc, label="телефон")
        raw = resp.headers.get("set-cookie") or ""
        assert COOKIE_NAME in raw
        assert "HttpOnly" in raw            # JS не прочитает — не украсть XSS'ом
        assert "SameSite=strict" in raw.lower().replace("samesite=strict", "SameSite=strict") \
            or "samesite=strict" in raw.lower()
        assert "Path=/" in raw
        body = resp.json()
        assert body["csrf"] and body["csrf_header"] == CSRF_HEADER
        # сам идентификатор сессии в теле НЕ возвращается
        assert not any(str(v) == client.cookies.get(COOKIE_NAME) for v in body.values())


async def test_cookie_session_authenticates_get_without_token(env):
    async with _anon(env.app) as client:
        await _login(client, env.svc)
        resp = await client.get("/api/models")           # ни заголовка, ни query
        assert resp.status_code == 200


async def test_unauthenticated_api_is_401(env):
    async with _anon(env.app) as client:
        assert (await client.get("/api/models")).status_code == 401
        assert (await client.post("/api/agents", json={"name": "x"})).status_code == 401


async def test_mutation_by_cookie_requires_csrf(env):
    async with _anon(env.app) as client:
        csrf = (await _login(client, env.svc)).json()["csrf"]

        # без CSRF-заголовка изменяющий метод отклоняется
        bad = await client.post("/api/agents", json={"name": "агент"})
        assert bad.status_code == 403
        assert "CSRF" in bad.json()["error"]["message"].upper()

        # с чужим значением — тоже (заголовки только ASCII)
        wrong = await client.post("/api/agents", json={"name": "агент"},
                                  headers={CSRF_HEADER: "forged-value"})
        assert wrong.status_code == 403

        ok = await client.post("/api/agents", json={"name": "агент"},
                               headers={CSRF_HEADER: csrf})
        assert ok.status_code == 200


async def test_logout_invalidates_session_server_side(env):
    async with _anon(env.app) as client:
        csrf = (await _login(client, env.svc)).json()["csrf"]
        sid = client.cookies.get(COOKIE_NAME)
        assert (await client.get("/api/models")).status_code == 200

        out = await client.post("/api/logout")
        assert out.status_code == 200 and out.json()["revoked"] is True

        # даже если браузер «забыл» удалить cookie — сервер её больше не примет
        client.cookies.set(COOKIE_NAME, sid)
        assert (await client.get("/api/models")).status_code == 401
        assert (await client.post("/api/agents", json={"name": "x"},
                                  headers={CSRF_HEADER: csrf})).status_code == 401

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(sessions_t.c.revoked))).first()
    assert row[0] is True


async def test_expired_session_is_rejected(env):
    """Просроченная сессия не проходит, даже если cookie цела."""
    async with _anon(env.app) as client:
        await _login(client, env.svc)
        assert (await client.get("/api/models")).status_code == 200
        async with env.svc.db.session() as s:
            await s.execute(sa.update(sessions_t).values(
                expires_at=sa.text("'2000-01-01 00:00:00'")))
            await s.commit()
        assert (await client.get("/api/models")).status_code == 401


async def test_session_id_never_appears_in_urls(env):
    """Секрет не должен утекать в URL/логи прокси — ни в одном пути."""
    async with _anon(env.app) as client:
        await _login(client, env.svc)
        sid = client.cookies.get(COOKIE_NAME)
        for path in ("/api/models", "/api/agents", "/api/system", "/api/approvals"):
            resp = await client.get(path)
            assert sid not in str(resp.url)
            assert sid not in resp.text


# ---------------------------------------------------------------- WebSocket

def test_ws_authenticates_from_cookie(env):
    """WS больше не требует токен в query: cookie той же сессии достаточно."""
    from fastapi.testclient import TestClient
    with TestClient(env.app) as client:
        login = client.post("/api/login", json={"token": env.svc.auth.token})
        assert login.status_code == 200
        with client.websocket_connect("/api/events") as ws:   # без ?token=
            assert ws.receive_json()["kind"] == "hello"


def test_ws_rejects_anonymous(env):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    with TestClient(env.app) as client:
        client.cookies.clear()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/events") as ws:
                ws.receive_json()


# ---------------------------------------------------------------- legacy

async def test_legacy_header_still_works_by_default(env):
    """CLI и скрипты не ломаются: заголовок принимается, пока включён legacy."""
    async with _anon(env.app) as client:
        resp = await client.get("/api/models", headers={HEADER: env.svc.auth.token})
        assert resp.status_code == 200
        # заголовок не подвержен CSRF — мутация проходит без CSRF-заголовка
        made = await client.post("/api/agents", json={"name": "cli-агент"},
                                 headers={HEADER: env.svc.auth.token})
        assert made.status_code == 200


async def test_legacy_mode_can_be_disabled(tmp_path):
    """BCC_LEGACY_TOKEN=0 — только сессии; вечный токен больше не принимается."""
    settings = make_settings(tmp_path)
    settings.legacy_token_auth = False
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with _anon(app) as client:
            denied = await client.get("/api/models", headers={HEADER: svc.auth.token})
            assert denied.status_code == 401
            assert "login" in (denied.json()["error"].get("hint") or "")

            await _login(client, svc)
            assert (await client.get("/api/models")).status_code == 200
    finally:
        await svc.stop()


async def test_ws_query_token_refused_when_legacy_disabled(tmp_path):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    settings = make_settings(tmp_path)
    settings.legacy_token_auth = False
    app, svc = await start_app(settings, start_workers=False)
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"/api/events?token={svc.auth.token}") as ws:
                    ws.receive_json()
    finally:
        await svc.stop()


def test_cookie_secure_flag_matches_scheme():
    """Secure по http ставить нельзя — иначе localhost/Tailscale перестанут работать."""
    from bcc.sessions import cookie_kwargs
    assert cookie_kwargs("https", "auto", 1)["secure"] is True
    assert cookie_kwargs("http", "auto", 1)["secure"] is False
    assert cookie_kwargs("http", "always", 1)["secure"] is True
    assert cookie_kwargs("https", "never", 1)["secure"] is False
    common = cookie_kwargs("http", "auto", 2)
    assert common["httponly"] is True and common["samesite"] == "strict"
    assert common["max_age"] == 7200


def test_settings_defaults_are_safe():
    s = Settings()
    assert s.host == "127.0.0.1"          # наружу по умолчанию не слушаем
    assert s.legacy_token_auth is True    # переходный режим, документирован
    assert s.cookie_secure == "auto"
