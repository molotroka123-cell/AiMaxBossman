"""ASTRA product/security regressions through actual API and provider seams."""
import io
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa

from bcc import metrics
from bcc.auth import TokenAuth
from bcc.provider_governance import GovernedAdapter
from bcc.providers import ProviderError
from bcc.v2.openrouter_ext import parse_model_card, OpenRouterClient
from bcc.v2.memory.context_pack import build_context_pack
from bcc.v2.memory.memsearch_bridge import MemoryHit
from bossman_shared.privacy import execution_privacy


@pytest.mark.parametrize("budget", [0, 1, 5, 20, 100])
def test_prod001_budget_includes_source_labels_and_separators(budget):
    hits=[MemoryHit("content"*80, "long/source/"*20, "heading"*20), MemoryHit("short", "a")]
    pack=build_context_pack("query", hits, max_tokens=budget)
    assert len(pack.text) <= budget * 3.5
    assert pack.estimated_tokens <= budget


@pytest.mark.parametrize("price", [None, "", "NaN", "-1", "Infinity", "garbage", True])
def test_prod002_unavailable_prices_remain_unknown(price):
    card=parse_model_card({"id":"synthetic", "pricing":{"prompt":price, "completion":price}})
    assert card.price_in is None and card.price_out is None


def test_prod002_explicit_free_price_still_works():
    card=parse_model_card({"id":"free", "pricing":{"prompt":"0", "completion":"0"}})
    assert card.price_in == 0 and card.price_out == 0


@pytest.mark.asyncio
async def test_o001_prod002_every_actual_provider_call_checks_privacy_and_price():
    calls=[]
    async def chat(*a, **k): calls.append(1); return "okay"
    raw=SimpleNamespace(chat=chat)
    provider={"kind":"openrouter", "base_url":"https://openrouter.ai/api/v1"}
    unknown=GovernedAdapter(raw, provider, {})
    with pytest.raises(ProviderError): await unknown.chat("m", [])
    priced=GovernedAdapter(raw, provider, {"price_in":1, "price_out":2, "pricing_known":True})
    with execution_privacy("private"):
        with execution_privacy("public"):
            with pytest.raises(PermissionError): await priced.chat("m", [])
    assert calls == []
    assert await priced.chat("m", []) == "okay" and calls == [1]


@pytest.mark.asyncio
async def test_o001_direct_openrouter_probe_refuses_private_context():
    calls=[]
    client=OpenRouterClient("synthetic", transport=httpx.MockTransport(lambda req:calls.append(req)))
    with execution_privacy("local_only"):
        with pytest.raises(PermissionError): await client.chat_raw("m", [])
        with pytest.raises(PermissionError): await client.stream_raw("m", [])
    assert calls == []


@pytest.mark.parametrize("rows", [None, [["bad"]], [["GPU", "123", "ollama", "[N/A]"]]])
def test_prod003_failed_process_query_is_unknown(monkeypatch, rows):
    monkeypatch.setattr(metrics.shutil, "which", lambda _:"nvidia-smi")
    monkeypatch.setattr(metrics, "_smi", lambda exe, fields, kind:
        [["GPU", "card", "1", "100", "1000", "20"]] if kind == "gpu" else rows)
    gpu=metrics._nvidia()[0]
    assert gpu["vram_procs_mb"] is None and not gpu["process_query_complete"]


def test_prod003_successful_empty_process_list_really_is_zero(monkeypatch):
    monkeypatch.setattr(metrics.shutil, "which", lambda _:"nvidia-smi")
    monkeypatch.setattr(metrics, "_smi", lambda exe, fields, kind:
        [["GPU", "card", "1", "100", "1000", "20"]] if kind == "gpu" else [])
    gpu=metrics._nvidia()[0]
    assert gpu["vram_procs_mb"] == 0 and gpu["process_query_complete"]


def test_sec104_token_never_prints_to_redirected_output(tmp_path, monkeypatch):
    import sys
    stream=io.StringIO(); monkeypatch.setattr(sys, "stdout", stream)
    for flag in (None, "1", "0"):
        if flag is None: monkeypatch.delenv("BCC_TOKEN_STDOUT", raising=False)
        else: monkeypatch.setenv("BCC_TOKEN_STDOUT", flag)
        auth=TokenAuth(tmp_path / str(flag))
        auth.announce(created=False)
        assert auth.token not in stream.getvalue()


@pytest.mark.asyncio
async def test_prod004_mission_tasks_are_filtered_before_limit_and_paginated(env):
    from bcc.db import tasks, utcnow
    async with env.svc.db.session() as session:
        for i in range(605):
            await session.execute(sa.insert(tasks).values(title=f"task-{i}", prompt="probe", status="draft",
                                                          created_at=utcnow(), updated_at=utcnow()))
        await session.execute(sa.text("UPDATE tasks SET mission_id=42 WHERE id<=505"))
        await session.commit()
    response=await env.client.get("/api/tasks", params={"mission_id":42, "limit":500})
    assert response.status_code == 200
    first=response.json(); assert len(first) == 500 and all(t["mission_id"] == 42 for t in first)
    second=(await env.client.get("/api/tasks", params={"mission_id":42, "limit":500,
                                                     "before_id":min(t["id"] for t in first)})).json()
    assert len(second) == 5 and len({t["id"] for t in first+second}) == 505


@pytest.mark.asyncio
async def test_prod002_registry_persists_unknown_and_explicit_free_prices(env):
    provider=await env.svc.registry.create_provider(name="catalog", kind="openai_compat", base_url="https://openrouter.ai/api/v1")
    unknown=await env.svc.registry.create_model(provider_id=provider["id"],name="unknown",kind="cloud")
    assert unknown["price_in"] is None and unknown["price_out"] is None and not unknown["pricing_known"]
    free=await env.svc.registry.create_model(provider_id=provider["id"],name="free",kind="cloud",price_in=0,price_out=0)
    assert free["pricing_known"]
    changed=await env.svc.registry.update_model(free["id"],price_in=None)
    assert not changed["pricing_known"] and changed["price_in"] is None


@pytest.mark.asyncio
async def test_prod002_legacy_cloud_price_requires_revalidation(env):
    from bcc.db import models
    p=await env.svc.registry.create_provider(name="legacy",kind="openai_compat",base_url="https://example.org/v1")
    m=await env.svc.registry.create_model(provider_id=p["id"],name="old",kind="cloud",price_in=0,price_out=0)
    async with env.svc.db.session() as session:
        await session.execute(sa.text("ALTER TABLE models DROP COLUMN pricing_known"))
        await session.commit()
    await env.svc.db._migrate()
    restored=await env.svc.registry.get_model(m["id"])
    assert not restored["pricing_known"]
    adapter,_=await env.svc.registry.adapter_for(m["id"])
    with pytest.raises(ProviderError): await adapter.chat("old", [])


def test_prod002_old_catalog_zero_without_provider_pricing_is_not_free():
    from bcc.v2.openrouter_ext import catalog_price_values
    assert catalog_price_values({"price_in":0,"price_out":0,"raw_metadata":{"id":"old"}}) == {
        "price_in":None,"price_out":None}
    assert catalog_price_values({"raw_metadata":{"pricing":{"prompt":"0","completion":"0"}}}) == {
        "price_in":0.0,"price_out":0.0}
