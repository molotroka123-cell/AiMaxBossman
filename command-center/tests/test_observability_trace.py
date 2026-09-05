"""TRUTH-003 §14: один trace_id на run во всей цепочке событий; ограниченное хранение;
базовые задержки в /api/control-plane; никаких промптов/секретов в событиях."""
from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa

from bcc.db import events as events_t, run_events as run_events_t, utcnow
from bcc.trace import get_trace_id, run_trace_id, trace

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import ToolAdapter


async def test_trace_id_spans_the_action_lifecycle(env):
    adapter = ToolAdapter([("tool", "terminal_run", {"command": "echo hi"}), ("text", "готово")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await make_stack(env.client, max_steps=3)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"tools": ["terminal.run"], "permissions": {"terminal.run": True}})
    # воркер вручную (claim → execute до конца), чтобы не обрывать run между task.completed и task.finalized
    run_id = await env.svc.engine.claim()
    await env.svc.engine.execute(run_id)
    for _ in range(3):
        nxt = await env.svc.engine.claim()
        if nxt is None:
            break
        await env.svc.engine.execute(nxt)
    assert (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]["status"] == "completed"
    chain = await env.svc.bus.by_trace(run_trace_id(int(run_id)))
    kinds = [e["kind"] for e in chain]
    assert {"task.started", "tool.called", "task.finalized", "task.completed"} <= set(kinds), kinds
    assert all(e["data"].get("trace_id") == run_trace_id(int(run_id)) for e in chain)
    # в цепочке нет текста промпта и секретов
    blob = str(chain)
    assert "system_prompt" not in blob and "sk-test" not in blob and "отвечай коротко" not in blob
    r = await env.client.get(f"/api/observability/trace/{run_trace_id(int(run_id))}")
    assert r.status_code == 200 and [e["kind"] for e in r.json()["events"]] == kinds
    assert get_trace_id() is None                                          # контекст сброшен после run'а


def test_trace_context_manager_scopes_id():
    assert get_trace_id() is None
    with trace("t-1"):
        assert get_trace_id() == "t-1"
    assert get_trace_id() is None


async def test_prune_is_bounded_by_age_and_rows(env):
    bus = env.svc.bus
    old = utcnow() - timedelta(days=40)
    async with env.svc.db.session() as s:
        for i in range(30):
            await s.execute(sa.insert(events_t).values(ts=old, kind="old.event", data={"i": i}))
        for i in range(50):
            await s.execute(sa.insert(events_t).values(ts=utcnow(), kind="new.event", data={"i": i}))
        await s.commit()
    removed = await bus.prune(max_age_days=14, max_rows=20)
    assert removed["events"] >= 30 + 30                                  # 30 старых + всё сверх 20 строк
    async with env.svc.db.session() as s:
        left = int((await s.execute(sa.select(sa.func.count()).select_from(events_t))).scalar())
        kinds = {r[0] for r in (await s.execute(sa.select(events_t.c.kind))).fetchall()}
    assert left <= 20 and "old.event" not in kinds


async def test_control_plane_exposes_measured_latency_only(env):
    body = (await env.client.get("/api/control-plane")).json()
    lat = body["latency"]
    assert set(lat) == {"execution_ms", "verification_ms", "task_completion_s"}
    assert lat["execution_ms"]["n"] == 0 and lat["execution_ms"]["p95"] is None      # не измерено → None, не 0
    assert body["retention"]["events_days"] >= 1
