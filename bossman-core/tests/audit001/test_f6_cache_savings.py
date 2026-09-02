"""AUDIT-ONLY-001 / F6-CACHE-FALSE-SAVINGS -- independent verification.

Fable's claim (truncated, so treated as a hypothesis only): the context-waste
detector / WasteSignal in ``bossman_shared/cache_intelligence.py`` produces false
savings.

What the real pipeline looks like (read end to end, no guessing):

* ``bossman_shared/cache_intelligence.py`` -- ``detect_context_waste`` /
  ``WasteSignal`` compute NO money at all.  They emit qualitative signals.
  ``cache_advice`` even refuses to claim savings when ``cache_control`` was
  applied without provider usage.  Nothing here can produce a dollar number.
* ``bossman_shared/cache_observation.py`` -- honest: ``classify()`` derives
  HIT/WRITE/MISS/BYPASS/UNKNOWN from PROVIDER usage only, and ``cost_pair()``
  explicitly returns ``actual=None`` when the price of a NON-EMPTY bucket is
  unknown ("... а не выдуманная экономия").  ``ObservationLog.summary()`` keeps
  ``measured_actual_cost_usd`` and ``estimated_baseline_cost_usd`` separate and
  exports ``unknown_cost_requests`` / ``cache_control_without_usage``.
* ``command-center/bcc/features/cache_intel.py`` -- ``economics()`` nulls
  ``estimated.saved_usd`` whenever ``unknown_cost_requests`` is non-zero, and the
  UI (``command-center/ui/pages.js``) renders "не может быть заявлена".

So the *shared* layer is honest.  The money is produced somewhere else:

    bossman/gateway/app.py::_cache_economics  ->  GatewayMetrics.end_cache
    ->  GatewayMetrics.snapshot()["prompt_cache"]["saved_usd"]
    ->  bossman/api.py::list_models()["prompt_cache"]
    ->  bossman-core/ui/index.html  ->  row  "Saved $"

``_cache_economics`` never calls ``cost_pair``; it re-implements the economics
with its own fallbacks.  These tests probe that end-to-end path with real
provider bodies through the real FastAPI gateway.

Tests named ``test_red_*`` are expected to FAIL against current code (real gap).
Tests named ``test_pin_*`` are green characterisation tests pinning behaviour
that is already correct and must not regress.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
CORE = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(CORE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bossman.gateway.app import create_gateway_app  # noqa: E402
from bossman.gateway.backends import OpenAIBackend  # noqa: E402
from bossman.gateway.config import (  # noqa: E402
    AliasConfig,
    BackendConfig,
    ClientConfig,
    GatewayConfig,
    ModelTarget,
)
from bossman.gateway.prompt_cache import stable_session_id  # noqa: E402
from bossman.gateway.router import ModelRouter  # noqa: E402
from bossman.gateway.telemetry import GatewayMetrics  # noqa: E402

from bossman_shared import cache_intelligence as ci  # noqa: E402
from bossman_shared import cache_observation as co  # noqa: E402


MODEL = "anthropic/claude-opus-5"

# Configured price table for the route under test (USD per MILLION tokens).
P_IN = Decimal("5")
P_OUT = Decimal("25")
P_CACHE_READ = Decimal("0.5")
P_CACHE_WRITE_5M = Decimal("6.25")


def _gateway(handler, *, cache_enabled: bool = True, with_prices: bool = True,
             with_cache_prices: bool = True):
    backend_cfg = BackendConfig(
        "openrouter", "http://openrouter", kind="openrouter", cloud=True,
        prompt_cache_enabled=cache_enabled, prompt_cache_ttl="5m",
    )
    extra: dict[str, object] = {}
    if with_prices:
        extra["price_usd_per_million_input_tokens"] = str(P_IN)
        extra["price_usd_per_million_output_tokens"] = str(P_OUT)
    if with_cache_prices:
        extra["price_usd_per_million_cache_read_tokens"] = str(P_CACHE_READ)
        extra["price_usd_per_million_cache_write_tokens_5m"] = str(P_CACHE_WRITE_5M)
        extra["price_usd_per_million_cache_write_tokens_1h"] = "10"
    target = ModelTarget("openrouter", MODEL, 10, {"text", "tools"},
                         max_output_tokens=100, **extra)
    cfg = GatewayConfig(
        backends={"openrouter": backend_cfg},
        aliases={"bossman-opus": AliasConfig("bossman-opus", [target])},
        clients={"core": ClientConfig("core", key="gateway-key", allowed_aliases={"*"})},
    )
    backend = OpenAIBackend(backend_cfg, httpx.MockTransport(handler))
    return create_gateway_app(cfg, ModelRouter(cfg, {"openrouter": backend}))


def _headers(**extra):
    return {
        "Authorization": "Bearer gateway-key",
        "X-Bossman-Cloud-Allowed": "1",
        "X-Bossman-Session-Id": stable_session_id("coder", "run-f6"),
        **extra,
    }


def _handler_for(usage: dict | None):
    async def handler(request):  # noqa: ANN001
        json.loads(request.content)  # payload is well formed
        body = {
            "model": MODEL,
            "provider": "Anthropic",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }
        if usage is not None:
            body["usage"] = usage
        return httpx.Response(200, json=body)
    return handler


def _call(app) -> dict:
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", headers=_headers(),
            json={"model": "bossman-opus", "max_tokens": 20,
                  "messages": [{"role": "system", "content": "stable " * 600},
                               {"role": "user", "content": "dynamic"}]})
        assert response.status_code == 200, response.text
        return client.get("/metrics", headers=_headers()).json()


# --------------------------------------------------------------------------- #
# PIN: the parts that are already honest.
# --------------------------------------------------------------------------- #

def test_pin_provider_hit_savings_use_the_configured_cache_read_price():
    """A real provider HIT with fully configured prices produces a defensible
    number: baseline(all fresh) - actual(cache-aware).  PIN it."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 10,
             "prompt_tokens_details": {"cached_tokens": 600}}
    pc = _call(_gateway(_handler_for(usage)))["prompt_cache"]
    baseline = (Decimal(1000) * P_IN + Decimal(10) * P_OUT) / Decimal(1_000_000)
    actual = (Decimal(400) * P_IN + Decimal(600) * P_CACHE_READ
              + Decimal(10) * P_OUT) / Decimal(1_000_000)
    assert pc["state"] == "HOT"
    assert pc["cached_tokens"] == 600
    assert pc["actual_cost_usd"] == pytest.approx(float(actual))
    assert pc["estimated_baseline_cost_usd"] == pytest.approx(float(baseline))
    assert pc["saved_usd"] == pytest.approx(float(baseline - actual))


def test_pin_cache_write_is_not_free_and_shows_negative_savings():
    """A cold WRITE costs MORE than fresh input; the pipeline must not hide it."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 10,
             "prompt_tokens_details": {"cache_write_tokens": 1000}}
    metrics = _call(_gateway(_handler_for(usage)))
    pc = metrics["prompt_cache"]
    assert pc["cache_write_tokens"] == 1000
    assert pc["cached_tokens"] == 0
    assert pc["cache_hit_percent"] == 0.0
    assert pc["saved_usd"] < 0.0, "a cache WRITE must never be reported as a saving"
    assert metrics["cache_observations"]["counts"]["WRITE"] == 1


def test_pin_cache_control_without_provider_usage_claims_nothing():
    """No usage evidence at all -> UNKNOWN observation, zero savings, and the
    ``cache_control_without_usage`` counter that the Advisor keys off."""
    metrics = _call(_gateway(_handler_for(None)))
    obs = metrics["cache_observations"]
    assert obs["counts"]["UNKNOWN"] == 1
    assert obs["cache_control_without_usage"] == 1
    assert metrics["prompt_cache"]["saved_usd"] == 0.0
    advice = ci.cache_advice({"eligible_requests": 50, "cache_control_without_usage": 1})
    assert any("do not report savings" in a.text for a in advice)


def test_pin_unknown_route_price_produces_no_savings():
    """No configured price -> baseline is None -> nothing is ever added to
    ``cache_saved_usd`` (fail closed).  PIN."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 10, "cost": 0.001,
             "prompt_tokens_details": {"cached_tokens": 600}}
    pc = _call(_gateway(_handler_for(usage), with_prices=False))["prompt_cache"]
    assert pc["cached_tokens"] == 600
    assert pc["estimated_baseline_cost_usd"] == 0.0
    assert pc["saved_usd"] == 0.0


def test_pin_shared_cost_pair_refuses_to_invent_a_price():
    """The shared contract that ``_cache_economics`` bypasses."""
    buckets = co.TokenBuckets(fresh_input=400, cache_read=600, cache_write=0, output=10)
    actual, baseline, _ = co.cost_pair(buckets, fresh_per_m=P_IN, read_per_m=None,
                                       write_per_m=None, output_per_m=P_OUT)
    assert actual is None, "unknown read price must yield UNKNOWN, not invented savings"
    assert baseline is not None


# --------------------------------------------------------------------------- #
# RED: savings claimed without provider cache evidence.
# --------------------------------------------------------------------------- #

def test_red_saved_usd_claimed_with_zero_cache_tokens():
    """Provider reports a MISS (no cache_read, no cache_write) but also reports
    its real ``usage.cost``.  ``_cache_economics`` then compares a PROVIDER-BILLED
    actual against a LOCAL-PRICE-TABLE baseline, and ``end_cache`` books the
    difference as cache savings.  The UI row is literally "Saved $"."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 10, "cost": 0.001}
    metrics = _call(_gateway(_handler_for(usage)))
    pc = metrics["prompt_cache"]
    assert pc["cached_tokens"] == 0
    assert pc["cache_write_tokens"] == 0
    assert pc["state"] == "MISS"
    assert metrics["cache_observations"]["counts"]["HIT"] == 0
    assert pc["saved_usd"] == 0.0, (
        "savings reported with zero provider cache tokens: "
        f"saved_usd={pc['saved_usd']} (this is price-table drift, not cache)")


def test_red_same_savings_reported_with_prompt_cache_disabled():
    """The strongest attribution proof: turn prompt caching completely OFF
    (``prompt_cache_enabled=False`` -> no cache_control is sent, the request is
    ineligible/UNSUPPORTED) and replay the SAME provider body.  If the number is
    really 'cache savings' it must be zero here."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 10, "cost": 0.001}
    on = _call(_gateway(_handler_for(usage), cache_enabled=True))["prompt_cache"]
    off = _call(_gateway(_handler_for(usage), cache_enabled=False))["prompt_cache"]
    assert off["eligible_requests"] == 0
    assert off["state"] == "UNSUPPORTED"
    assert off["saved_usd"] == 0.0, (
        "cache savings booked while prompt caching is disabled: "
        f"{off['saved_usd']} (identical to cache-ON value {on['saved_usd']})")


def test_red_savings_invented_from_assumed_cache_prices():
    """Prices for the cache buckets are NOT configured.  ``_cache_prices`` invents
    them (read = 0.1 * input, write = 1.25 * input for anthropic/*), and the
    invented discount is published as ``saved_usd`` / ``actual_cost_usd`` with no
    'estimated' marker -- exactly what ``cache_observation.cost_pair`` refuses to
    do.  The fabricated actual also travels into the observation log, where the
    command-center reads it as ``measured_actual_cost_usd`` with
    ``unknown_cost_requests == 0``, so the dashboard prints a savings number."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 10,
             "prompt_tokens_details": {"cached_tokens": 600}}
    metrics = _call(_gateway(_handler_for(usage), with_cache_prices=False))
    pc, obs = metrics["prompt_cache"], metrics["cache_observations"]
    assert pc["cached_tokens"] == 600
    # dashboard-side reachability (command-center economics(): saved is published
    # whenever measured and baseline exist and unknown_cost_requests == 0)
    assert obs["unknown_cost_requests"] == 0
    assert obs["measured_actual_cost_usd"] is not None
    assert pc["saved_usd"] == 0.0, (
        "savings derived from an assumed (unconfigured) cache read price: "
        f"{pc['saved_usd']}")


def test_red_bypassed_request_still_accrues_savings_in_the_counter():
    """Unit-level proof inside the production accumulator: ``end_cache`` adds
    ``baseline - actual`` unconditionally, ignoring ``eligible`` and ignoring the
    fact that the provider reported no cache tokens whatsoever."""
    m = GatewayMetrics()
    m.end_cache({"provider": "openai", "enabled": False, "state": "UNSUPPORTED"},
                {"prompt_tokens": 1000, "completion_tokens": 10, "cached_tokens": 0,
                 "cache_write_tokens": 0, "fresh_input_tokens": 1000},
                backend="local", model="local/llama",
                actual_cost=Decimal("0.001"), baseline_cost=Decimal("0.005"))
    pc = m.snapshot()["prompt_cache"]
    assert pc["eligible_requests"] == 0
    assert pc["cached_tokens"] == 0
    assert pc["saved_usd"] == 0.0, (
        "savings accrued for a request that never used the cache: "
        f"{pc['saved_usd']}")


# --------------------------------------------------------------------------- #
# RED: savings survive a quality regression.
# --------------------------------------------------------------------------- #

def _obs(state: str, *, read: int, fresh: int, verified: bool, actual: float,
         baseline: float) -> dict:
    return {"event_version": 1, "timestamp": "2026-01-01T00:00:00Z",
            "provider": "openrouter", "model": MODEL, "route": "gateway",
            "state": state, "ttl": "5m", "cache_control_applied": True,
            "prefix_hash": "same-prefix", "prefix_tokens": 900,
            "fresh_input_tokens": fresh, "cache_read_tokens": read,
            "cache_write_tokens": 0, "output_tokens": 10,
            "actual_cost_usd": actual, "baseline_cost_usd": baseline,
            "baseline_is_estimate": True, "verified_success": verified}


CACHE_INTEL_SRC = ROOT / "command-center" / "bcc" / "features" / "cache_intel.py"


def _production_detector_call() -> "ast.Call":
    """The single production call site of ``detect_context_waste``."""
    import ast
    tree = ast.parse(CACHE_INTEL_SRC.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "detect_context_waste"]
    assert len(calls) == 1, f"expected one production call site, found {len(calls)}"
    return calls[0]


def test_red_savings_survive_a_verified_success_regression():
    """Cache reuse cut tokens (and therefore 'savings') while VerifiedSuccess
    collapsed: every cache-read request failed verification, every fresh one
    passed.  ``economics()`` still publishes ``saved_usd``, and nothing flags the
    regression, because the only production caller of the detector
    (``command-center/bcc/features/cache_intel.py::intelligence``) invokes
    ``ci.detect_context_waste(obs)`` with observations ONLY -- no ``episodes``,
    no ``layout`` -- so ``reuse_degrades_quality`` (a RED signal) and 7 of the 9
    signals are unreachable in production.

    Fix-agnostic: this passes as soon as EITHER the call site supplies episode
    evidence OR the detector derives reuse quality from the observations it is
    actually given.
    """
    observations = ([_obs("HIT", read=600, fresh=400, verified=False,
                          actual=0.00255, baseline=0.00525) for _ in range(10)]
                    + [_obs("MISS", read=0, fresh=1000, verified=True,
                            actual=0.00525, baseline=0.00525) for _ in range(10)])

    log = co.ObservationLog(capacity=1000)
    for o in observations:
        log.record(o)
    s = log.summary()
    # command-center economics(): saved is published when nothing is unknown
    saved = (None if s["measured_actual_cost_usd"] is None
             or s["estimated_baseline_cost_usd"] is None
             or s["unknown_cost_requests"] else
             round(s["estimated_baseline_cost_usd"] - s["measured_actual_cost_usd"], 6))
    assert saved is not None and saved > 0, "sanity: the panel is claiming savings"

    call = _production_detector_call()
    wired = len(call.args) > 1 or {kw.arg for kw in call.keywords} & {"episodes", "layout"}
    detected = "reuse_degrades_quality" in {sig.kind for sig in
                                            ci.detect_context_waste(observations)}
    assert wired or detected, (
        f"panel claims saved_usd={saved} while verified success dropped from 1.00 "
        "(fresh) to 0.00 (cache reads); the production call passes only "
        "observations, so reuse_degrades_quality can never fire")
