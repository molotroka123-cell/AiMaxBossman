"""SECREM — ограниченный идемпотентный retry в GatewayClient (429/503/сеть).

Только chat/completions (без побочных эффектов). Бюджет: retry_max повторов и
общий дедлайн; исчерпано → честная ошибка, не тихий None; 4xx/5xx вне
{429,503} не повторяются; Retry-After уважается.
"""
from __future__ import annotations

import httpx
import pytest

from bossman.gateway.client import GatewayClient, _retry_after_seconds


def _gc(script, **kw):
    """script: список статусов ('E' = ConnectError)."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        code = script[min(len(calls), len(script) - 1)]
        calls.append(code)
        if code == "E":
            raise httpx.ConnectError("boom", request=request)
        hdr = {"retry-after": "2"} if code == 429 and kw.get("retry_after") else {}
        return httpx.Response(code, json={"choices": [], "error": {"code": "x"}}, headers=hdr)
    gc = GatewayClient(base_url="http://gw/v1", api_key="k", retry_max=kw.get("retry_max", 4),
                       retry_deadline_s=kw.get("deadline", 60.0), retry_base_s=0.5)
    gc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sleeps = []
    clock = {"t": 0.0}

    async def fake_sleep(d):
        sleeps.append(d)
        clock["t"] += d
    gc._sleep = fake_sleep
    gc._rand = lambda: 0.5          # jitter → ровно base
    gc._clock = lambda: clock["t"]
    return gc, calls, sleeps


async def test_429_then_ok_retries_with_backoff():
    gc, calls, sleeps = _gc([429, 429, 200])
    data = await gc.chat(model="m", messages=[])
    assert calls == [429, 429, 200] and data["choices"] == []
    assert sleeps == [0.5, 1.0]                   # base * 2**attempt, jitter=1.0x


async def test_retry_after_header_wins():
    gc, calls, sleeps = _gc([429, 200], retry_after=True)
    await gc.chat(model="m", messages=[])
    assert sleeps == [2.0]


async def test_exhausted_budget_raises_last_error():
    gc, calls, sleeps = _gc([429], retry_max=2)
    with pytest.raises(httpx.HTTPStatusError) as ei:
        await gc.chat(model="m", messages=[])
    assert ei.value.response.status_code == 429
    assert calls == [429, 429, 429] and len(sleeps) == 2


async def test_deadline_stops_early():
    gc, calls, sleeps = _gc([503], retry_max=10, deadline=1.2)
    with pytest.raises(httpx.HTTPStatusError):
        await gc.chat(model="m", messages=[])
    assert sum(sleeps) <= 1.2 and len(calls) <= 3


async def test_non_retryable_status_not_retried():
    gc, calls, sleeps = _gc([500])
    with pytest.raises(httpx.HTTPStatusError):
        await gc.chat(model="m", messages=[])
    assert calls == [500] and sleeps == []


async def test_connect_error_retried_then_ok():
    gc, calls, sleeps = _gc(["E", 200])
    await gc.chat(model="m", messages=[])
    assert calls == ["E", 200] and len(sleeps) == 1


def test_retry_after_parsing():
    assert _retry_after_seconds("3") == 3.0
    assert _retry_after_seconds("-1") == 0.0
    assert _retry_after_seconds("garbage") is None
    assert _retry_after_seconds(None) is None
    assert _retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0   # в прошлом → 0
