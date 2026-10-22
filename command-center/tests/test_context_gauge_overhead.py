"""Датчик контекста должен считать то, что реально уходит модели: схемы выданных инструментов и системный промпт.

Без этого «оценка» (чат ещё без завершённого прогона) показывала «зелёные» 20%, хотя схемы инструментов
Gemma-кита и промпт агента уже съели заметную часть 32k-окна.
"""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from bcc import db as dbm
from bcc.features import agent_extras as ax
from bcc.features.chat_threads import _append_turn, _new_turn

pytestmark = pytest.mark.asyncio


async def _agent(svc, *, tools, system_prompt="") -> int:
    async with svc.db.session() as s:
        p = await s.execute(sa.insert(dbm.providers).values(
            name="Local Ollama", kind="openai_compat", base_url="http://127.0.0.1:11434/v1", created_at=dbm.utcnow()))
        m = await s.execute(sa.insert(dbm.models).values(
            provider_id=int(p.inserted_primary_key[0]), name="gemma-ctx", alias="gemma-ctx", kind="local",
            context_window=32768, caps={"tools": True}))
        a = await s.execute(sa.insert(dbm.agents).values(
            name="ctx-agent", role="t", system_prompt=system_prompt, model_id=int(m.inserted_primary_key[0]),
            tools=tools, enabled=True, created_at=dbm.utcnow()))
        await s.commit()
        return int(a.inserted_primary_key[0])


async def test_unknown_agent_has_zero_overhead(env):
    assert await ax._fixed_overhead(env.svc, 987654) == (0, 0)
    assert await ax._fixed_overhead(env.svc, None) == (0, 0)


async def test_agent_without_tools_counts_only_system_prompt(env):
    aid = await _agent(env.svc, tools=[], system_prompt="я" * 220)
    tokens, count = await ax._fixed_overhead(env.svc, aid)
    assert count == 0
    assert tokens == ax.estimate_tokens("я" * 220) == 100


async def test_granted_tools_schemas_are_counted(env):
    from bcc.tools import REGISTRY
    aid = await _agent(env.svc, tools=["code.search", "call_agent"])
    tokens, count = await ax._fixed_overhead(env.svc, aid)
    expected = sum(ax.estimate_tokens(json.dumps(t.schema(), ensure_ascii=False))
                   for t in REGISTRY.resolve(["code.search", "call_agent"]))
    assert count == 2
    assert tokens > 100 and abs(tokens - expected) <= 2 * count      # допуск на округление по каждому инструменту


async def test_context_view_includes_overhead_on_estimate_path(env):
    aid = await _agent(env.svc, tools=["code.search", "call_agent", "agents.list"], system_prompt="x" * 400)
    created = await env.client.post("/api/agent/sessions", json={"title": "ctx", "mode": "CHAT", "agent_id": aid})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    await _append_turn(env.svc, sid, _new_turn(-301, "привет", []))
    ctx = (await env.client.get(f"/api/agent/sessions/{sid}/context")).json()
    assert ctx["source"] == "estimate"
    assert ctx["fixed_overhead_tokens"] > 100 and ctx["tools_count"] == 3
    assert ctx["used_tokens"] >= ctx["fixed_overhead_tokens"]
    # проценты согласованы с токенами (не «20%» от пустоты)
    if ctx["context_window"]:
        assert ctx["used_pct"] == round(100.0 * ctx["used_tokens"] / ctx["context_window"], 1)
        assert ctx["left_tokens"] == max(0, ctx["context_window"] - ctx["used_tokens"])
