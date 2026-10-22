"""CP-AG-01/02: настоящий STOP, защита от повтора, общий бюджет, независимая проверка и единый receipt.

Негативные случаи написаны раньше реализации. Матрица: A04 A05 A06 A07 A12 A13 A15 A17 (+ A03 связь child↔parent).
Платные запросы НЕ выполняются: tasks только создаются, движок без воркеров.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from bcc import db as dbm
from bcc.features import tools_agents, tools_code
from bcc.features.chat_threads import _append_turn, _new_turn

from .test_agents_orchestration import _agent, _ctx, _provider

pytestmark = pytest.mark.asyncio

KEY = "op-0123456789abcdef-test"


async def _local(env, name="Local Coder", model="coder-local") -> int:
    pid = await _provider(env.svc, f"Local Ollama {name}", "http://127.0.0.1:11434/v1")
    return await _agent(env.svc, name, pid, model, kind="local")


async def _tasks(env) -> list[dict]:
    async with env.svc.db.session() as s:
        return [dbm.row_dict(r) for r in (await s.execute(sa.select(dbm.tasks).order_by(dbm.tasks.c.id))).all()]


async def _parent(env, agent_id: int) -> dict:
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(dbm.tasks).values(
            title="parent", prompt="p", agent_id=agent_id, status="running", meta={},
            created_at=dbm.utcnow(), updated_at=dbm.utcnow()))
        await s.commit()
        tid = int(res.inserted_primary_key[0])
    return {"id": tid, "meta": {}}


def _pctx(env, parent: dict, caller: int, approval_id=None):
    from bcc.tools import ToolContext
    return ToolContext(svc=env.svc, task=parent, run_id=1, agent={"id": caller}, approval_id=approval_id)


async def _finish_child(env, task_id: int, *, status="completed", result="", model="coder-local",
                        tin=100, tout=20, cost=0.0, error=None):
    async with env.svc.db.session() as s:
        await s.execute(sa.update(dbm.tasks).where(dbm.tasks.c.id == task_id).values(status=status))
        await s.execute(sa.insert(dbm.task_runs).values(
            task_id=task_id, attempt=0, status=status if status != "stopped" else "stopped", result=result, error=error,
            model_alias=model, tokens_in=tin, tokens_out=tout, cost_usd=cost,
            started_at=dbm.utcnow(), finished_at=dbm.utcnow()))
        await s.commit()


# ------------------------------------------------------------------ A06: единый STOP (WS = HTTP)

async def test_ws_stop_really_stops_session_tasks_like_http(env):
    aid = await _local(env)
    created = await env.client.post("/api/agent/sessions", json={"title": "ws-stop", "mode": "CHAT"})
    sid = created.json()["id"]
    task = (await env.client.post("/api/tasks", json={"title": "t", "prompt": "x", "agent_id": aid, "run_now": True})).json()["task"]
    await _append_turn(env.svc, sid, _new_turn(task["id"], "x", []))
    with TestClient(env.app) as sync_client:
        with sync_client.websocket_connect(f"/api/agent/sessions/{sid}/stream?after=0&token={env.svc.auth.token}") as ws:
            ws.send_json({"action": "stop"})
            seen = []
            for _ in range(20):
                msg = ws.receive_json()
                seen.append(msg.get("type"))
                if msg.get("type") == "agent.stopped":
                    assert task["id"] in msg.get("stopped_tasks", [])
                    break
            else:
                pytest.fail(f"agent.stopped не пришёл: {seen}")
    status = (await env.client.get(f"/api/tasks/{task['id']}")).json()["task"]["status"]
    assert status == "stopped"          # раньше WS только рисовал событие, задача продолжала жить


# ------------------------------------------------------------------ A07: каскад STOP на детей

async def test_parent_stop_cascades_to_queued_child_and_blocks_new_dispatch(env):
    child_agent = await _local(env)
    parent = await _parent(env, child_agent)
    res = await tools_agents.tool_call_agent(
        {"agent_id": child_agent, "task_description": "прочитай README", "operation_key": KEY,
         "acceptance_criteria": ["not_empty"]}, _pctx(env, parent, caller=999))
    assert not res.error, res.content
    child_id = res.data["task_id"]
    assert (await env.client.get(f"/api/tasks/{child_id}")).json()["task"]["status"] == "queued"

    await env.svc.engine.stop(parent["id"])

    assert (await env.client.get(f"/api/tasks/{child_id}")).json()["task"]["status"] == "stopped"
    again = await tools_agents.tool_call_agent(
        {"agent_id": child_agent, "task_description": "ещё одно", "operation_key": KEY + "-2",
         "acceptance_criteria": ["not_empty"]}, _pctx(env, parent, caller=999))
    assert again.error is True and "остановлен" in again.content
    assert len(await _tasks(env)) == 2                      # parent + один child, новых стартов после STOP нет


async def test_global_owner_stop_blocks_delegation(env, monkeypatch):
    from bcc.features import tools_cloud
    child_agent = await _local(env)
    monkeypatch.setattr(tools_cloud, "stop_reason", lambda svc: "owner STOP: тест")
    res = await tools_agents.tool_call_agent({"agent_id": child_agent, "task_description": "x", "acceptance_criteria": ["not_empty"]},
                                             _ctx(env.svc, agent_id=999))
    assert res.error is True and "STOP" in res.content
    assert await _tasks(env) == []


# ------------------------------------------------------------------ A04/A05: идемпотентность intent

async def test_same_operation_key_same_payload_returns_same_child_once(env):
    aid = await _local(env)
    parent = await _parent(env, aid)
    args = {"agent_id": aid, "task_description": "прочитай README", "operation_key": KEY, "acceptance_criteria": ["not_empty"]}
    r1 = await tools_agents.tool_call_agent(dict(args), _pctx(env, parent, 999))
    r2 = await tools_agents.tool_call_agent(dict(args), _pctx(env, parent, 999))
    assert not r1.error and not r2.error
    assert r1.data["task_id"] == r2.data["task_id"]
    assert r1.data["replayed"] is False and r2.data["replayed"] is True
    assert len(await _tasks(env)) == 2                      # parent + ровно один child


async def test_concurrent_taps_with_same_key_create_one_child(env):
    aid = await _local(env)
    parent = await _parent(env, aid)
    args = {"agent_id": aid, "task_description": "параллельный тап", "operation_key": KEY + "-par", "acceptance_criteria": ["not_empty"]}
    rs = await asyncio.gather(*[tools_agents.tool_call_agent(dict(args), _pctx(env, parent, 999)) for _ in range(5)])
    assert all(not r.error for r in rs), [r.content for r in rs]
    assert len({r.data["task_id"] for r in rs}) == 1
    assert len(await _tasks(env)) == 2


async def test_same_key_other_payload_is_conflict_before_dispatch(env):
    aid = await _local(env)
    parent = await _parent(env, aid)
    base = {"agent_id": aid, "operation_key": KEY + "-c", "acceptance_criteria": ["not_empty"]}
    ok = await tools_agents.tool_call_agent({**base, "task_description": "первое"}, _pctx(env, parent, 999))
    bad = await tools_agents.tool_call_agent({**base, "task_description": "ДРУГОЕ"}, _pctx(env, parent, 999))
    assert not ok.error and bad.error is True and "operation_key" in bad.content
    assert len(await _tasks(env)) == 2


async def test_weak_operation_key_is_rejected(env):
    aid = await _local(env)
    res = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": "x", "operation_key": "short",
                                              "acceptance_criteria": ["not_empty"]}, _ctx(env.svc, agent_id=999))
    assert res.error is True


# ------------------------------------------------------------------ лимиты роя и scoped child

async def test_depth_is_one_and_at_most_two_active_children(env):
    assert tools_agents.MAX_DELEGATION_DEPTH == 1 and tools_agents.MAX_ACTIVE_CHILDREN == 2
    aid = await _local(env)
    parent = await _parent(env, aid)
    for i in range(2):
        r = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": f"дело {i}", "operation_key": f"{KEY}-lim{i}",
                                                "acceptance_criteria": ["not_empty"]}, _pctx(env, parent, 999))
        assert not r.error, r.content
    third = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": "дело 3", "operation_key": f"{KEY}-lim3",
                                                "acceptance_criteria": ["not_empty"]}, _pctx(env, parent, 999))
    assert third.error is True and "одновременно" in third.content
    child = (await _tasks(env))[-1]
    grand = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": "внук", "operation_key": f"{KEY}-deep",
                                                "acceptance_criteria": ["not_empty"]},
                                               _pctx(env, {"id": child["id"], "meta": child["meta"]}, 999))
    assert grand.error is True and "глубин" in grand.content


async def test_child_envelope_is_read_only_and_cannot_delegate(env):
    pid = await _provider(env.svc, "Local Ollama env", "http://127.0.0.1:11434/v1")
    async with env.svc.db.session() as s:
        m = await s.execute(sa.insert(dbm.models).values(provider_id=pid, name="m-env", alias="m-env", kind="local",
                                                         context_window=32768, caps={}))
        a = await s.execute(sa.insert(dbm.agents).values(
            name="Envelope", role="t", model_id=int(m.inserted_primary_key[0]), enabled=True,
            tools=["terminal.run", "code.search", "call_agent", "agents.*"], created_at=dbm.utcnow()))
        await s.commit()
        aid = int(a.inserted_primary_key[0])
    parent = await _parent(env, aid)
    r = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": "обзор", "operation_key": KEY + "-env",
                                            "acceptance_criteria": ["not_empty"]}, _pctx(env, parent, 999))
    assert not r.error, r.content
    allowed = [t for t in (await _tasks(env))[-1]["meta"]["allowed_tools"]]
    assert "code.search" in allowed
    assert "terminal.run" not in allowed and "call_agent" not in allowed and not any(t.startswith("agents.") for t in allowed)


# ------------------------------------------------------------------ A17 + receipt (A03, A15)

async def _delegate(env, criteria, *, key=KEY + "-v", description="сделай отчёт"):
    aid = await _local(env, name=f"V{key[-3:]}", model=f"m-{key[-6:]}")
    parent = await _parent(env, aid)
    r = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": description, "operation_key": key,
                                            "acceptance_criteria": criteria}, _pctx(env, parent, 999))
    assert not r.error, r.content
    return parent, r.data["task_id"], aid


async def test_running_child_has_no_receipt_and_is_not_success(env):
    parent, cid, _ = await _delegate(env, ["not_empty"], key=KEY + "-run")
    out = await tools_agents.finalize_delegation(env.svc, cid)
    assert out["state"] in {"QUEUED", "RUNNING"} and out["receipt"] is None


async def test_author_says_done_but_artifact_is_wrong_is_not_succeeded(env):
    parent, cid, _ = await _delegate(env, ["contains:ИТОГ: ПРИНЯТО", "max_chars:200"], key=KEY + "-bad")
    await _finish_child(env, cid, result="Готово! Всё сделано отлично.")      # автор уверен, артефакт неверен
    out = await tools_agents.finalize_delegation(env.svc, cid)
    assert out["state"] == "FAILED_VERIFICATION" and out["receipt"]["postcondition"]["verdict"] == "FAIL"
    assert [c["ok"] for c in out["receipt"]["postcondition"]["criteria"]] == [False, True]


async def test_unknown_criterion_is_unverified_never_success(env):
    parent, cid, _ = await _delegate(env, ["всё должно быть красиво"], key=KEY + "-unk")
    await _finish_child(env, cid, result="что угодно")
    out = await tools_agents.finalize_delegation(env.svc, cid)
    assert out["state"] == "UNVERIFIED" and out["receipt"]["postcondition"]["verdict"] == "UNVERIFIED"


async def test_failed_and_stopped_children_are_not_green(env):
    _, cid1, _ = await _delegate(env, ["not_empty"], key=KEY + "-f1")
    await _finish_child(env, cid1, status="failed", result="", error="boom")
    assert (await tools_agents.finalize_delegation(env.svc, cid1))["state"] == "FAILED"
    _, cid2, _ = await _delegate(env, ["not_empty"], key=KEY + "-f2")
    await _finish_child(env, cid2, status="stopped", result="")
    assert (await tools_agents.finalize_delegation(env.svc, cid2))["state"] == "CANCELLED"


async def test_verified_child_gets_single_stable_receipt_without_private_text(env):
    secret = "SYNTHETIC_PRIVATE_BRIEF_DO_NOT_LEAK"
    parent, cid, aid = await _delegate(env, ["contains:ПРИНЯТО", "not_empty"], key=KEY + "-ok",
                                       description=f"отчёт {secret}")
    await _finish_child(env, cid, result="ИТОГ: ПРИНЯТО", model="m-actual-model", tin=111, tout=22)
    first = await tools_agents.finalize_delegation(env.svc, cid)
    second = await tools_agents.finalize_delegation(env.svc, cid)
    assert first["state"] == "SUCCEEDED"
    rc = first["receipt"]
    assert rc["receipt_id"] == second["receipt"]["receipt_id"]                    # идемпотентно: один receipt
    assert rc["child_task_id"] == cid and rc["parent_task_id"] == parent["id"] and rc["root_task_id"] == parent["id"]
    assert rc["agent_id"] == aid and rc["model_actual"] == "m-actual-model"
    assert rc["tokens"] == {"in": 111, "out": 22, "source": "provider_usage"}
    assert rc["artifact_hash"] == hashlib.sha256("ИТОГ: ПРИНЯТО".encode()).hexdigest()
    assert rc["cost"]["reserved_usd"] == "0" and rc["cost"]["actual_usd"] == "0.0"
    assert KEY not in json.dumps(rc) and secret not in json.dumps(rc, ensure_ascii=False)   # ключ только в виде ссылки
    assert len(rc["operation_key_ref"]) == 16
    stored = (await _tasks(env))[cid - 1 if False else -1]
    row = next(t for t in await _tasks(env) if t["id"] == cid)
    assert row["meta"]["receipt"]["receipt_id"] == rc["receipt_id"]                    # хранится вместе с задачей
    assert stored is not None


async def test_receipt_endpoint_same_data_for_desktop_and_mobile(env):
    parent, cid, _ = await _delegate(env, ["not_empty"], key=KEY + "-http")
    await _finish_child(env, cid, result="есть вывод")
    a = await env.client.get(f"/api/agents/delegations/{cid}/receipt")
    assert a.status_code == 200, a.text
    assert a.json()["state"] == "SUCCEEDED"
    lst = await env.client.get("/api/agents/delegations", params={"parent": parent["id"]})
    assert lst.status_code == 200 and [x["child_task_id"] for x in lst.json()["items"]] == [cid]
    assert (await env.client.get("/api/agents/delegations/987654/receipt")).status_code == 404


# ------------------------------------------------------------------ A12/A13: общий бюджет, paid

async def _paid(env, monkeypatch, *, price=(3.0, 15.0), name="Paid"):
    monkeypatch.setenv("BOSSMAN_ALLOW_PAID_CLOUD", "1")
    pid = await _provider(env.svc, f"Cloud {name}", "https://api.example.com/v1")
    return await _agent(env.svc, name, pid, f"paid-{name}", kind="cloud", price_in=price[0], price_out=price[1])


async def test_paid_blocked_without_owner_cap_and_for_zero_or_bad_cap(env, monkeypatch):
    aid = await _paid(env, monkeypatch)
    parent = await _parent(env, aid)
    for cap in (None, "0", "-1", "abc", "1e3"):
        args = {"agent_id": aid, "task_description": "платное", "operation_key": f"{KEY}-p{cap}", "acceptance_criteria": ["not_empty"]}
        if cap is not None:
            args["requested_cost_cap_usd"] = cap
        res = await tools_agents.tool_call_agent(args, _pctx(env, parent, 999, approval_id=5))
        assert res.error is True, cap
    # cap владельца не задан (0) → даже корректный запрос не проходит
    res = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": "платное", "operation_key": KEY + "-pok",
                                              "acceptance_criteria": ["not_empty"], "requested_cost_cap_usd": "0.50"},
                                             _pctx(env, parent, 999, approval_id=5))
    assert res.error is True and "лимит" in res.content
    assert len(await _tasks(env)) == 1


async def test_two_paid_children_cannot_exceed_shared_root_cap(env, monkeypatch):
    aid = await _paid(env, monkeypatch)
    parent = await _parent(env, aid)
    await tools_code._write_setting_json(env.svc, tools_agents.CAP_KEY, "1.00")
    def call(i):
        return tools_agents.tool_call_agent({"agent_id": aid, "task_description": f"платное {i}", "operation_key": f"{KEY}-cap{i}",
                                             "acceptance_criteria": ["not_empty"], "requested_cost_cap_usd": "0.60"},
                                            _pctx(env, parent, 999, approval_id=5))
    rs = await asyncio.gather(call(1), call(2))                   # конкурентно: spent+reserved ≤ cap
    assert sorted(bool(r.error) for r in rs) == [False, True], [r.content for r in rs]
    assert len(await _tasks(env)) == 2
    ok = next(r for r in rs if not r.error)
    assert ok.data["reserved_usd"] == "0.60"


async def test_reservation_settles_to_actual_on_receipt_and_frees_remainder(env, monkeypatch):
    aid = await _paid(env, monkeypatch, name="Settle")
    parent = await _parent(env, aid)
    await tools_code._write_setting_json(env.svc, tools_agents.CAP_KEY, "1.00")
    r1 = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": "a", "operation_key": KEY + "-s1",
                                             "acceptance_criteria": ["not_empty"], "requested_cost_cap_usd": "0.60"},
                                            _pctx(env, parent, 999, approval_id=5))
    assert not r1.error, r1.content
    await _finish_child(env, r1.data["task_id"], result="ok", model="paid-Settle", cost=0.10)
    rc = (await tools_agents.finalize_delegation(env.svc, r1.data["task_id"]))["receipt"]
    assert rc["cost"]["actual_usd"] == "0.1" and rc["cost"]["reserved_usd"] == "0.60"
    r2 = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": "b", "operation_key": KEY + "-s2",
                                             "acceptance_criteria": ["not_empty"], "requested_cost_cap_usd": "0.85"},
                                            _pctx(env, parent, 999, approval_id=5))
    assert not r2.error, r2.content          # 0.10 списано + 0.85 резерв = 0.95 ≤ 1.00 (остаток 0.50 освобождён)


async def test_unknown_price_model_is_unavailable_not_hidden_paid_fallback(env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_ALLOW_PAID_CLOUD", "1")
    pid = await _provider(env.svc, "Cloud Unknown", "https://api.example.com/v1")
    aid = await _agent(env.svc, "Unknown", pid, "unknown-price", kind="cloud")          # цена не задана
    parent = await _parent(env, aid)
    res = await tools_agents.tool_call_agent({"agent_id": aid, "task_description": "x", "operation_key": KEY + "-unk",
                                              "acceptance_criteria": ["not_empty"], "requested_cost_cap_usd": "0.10"},
                                             _pctx(env, parent, 999, approval_id=5))
    assert res.error is True
    assert len(await _tasks(env)) == 1
