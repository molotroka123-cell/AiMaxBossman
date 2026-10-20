"""B11 #8 — `safe_get` не отдаёт Authorization/Cookie чужому хосту по redirect.

Дефект: redirect'ы safe_get переходит сам (каждый hop перепроверяется на SSRF),
но заголовки вызывающего — включая `Authorization: Bearer <токен>` — уходили и
на следующий hop. Публичный хост, отвечающий `302 Location: https://evil/…`,
получал токен владельца. httpx/requests при своём следовании redirect'ам такие
заголовки на чужой origin не передают; safe_get обходил эту защиту, потому что
follow_redirects=False. Сеть не используется: DNS и транспорт — двойники.
"""
from __future__ import annotations

import socket

import httpx

from bcc.plugin_security import safe_get

SEEN: dict[str, dict] = {}


def _install(monkeypatch, handler):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: [(socket.AF_INET, None, None, "", ("1.2.3.4", 0))])
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(*a, follow_redirects=False,
                                             transport=httpx.MockTransport(handler),
                                             **{kk: vv for kk, vv in k.items()
                                                if kk not in ("follow_redirects", "transport")}))


def _handler(request: httpx.Request) -> httpx.Response:
    SEEN[f"{request.url.host}{request.url.path}"] = dict(request.headers)
    if request.url.path == "/start":
        return httpx.Response(302, headers={"location": "https://other.example/land"})
    if request.url.path == "/same":
        return httpx.Response(302, headers={"location": "/next"})
    return httpx.Response(200, text="ok")


HEADERS = {"Authorization": "Bearer owner-token-value", "Cookie": "sid=owner-session",
           "Accept": "application/json"}


async def test_credentials_not_forwarded_cross_origin(monkeypatch):
    SEEN.clear()
    _install(monkeypatch, _handler)
    r = await safe_get("https://api.example/start", headers=dict(HEADERS))
    assert r.status_code == 200
    assert SEEN["api.example/start"]["authorization"] == "Bearer owner-token-value"
    landed = SEEN["other.example/land"]
    assert "authorization" not in landed and "cookie" not in landed
    assert landed["accept"] == "application/json"     # безопасные заголовки остаются


async def test_same_origin_redirect_keeps_credentials(monkeypatch):
    SEEN.clear()
    _install(monkeypatch, _handler)
    r = await safe_get("https://api.example/same", headers=dict(HEADERS))
    assert r.status_code == 200
    assert SEEN["api.example/next"]["authorization"] == "Bearer owner-token-value"
