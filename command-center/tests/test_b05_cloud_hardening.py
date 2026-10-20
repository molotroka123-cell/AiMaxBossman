"""Branch 5 «Cloud»: provider-client hardening. Every test uses fakes; no real API is called."""
from __future__ import annotations

import httpx
import pytest

from bcc import model_health as mh
from bcc.providers import OpenAICompatAdapter, ProviderError


# ------------------------------------------------------------------ timeouts are timeouts

async def test_a_provider_timeout_is_classified_as_timeout_not_provider_down():
    """The adapter words a timeout in Russian («не ответил за N с»); the health classifier only
    knew the English "timed out", so a slow free endpoint was filed as provider_down."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    adapter = OpenAICompatAdapter(base_url="https://integrate.api.nvidia.com/v1", api_key="k",
                                  transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        await adapter.chat("m", [{"role": "user", "content": "hi"}], timeout=1)
    assert info.value.kind == "network" and info.value.timed_out is True
    assert mh.classify_exception(info.value)[0] == mh.TIMEOUT


def test_a_refused_connection_is_still_provider_down():
    """Negative control: only a real timeout moves to TIMEOUT."""
    assert mh.classify_exception(ProviderError("нет связи", kind="network"))[0] == mh.PROVIDER_DOWN


# ------------------------------------------------------------------ key redaction in errors

FAKE_KEY = "nvapi-SYNTHETIC0TEST0KEY0not0real0000"


@pytest.mark.parametrize("status,body", [
    (401, {"error": {"message": f"Invalid API key provided: {FAKE_KEY}"}}),
    (403, f"Authorization: Bearer {FAKE_KEY} rejected"),
])
async def test_a_provider_that_echoes_the_key_never_puts_it_in_the_error(status, body):
    def handler(request: httpx.Request) -> httpx.Response:
        return (httpx.Response(status, json=body) if isinstance(body, dict)
                else httpx.Response(status, text=body))

    adapter = OpenAICompatAdapter(base_url="https://integrate.api.nvidia.com/v1", api_key=FAKE_KEY,
                                  transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        await adapter.chat("m", [{"role": "user", "content": "hi"}])
    assert FAKE_KEY not in str(info.value)
    assert info.value.status_code == status and str(status) in str(info.value)   # still diagnosable


async def test_an_in_body_gateway_error_echoing_the_key_is_redacted():
    other = "sk-or-v1-SYNTHETIC0TEST0KEY0not0real0000"
    body = {"error": {"code": 401, "message": f"No auth credentials found for {other} / {FAKE_KEY}"}}
    adapter = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1", api_key=FAKE_KEY,
                                  transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(ProviderError) as info:
        await adapter.chat("m", [{"role": "user", "content": "hi"}])
    assert FAKE_KEY not in str(info.value) and other not in str(info.value)
    assert "No auth credentials" in str(info.value)


# ------------------------------------------------------------------ HTTP 429 is a rate limit

async def test_an_http_429_is_a_rate_limit_with_the_providers_retry_after():
    """An in-body 429 already was kind="rate_limit"; a real HTTP 429 was kind="http" and its
    Retry-After was thrown away, so callers could not tell "wait 7 s" from "broken"."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "slow down"})

    adapter = OpenAICompatAdapter(base_url="https://api.groq.com/openai/v1", api_key="k",
                                  transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        await adapter.chat("m", [{"role": "user", "content": "hi"}])
    exc = info.value
    assert (exc.kind, exc.status_code, exc.retry_after) == ("rate_limit", 429, 7.0)
    assert "(429)" in str(exc) and len(calls) == 1        # no hidden extra request
    assert mh.classify_exception(exc)[0] == mh.THROTTLED


def test_retry_after_parsing_is_bounded_and_never_invents_zero():
    from bcc.providers import MAX_RETRY_AFTER_S, parse_retry_after
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("999999999") == MAX_RETRY_AFTER_S
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", now=1792567650.0) == 30.0
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1792567650.0) == 0.0   # past date
    for junk in (None, "", "soon", "-5", "1.5e9"):
        assert parse_retry_after(junk) is None, junk


async def test_a_400_stays_an_http_error_without_retry_after():
    adapter = OpenAICompatAdapter(base_url="https://api.groq.com/openai/v1", api_key="k",
                                  transport=httpx.MockTransport(
                                      lambda r: httpx.Response(400, headers={"Retry-After": "5"}, text="bad")))
    with pytest.raises(ProviderError) as info:
        await adapter.chat("m", [{"role": "user", "content": "hi"}])
    assert (info.value.kind, info.value.retry_after) == ("http", None)
