"""Прокси Poker Vision: один HTTP-клиент на цикл событий, а не новый на каждый вызов.

Воспроизведено 2026-10-08 на 1eac8ac8 (до правки): 20 проксированных вызовов `_call` —
20 экземпляров `httpx.AsyncClient`; постройка одного — ~18 мс синхронного CPU (SSL-контекст)
на этой машине, 435 мс на 20 вызовов против loopback-сервера. Страница опрашивает
overlay.json 4 раза в секунду (ui/pages/poker_vision.js: setInterval(overlayOnce, 250)).

Здесь без cv2/uvicorn: настоящий loopback-сокет с keep-alive и настоящий `_call`.
Правила выхода в сеть прежние: тот же TIMEOUT, trust_env=False, только loopback.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from bcc.features import poker_vision as pv


async def _keep_alive_server(connections: list[asyncio.StreamWriter]):
    """HTTP/1.1 с keep-alive: отвечает {"path": ...} на каждый запрос соединения."""
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                length = 0
                while (header := await reader.readline()) not in (b"\r\n", b""):
                    name, _, value = header.decode("latin-1").partition(":")
                    if name.strip().lower() == "content-length":
                        length = int(value.strip())
                if length:
                    await reader.readexactly(length)
                body = b'{"path": "' + line.split()[1] + b'"}'
                writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                             b"content-length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                await writer.drain()
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.fixture
def counted_clients(monkeypatch):
    made: list[dict] = []
    real = httpx.AsyncClient

    def counting(*a, **kw):
        made.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(pv.httpx, "AsyncClient", counting)
    monkeypatch.setattr(pv, "_client", None, raising=False)
    return made


async def test_proxied_calls_share_one_client_and_keep_the_egress_rules(counted_clients, monkeypatch):
    connections: list[asyncio.StreamWriter] = []
    server, port = await _keep_alive_server(connections)
    monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", f"http://127.0.0.1:{port}")
    try:
        for _ in range(3):
            assert await pv._call("GET", "/api/v1/overlay.json") == {"path": "/api/v1/overlay.json"}
        assert await pv._call("POST", "/api/v1/desk/pause", {"paused": True}) == {"path": "/api/v1/desk/pause"}
        raw = await pv._call("GET", "/api/v1/frame.jpg", raw=True)
        assert raw.status_code == 200
    finally:
        if pv._client is not None:
            await pv._client.aclose()
        server.close()
        for w in connections:
            w.close()
    assert len(counted_clients) == 1, f"клиентов на 5 вызовов: {len(counted_clients)}"
    assert counted_clients[0]["timeout"] is pv.TIMEOUT and counted_clients[0]["trust_env"] is False
    assert len(connections) == 1, "keep-alive: пять вызовов — одно соединение"


async def test_service_gone_is_still_503_and_the_override_is_still_loopback_only(counted_clients, monkeypatch):
    connections: list[asyncio.StreamWriter] = []
    server, port = await _keep_alive_server(connections)
    monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", f"http://127.0.0.1:{port}")
    assert await pv._call("GET", "/api/v1/state") == {"path": "/api/v1/state"}
    server.close()
    for w in connections:
        w.close()
    await server.wait_closed()
    # Соединение в пуле уже мёртвое, порт закрыт: ответ — прежний 503, а не зависание/500.
    with pytest.raises(HTTPException) as down:
        await pv._call("GET", "/api/v1/state")
    assert down.value.status_code == 503 and down.value.detail["code"] == "PV_SERVICE_DOWN"

    monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", "http://evil.example.com:80")
    with pytest.raises(HTTPException) as bad:
        await pv._call("GET", "/api/v1/state")
    assert bad.value.status_code == 500 and bad.value.detail["code"] == "PV_BAD_OVERRIDE"
    if pv._client is not None:
        await pv._client.aclose()


def test_the_shared_client_never_crosses_event_loops(counted_clients, monkeypatch):
    """Клиент привязан к циклу, в котором открыл соединения. Новый цикл (перезапуск
    сервера, другой тест) получает свой клиент, а не «Event loop is closed»."""
    async def one_round(close_client: bool) -> dict:
        connections: list[asyncio.StreamWriter] = []
        server, port = await _keep_alive_server(connections)
        monkeypatch.setenv("BOSSMAN_POKER_VISION_URL", f"http://127.0.0.1:{port}")
        try:
            return await pv._call("GET", "/api/v1/status")
        finally:
            if close_client and pv._client is not None:
                await pv._client.aclose()
            server.close()
            for w in connections:
                w.close()

    # Первый цикл оставляет клиент с keep-alive соединением — именно этот случай и проверяется.
    assert asyncio.run(one_round(close_client=False)) == {"path": "/api/v1/status"}
    assert asyncio.run(one_round(close_client=True)) == {"path": "/api/v1/status"}
    assert len(counted_clients) == 2
