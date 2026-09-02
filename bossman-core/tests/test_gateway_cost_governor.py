"""Интеграция Cost Governor c реальным Stage 3 Gateway.

Проверяет integration/GATEWAY_COST_HOOK.md пакета cost-governor+notifications:
проверка ИМЕННО перед реальным облачным upstream-вызовом (не только в
llm.py — capability-алиас может сперва попробовать local и лишь потом
fallback в облако), с учётом того, что бюджет не изобретается там, где не
настроен, и что cloud_policy=never остаётся сильнее любого бюджетного approve.
"""
from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from bossman.cost_control.governor import CostGovernor
from bossman.cost_control.models import BudgetPolicy, BudgetScope, HardLimitAction
from bossman.cost_control.store import SQLiteBudgetStore
from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.config import (
    AliasConfig, BackendConfig, ClientConfig, GatewayConfig, ModelTarget,
)
from bossman.gateway.router import ModelRouter


def _priced_target(backend="openrouter", model="gpt-4o", priority=100, **extra):
    return ModelTarget(
        backend, model, priority, {"text"},
        price_usd_per_million_input_tokens="1", price_usd_per_million_output_tokens="2",
        **extra,
    )


def _client(cfg: GatewayConfig, transport: httpx.MockTransport) -> httpx.AsyncClient:
    backends = {n: OpenAIBackend(c, transport) for n, c in cfg.backends.items()}
    app = create_gateway_app(cfg, router=ModelRouter(cfg, backends))
    # F-008: облако fail-closed — тесты бюджета явно разрешают облако заголовком,
    # чтобы проверять именно бюджетную политику, а не политику облака.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw",
                             headers={"x-bossman-cloud-allowed": "1"})


def _cloud_hits(counter):
    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
    return handler


@pytest.fixture
def budget_store(tmp_path, monkeypatch):
    """Изолированная БД бюджета на тест; _cost_reserve/_cost_settle читают
    GOVERNOR/STORE лениво из cost_control.runtime на каждый вызов, поэтому
    подмена атрибутов модуля подхватывается сразу."""
    import bossman.cost_control.runtime as rt
    store = SQLiteBudgetStore(tmp_path / "budget.db")
    events = []
    governor = CostGovernor(store, lambda kind, **data: events.append({"kind": kind, **data}))
    monkeypatch.setattr(rt, "STORE", store, raising=False)
    monkeypatch.setattr(rt, "GOVERNOR", governor, raising=False)
    return store, events


@pytest.mark.asyncio
async def test_no_budget_configured_does_not_touch_local_inference(budget_store):
    """Ни одна политика бюджета не настроена: local-цель проходит мимо
    Cost Governor вовсе (bug class: 'connecting cost governor breaks local')."""
    hits = {"n": 0}
    cfg = GatewayConfig(
        backends={"ollama": BackendConfig("ollama", "http://local", cloud=False)},
        aliases={"bossman-fast": AliasConfig("bossman-fast",
                                             [ModelTarget("ollama", "qwen", 10, {"text"})])},
        clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True,
    )
    async with _client(cfg, httpx.MockTransport(_cloud_hits(hits))) as client:
        r = await client.post("/v1/chat/completions",
                              json={"model": "bossman-fast", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert hits["n"] == 1


@pytest.mark.asyncio
async def test_no_budget_configured_lets_unpriced_cloud_through(budget_store):
    """Ни одна политика не включена: неизвестная цена облачной цели НЕ
    придумывает лимит и НЕ блокирует запрос — см. integration/CONFIG.md
    'Empty budget env vars do not invent limits'."""
    hits = {"n": 0}
    cfg = GatewayConfig(
        backends={"openrouter": BackendConfig("openrouter", "http://cloud", cloud=True)},
        aliases={"cloud-only": AliasConfig("cloud-only",
                                           [ModelTarget("openrouter", "gpt-4o", 100, {"text"})])},
        clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True,
    )
    async with _client(cfg, httpx.MockTransport(_cloud_hits(hits))) as client:
        r = await client.post("/v1/chat/completions",
                              json={"model": "cloud-only", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert hits["n"] == 1


@pytest.mark.asyncio
async def test_unknown_pricing_fails_closed_when_budget_enabled(budget_store):
    """Бюджет включён, у цели НЕТ цены: cloud upstream не трогается вовсе —
    BudgetPricingUnknown, а не 'наверное дёшево'."""
    store, _ = budget_store
    store.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL, Decimal("10")))
    hits = {"n": 0}
    cfg = GatewayConfig(
        backends={"openrouter": BackendConfig("openrouter", "http://cloud", cloud=True)},
        aliases={"cloud-only": AliasConfig("cloud-only",
                                           [ModelTarget("openrouter", "gpt-4o-unpriced", 100, {"text"})])},
        clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True,
    )
    async with _client(cfg, httpx.MockTransport(_cloud_hits(hits))) as client:
        r = await client.post("/v1/chat/completions",
                              json={"model": "cloud-only", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502
    assert hits["n"] == 0
    assert "BudgetPricingUnknown" in r.text


@pytest.mark.asyncio
async def test_hard_stop_denies_before_network_and_reserves_nothing_permanent(budget_store):
    """STOP: бюджет исчерпан заранее → к сети не подступались, спорной брони
    не остаётся (reserved возвращается к 0)."""
    store, _ = budget_store
    store.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL, Decimal("0.0001"),
                                  hard_action=HardLimitAction.STOP))
    hits = {"n": 0}
    cfg = GatewayConfig(
        backends={"openrouter": BackendConfig("openrouter", "http://cloud", cloud=True)},
        aliases={"cloud-only": AliasConfig("cloud-only", [_priced_target()])},
        clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True,
    )
    async with _client(cfg, httpx.MockTransport(_cloud_hits(hits))) as client:
        r = await client.post("/v1/chat/completions",
                              json={"model": "cloud-only", "max_tokens": 100,
                                    "messages": [{"role": "user", "content": "hello there, spend money please"}]})
    assert r.status_code == 502
    assert hits["n"] == 0
    snap = store.snapshots()[0]
    assert Decimal(snap["reserved_usd"]) == 0


@pytest.mark.asyncio
async def test_allowed_reserve_commits_actual_after_real_call(budget_store):
    """ALLOW: бронь создаётся, вызов идёт, после ответа reserved→0 и spent растёт
    ровно на посчитанную по РЕАЛЬНОМУ usage сумму (не на верхнюю границу)."""
    store, _ = budget_store
    store.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL, Decimal("10")))
    hits = {"n": 0}
    cfg = GatewayConfig(
        backends={"openrouter": BackendConfig("openrouter", "http://cloud", cloud=True)},
        aliases={"cloud-only": AliasConfig("cloud-only", [_priced_target()])},
        clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True,
    )
    async with _client(cfg, httpx.MockTransport(_cloud_hits(hits))) as client:
        r = await client.post("/v1/chat/completions",
                              json={"model": "cloud-only", "max_tokens": 200,
                                    "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert hits["n"] == 1
    snap = store.snapshots()[0]
    assert Decimal(snap["reserved_usd"]) == 0
    # usage из мока: 100 prompt * $1/1e6 + 50 completion * $2/1e6 = 0.0002
    assert Decimal(snap["spent_usd"]) == Decimal("0.000200")


@pytest.mark.asyncio
async def test_cloud_policy_never_beats_budget_even_with_headroom(budget_store):
    """cloud_policy=never (X-Bossman-Cloud-Allowed: 0): облако не трогается,
    даже если бюджета в избытке — governor.reserve_cloud_call сам режет по
    cloud_allowed=False (defense-in-depth поверх resolve()'а роутера)."""
    store, _ = budget_store
    store.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL, Decimal("1000"),
                                  hard_action=HardLimitAction.ASK))
    hits = {"n": 0}
    cfg = GatewayConfig(
        backends={"ollama": BackendConfig("ollama", "http://local", cloud=False),
                  "openrouter": BackendConfig("openrouter", "http://cloud", cloud=True)},
        aliases={"bossman-smart": AliasConfig("bossman-smart", [
            ModelTarget("ollama", "qwen", 10, {"text"}),
            _priced_target(priority=100)])},
        clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "cloud" in str(request.url):
            hits["n"] += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "из облака"}}],
                                             "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        return httpx.Response(503, json={"error": "local down"})

    async with _client(cfg, httpx.MockTransport(handler)) as client:
        r = await client.post("/v1/chat/completions",
                              headers={"X-Bossman-Cloud-Allowed": "0"},
                              json={"model": "bossman-smart", "max_tokens": 10,
                                    "messages": [{"role": "user", "content": "секрет"}]})
    assert r.status_code in (502, 503)
    assert hits["n"] == 0, "cloud_policy=never пробит бюджетным ALLOW"
