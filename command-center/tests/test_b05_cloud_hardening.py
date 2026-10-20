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


# ------------------------------------------------------------------ health cooldown honours Retry-After

def test_the_health_cooldown_is_never_shorter_than_the_providers_retry_after():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    rec = mh.record_observation(None, mh.THROTTLED, "429", now=now, retry_after_s=900)
    assert rec.cooldown_until - now == timedelta(seconds=900)          # not the 60 s default
    capped = mh.record_observation(None, mh.THROTTLED, "429", now=now, retry_after_s=10 ** 9)
    assert capped.cooldown_until - now == timedelta(seconds=mh.MAX_COOLDOWN_SECONDS)
    short = mh.record_observation(None, mh.THROTTLED, "429", now=now, retry_after_s=5)
    assert short.cooldown_until - now == timedelta(seconds=mh.BASE_COOLDOWN_SECONDS)  # never shortened
    ok = mh.record_observation(None, mh.HEALTHY, "", now=now, retry_after_s=900)
    assert ok.cooldown_until is None


async def test_a_probe_hit_by_429_parks_the_model_for_the_providers_window(env):
    from .conftest import FakeAdapter
    from .helpers import make_stack

    class RateLimited(FakeAdapter):
        async def chat(self, model, messages, **kw):
            raise ProviderError("лимит запросов провайдера (429)", kind="rate_limit",
                                status_code=429, retry_after=1200.0)

    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    env.svc.registry.adapter_factory = lambda m, p: RateLimited()
    with pytest.raises(ProviderError):
        await env.svc.registry.test_model(mid)
    record = await env.svc.registry.model_health(mid)
    assert record.status == mh.THROTTLED
    assert (record.cooldown_until - record.checked_at).total_seconds() >= 1200


# ------------------------------------------------------------------ long-run spend caps

OR = "https://openrouter.ai/api/v1"


@pytest.fixture
def lr_root(tmp_path, monkeypatch):
    from bcc import long_run
    monkeypatch.delenv("BOSSMAN_ALLOW_PAID_CLOUD", raising=False)
    monkeypatch.delenv("BOSSMAN_LOCAL_ONLY", raising=False)
    root = tmp_path / "autonomy"
    monkeypatch.setenv("BOSSMAN_AUTONOMY_ROOT", str(root))
    long_run.write_profile(enable=True, root=root)
    return root


@pytest.mark.parametrize("worst", [float("nan"), float("inf"), "n/a", None])
def test_an_unknown_worst_case_is_refused_not_booked_as_free(lr_root, worst):
    """max(0.0, nan) == 0.0: a NaN estimate was reserved as a zero-cost call and passed the cap."""
    from bcc import long_run
    ledger = long_run.LongRunLedger(lr_root)
    with pytest.raises(long_run.LongRunRefused, match="unknown|CAP HIT"):
        ledger.reserve("glm", worst, model="z-ai/glm-5.3-flash")
    rows = ledger._rows()
    assert not [r for r in rows if r.get("kind") == "reserve"]
    assert [r for r in rows if r.get("kind") == "refusal"]


class _Recorder:
    def __init__(self, cost=0.0001, tokens=(10, 5)):
        self.kwargs = []
        self.cost, self.tokens = cost, tokens

    async def chat(self, *args, **kw):
        from bcc.providers import ChatResult
        self.kwargs.append(dict(kw))
        meta = {"usage": {"cost": self.cost}} if self.cost is not None else {}
        return ChatResult(text="ok", tokens_in=self.tokens[0], tokens_out=self.tokens[1], provider_meta=meta)


def _paid(name="z-ai/glm-5.3-flash", pin=0.5, pout=2.0):
    return {"kind": "cloud", "name": name, "alias": name, "pricing_known": True,
            "price_in": pin, "price_out": pout}


async def test_a_long_run_call_binds_the_provider_to_the_reserved_output_budget(lr_root):
    """The reservation assumed 2048 output tokens, but the request went out without
    max_tokens: the provider was free to answer past the booked worst case."""
    from bcc.provider_governance import GovernedAdapter
    inner = _Recorder()
    gov = GovernedAdapter(inner, {"kind": "openai_compat", "base_url": OR, "name": "or"}, _paid())
    await gov.chat("z-ai/glm-5.3-flash", [{"role": "user", "content": "hi"}])
    assert inner.kwargs[-1]["max_tokens"] == 2048
    await gov.chat("z-ai/glm-5.3-flash", [{"role": "user", "content": "hi"}], max_tokens=77)
    assert inner.kwargs[-1]["max_tokens"] == 77                    # an explicit budget is kept


def test_settled_cost_without_a_provider_figure_uses_the_registry_units():
    """Registry prices are USD per 1M tokens; the settle step multiplied raw prices by tokens."""
    from bcc import long_run
    from bcc.providers import ChatResult
    m = {"price_in": 2.0, "price_out": 6.0}                          # USD / 1M tokens
    res = ChatResult(text="ok", tokens_in=1000, tokens_out=500)
    assert long_run.actual_eur(m, res) == pytest.approx((1000 * 2.0 + 500 * 6.0) / 1e6)
    per_token = {"price_in": 2.0e-6, "price_out": 6.0e-6, "price_unit": "per_token"}
    assert long_run.actual_eur(per_token, res) == pytest.approx(long_run.actual_eur(m, res))
    # settled <= reserved worst case for the same call (the reservation is an upper bound)
    worst = long_run.estimate_worst_case_eur(m, [{"role": "user", "content": "x" * 1000}], 500)
    assert long_run.actual_eur(m, res) <= worst


async def test_an_uncosted_paid_call_does_not_close_its_line_for_the_day(lr_root):
    from bcc import long_run
    from bcc.provider_governance import GovernedAdapter
    inner = _Recorder(cost=None, tokens=(1000, 500))
    gov = GovernedAdapter(inner, {"kind": "openai_compat", "base_url": "https://api.mistral.ai/v1",
                                  "name": "mistral"}, _paid("mistral-large-4", 2.0, 6.0))
    for _ in range(3):
        await gov.chat("mistral-large-4", [{"role": "user", "content": "hi"}], max_tokens=500)
    assert len(inner.kwargs) == 3
    assert long_run.LongRunLedger(lr_root).spent_today()["mistral"] == pytest.approx(3 * 0.005)


@pytest.mark.parametrize("pin,pout", [(0, 0), (0.5, 0), (0, 2.0)])
async def test_a_paid_family_priced_zero_is_refused_before_the_call(lr_root, pin, pout):
    """0/0 on glm-5.3-flash (a hand-typed row, a stale catalog) booked every call as EUR 0:
    the daily cap could never bind before the money was spent."""
    from bcc import long_run
    from bcc.provider_governance import GovernedAdapter
    inner = _Recorder(cost=None)
    gov = GovernedAdapter(inner, {"kind": "openai_compat", "base_url": OR, "name": "or"},
                          _paid(pin=pin, pout=pout))
    with pytest.raises(ProviderError) as info:
        await gov.chat("z-ai/glm-5.3-flash", [{"role": "user", "content": "hi"}], max_tokens=10)
    assert info.value.kind == "budget" and "zero price" in str(info.value)
    assert inner.kwargs == []                                                     # nothing sent
    rows = long_run.LongRunLedger(lr_root)._rows()
    assert [r for r in rows if r.get("kind") == "refusal"] and not [r for r in rows if r.get("kind") == "reserve"]


async def test_a_free_variant_priced_zero_is_not_affected(lr_root):
    """Negative control: ':free' is not a long-run family, its 0/0 stays legitimate."""
    from bcc.provider_governance import GovernedAdapter
    inner = _Recorder(cost=None)
    gov = GovernedAdapter(inner, {"kind": "openai_compat", "base_url": OR, "name": "or"},
                          _paid("z-ai/glm-5.3-flash:free", 0, 0))
    await gov.chat("z-ai/glm-5.3-flash:free", [{"role": "user", "content": "hi"}], max_tokens=10)
    assert len(inner.kwargs) == 1


def test_a_finite_worst_case_still_reserves(lr_root):
    from bcc import long_run
    ledger = long_run.LongRunLedger(lr_root)
    assert ledger.reserve("glm", 0.01, model="z-ai/glm-5.3-flash")
    assert ledger.spent_today()["glm"] == pytest.approx(0.01)
