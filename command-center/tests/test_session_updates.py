"""`/updates`: Bossman от первого лица перечисляет, чему научился за сессию — только по журналу, без выдумок.

Каждое утверждение обязано опираться на запись: вызов инструмента, проверку делегирования, отказ или сжатие контекста.
«Усвоено» здесь — применённое и проверенное в этой сессии, а не изменение весов; ответ так и говорит.
Приватный текст (сообщения, аргументы и вывод инструментов) в ответ не попадает.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc import db as dbm
from bcc.features import agent_extras as ax
from bcc.features.chat_threads import _append_turn, _new_turn

pytestmark = pytest.mark.asyncio

CANARY = "KANAREYKA-updates-private-7731"


async def _session(env) -> str:
    created = await env.client.post("/api/agent/sessions", json={"title": "upd", "mode": "CHAT"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _task(env, *, agent_id=None, meta=None, status="completed") -> int:
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(dbm.tasks).values(
            title="t", prompt=f"prompt {CANARY}", agent_id=agent_id, status=status, meta=meta or {},
            created_at=dbm.utcnow(), updated_at=dbm.utcnow()))
        await s.commit()
        return int(res.inserted_primary_key[0])


async def _call(env, task_id: int, tool: str, status: str = "executed", n: int = 1):
    async with env.svc.db.session() as s:
        run = await s.execute(sa.insert(dbm.task_runs).values(task_id=task_id, attempt=0, status="completed"))
        run_id = int(run.inserted_primary_key[0])
        for i in range(n):
            await s.execute(sa.insert(dbm.tool_calls).values(
                run_id=run_id, task_id=task_id, step=i, call_id=f"c{task_id}-{tool}-{status}-{i}", tool=tool,
                args={"secret": CANARY}, status=status, result_preview=f"вывод {CANARY}", created_at=dbm.utcnow()))
        await s.commit()


async def _updates(env, sid: str) -> dict:
    r = await env.client.get(f"/api/agent/sessions/{sid}/updates")
    assert r.status_code == 200, r.text
    assert CANARY not in r.text                                   # приватное не утекает никогда
    return r.json()


async def test_empty_session_claims_nothing_new(env):
    sid = await _session(env)
    body = await _updates(env, sid)
    assert body["learned"] == [] and body["counts"]["tools_used"] == 0
    assert any("не применял" in s for s in body["statements"])
    assert "веса" in body["disclaimer"].lower()


async def test_unknown_session_is_404(env):
    assert (await env.client.get("/api/agent/sessions/20261022-000000-aaaaaa/updates")).status_code == 404


async def test_first_use_is_new_but_repeat_use_is_practice_not_new(env):
    sid = await _session(env)
    old = await _task(env)
    await _call(env, old, "code.search")                         # раньше, вне этой сессии
    t = await _task(env)
    await _append_turn(env.svc, sid, _new_turn(t, f"привет {CANARY}", []))
    await _call(env, t, "code.search", n=2)
    await _call(env, t, "code.workspace_map", n=3)
    body = await _updates(env, sid)
    kinds = {(x["kind"], x.get("tool")) for x in body["learned"]}
    assert ("new_tool", "code.workspace_map") in kinds           # впервые
    assert ("practiced_tool", "code.search") in kinds            # уже было до сессии
    assert ("new_tool", "code.search") not in kinds
    new = next(x for x in body["learned"] if x["kind"] == "new_tool")
    assert new["count"] == 3 and new["task_ids"] == [t]


async def test_everything_is_first_person_and_has_evidence(env):
    sid = await _session(env)
    t = await _task(env)
    await _append_turn(env.svc, sid, _new_turn(t, "x", []))
    await _call(env, t, "code.search")
    await _call(env, t, "terminal.run", status="denied", n=2)
    body = await _updates(env, sid)
    assert body["learned"]
    for item in body["learned"]:
        assert item["text"].startswith(("Я ", "Мне ")), item["text"]
        assert item["task_ids"] and item["count"] >= 1


async def test_refusals_are_reported_as_limits_not_as_skills(env):
    sid = await _session(env)
    t = await _task(env)
    await _append_turn(env.svc, sid, _new_turn(t, "x", []))
    await _call(env, t, "terminal.run", status="denied", n=2)
    await _call(env, t, "cloud.ask", status="rejected")
    body = await _updates(env, sid)
    ref = [x for x in body["learned"] if x["kind"] == "refusal"]
    assert {x["tool"] for x in ref} == {"terminal.run", "cloud.ask"}
    assert all("не обхожу" in x["text"] for x in ref)
    assert not any(x["kind"] in {"new_tool", "practiced_tool"} for x in body["learned"])   # отказ ≠ освоенный навык


async def test_failed_delegation_is_never_reported_as_learned(env):
    sid = await _session(env)
    parent = await _task(env)
    await _append_turn(env.svc, sid, _new_turn(parent, "x", []))
    child = await _task(env, meta={"delegated_by_task": parent, "root_task_id": parent, "acceptance_criteria": ["contains:ПРИНЯТО"],
                                   "operation_key_ref": "r" * 16, "payload_hash": "h", "reserved_usd": "0"})
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(dbm.task_runs).values(task_id=child, attempt=0, status="completed", result="Готово!",
                                                        model_alias="m", tokens_in=1, tokens_out=1, cost_usd=0.0))
        await s.commit()
    body = await _updates(env, sid)
    d = [x for x in body["learned"] if x["kind"] == "delegation"]
    assert len(d) == 1 and "не подтвердила" in d[0]["text"] and "готовым я это не считаю" in d[0]["text"]
    assert body["counts"]["delegations_verified"] == 0


async def test_verified_delegation_is_reported_with_receipt(env):
    sid = await _session(env)
    parent = await _task(env)
    await _append_turn(env.svc, sid, _new_turn(parent, "x", []))
    child = await _task(env, meta={"delegated_by_task": parent, "root_task_id": parent, "acceptance_criteria": ["contains:ПРИНЯТО"],
                                   "operation_key_ref": "r" * 16, "payload_hash": "h", "reserved_usd": "0"})
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(dbm.task_runs).values(task_id=child, attempt=0, status="completed", result="ИТОГ: ПРИНЯТО",
                                                        model_alias="m", tokens_in=1, tokens_out=1, cost_usd=0.0))
        await s.commit()
    body = await _updates(env, sid)
    d = next(x for x in body["learned"] if x["kind"] == "delegation")
    assert "проверка пройдена" in d["text"] and "receipt" in d["text"]
    assert body["counts"]["delegations_verified"] == 1


async def test_updates_command_is_in_the_palette(env):
    sid = await _session(env)
    pal = (await env.client.get("/api/agent/palette", params={"session_id": sid})).json()
    cmd = next((c for c in pal["commands"] if c["name"] == "updates"), None)
    assert cmd and cmd["kind"] == "action" and cmd["endpoint"].endswith("/updates")
    assert ax.UPDATES_DISCLAIMER and "веса" in ax.UPDATES_DISCLAIMER.lower()
