"""RC19 security audit — регрессии найденных дыр API/UI.

Каждый тест падал до исправления и описывает конкретный путь атаки или сбоя.
"""
from __future__ import annotations

import pytest


# ------------------------------------------------ P1-1: WS и чужой Origin

def _login(client, svc):
    assert client.post("/api/login", json={"token": svc.auth.token}).status_code == 200


def test_ws_refuses_cookie_from_another_local_origin(env):
    """Страница на другом порту того же хоста (SameSite порт не различает)
    несёт cookie владельца; ленту событий она читать не должна."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    with TestClient(env.app) as client:
        _login(client, env.svc)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/events", headers={"Origin": "http://testserver:5173"}) as ws:
                ws.receive_json()
        assert exc.value.code == 4403
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/events", headers={"Origin": "null"}) as ws:
                ws.receive_json()


def test_ws_accepts_same_origin_and_non_browser_clients(env):
    """Собственный UI (тот же host:port) и CLI без Origin продолжают работать."""
    from fastapi.testclient import TestClient
    with TestClient(env.app) as client:
        _login(client, env.svc)
        with client.websocket_connect(
                "/api/events", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json()["kind"] == "hello"
        with client.websocket_connect("/api/events") as ws:
            assert ws.receive_json()["kind"] == "hello"
