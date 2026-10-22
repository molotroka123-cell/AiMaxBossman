"""Уровень 3: Gemma → НАСТОЯЩИЙ локальный помощник → проверенный артефакт → один receipt; STOP родителя гасит цепочку.

Здесь НИЧЕГО не подменяется в исполнении: оба агента запускает реальный TaskEngine, обе модели — отдельные
локальные OpenAI-совместимые серверы (scripted: они лишь выбирают вызов инструмента / текст ответа).
Доказывается: родитель сам вызвал call_agent (через настоящее подтверждение владельца), ребёнок реально исполнился на
СВОЕЙ модели, независимая проверка поймала артефакт, receipt одинаков для API desktop/mobile, повтор намерения не
создаёт вторую работу, STOP родителя останавливает ребёнка.
"""
from __future__ import annotations

import asyncio
import json
import re

import sqlalchemy as sa

from bcc import db as dbm
from bcc.features import tools_agents
from bcc.tools import ToolContext

from .conftest import client_for, make_settings, start_app, wait_for

FAST_ENGINE = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01}
OP_KEY = "op-e2e-level3-chain-0001"
CRITERIA = ["contains:ИТОГ: ПРИНЯТО", "max_chars:200"]


class ScriptedModel:
    """Локальный OpenAI-совместимый сервер. `script(n, body)` → (message, finish). Потоковый режим отклоняется (honest fallback)."""

    def __init__(self, script, *, alias: str):
        self.script, self.alias = script, alias
        self.requests: list[dict] = []
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v1"

    async def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next((int(x.split(b":")[1]) for x in head.split(b"\r\n") if x.lower().startswith(b"content-length:")), 0)
            body = json.loads(await reader.readexactly(length) or b"{}")
            if body.get("stream"):
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            self.requests.append(body)
            message, finish = await self.script(len(self.requests), body)
            data = json.dumps({"model": self.alias, "choices": [{"message": message, "finish_reason": finish}],
                               "usage": {"prompt_tokens": 120, "completion_tokens": 12}}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
                         b"Connection: close\r\n\r\n" % len(data) + data)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        finally:
            writer.close()


def _tool_call(name: str, arguments: dict) -> tuple[dict, str]:
    return ({"role": "assistant", "content": None, "tool_calls": [{
        "id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]},
            "tool_calls")


def _text(text: str) -> tuple[dict, str]:
    return {"role": "assistant", "content": text}, "stop"


def _last_tool_text(body: dict) -> str:
    return next((str(m.get("content")) for m in reversed(body["messages"]) if m.get("role") == "tool"), "")


async def _register(client, base: str, alias: str, name: str, tools: list[str]) -> int:
    prov = (await client.post("/api/providers", json={"name": f"loc-{alias}", "kind": "openai_compat", "base_url": base})).json()
    mod = (await client.post("/api/models", json={"provider_id": prov["id"], "name": alias, "alias": alias})).json()
    return (await client.post("/api/agents", json={"name": name, "model_id": mod["id"], "max_steps": 5, "tools": tools})).json()["id"]


async def _approve_pending_tool(client, tool: str = "call_agent") -> dict:
    async def pending():
        rows = (await client.get("/api/approvals", params={"status": "pending"})).json()
        rows = rows.get("approvals", rows) if isinstance(rows, dict) else rows
        return next((r for r in rows if tool in str(r.get("preview") or "") or tool in json.dumps(r, ensure_ascii=False)), None)
    appr = await wait_for(pending, timeout=15)
    res = await client.post(f"/api/approvals/{appr['id']}", json={"approve": True})
    assert res.status_code == 200, res.text
    return appr


async def _all_tasks(svc) -> list[dict]:
    async with svc.db.session() as s:
        return [dbm.row_dict(r) for r in (await s.execute(sa.select(dbm.tasks).order_by(dbm.tasks.c.id))).all()]


async def test_level3_gemma_delegates_to_real_local_child_verifies_and_receipts(tmp_path):
    child_done = asyncio.Event()
    args = {"agent_id": 0, "task_description": "Подведи итог проверки одной строкой", "operation_key": OP_KEY,
            "acceptance_criteria": CRITERIA}

    async def child_script(n, body):
        return _text("ИТОГ: ПРИНЯТО — проверка синтетической фикстуры пройдена")

    child = ScriptedModel(child_script, alias="child-local-scripted")
    child_base = await child.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    parent = None

    async def parent_script(n, body):
        if n == 1:
            return _tool_call("call_agent", args)
        if n == 2:
            await asyncio.wait_for(child_done.wait(), timeout=30)       # ребёнок реально должен закончить сам
            cid = int(re.search(r"#(\d+)", _last_tool_text(body)).group(1))
            return _tool_call("agents_result", {"task_id": cid})
        return _text("Помощник завершил работу, итог принят проверкой.")

    parent = ScriptedModel(parent_script, alias="gemma-parent-scripted")
    parent_base = await parent.start()
    try:
        async with client_for(app, svc) as client:
            child_agent = await _register(client, child_base, "child-local-scripted", "Local Reviewer", ["code.search", "terminal.run"])
            args["agent_id"] = child_agent
            parent_agent = await _register(client, parent_base, "gemma-parent-scripted", "Gemma Manager",
                                           ["agents.list", "call_agent", "agents.result"])
            task = (await client.post("/api/tasks", json={"title": "root", "prompt": "поручи проверку помощнику",
                                                          "agent_id": parent_agent, "run_now": True})).json()["task"]
            await _approve_pending_tool(client)                        # настоящее подтверждение владельца

            async def child_task():
                rows = [t for t in await _all_tasks(svc) if (t["meta"] or {}).get("delegated_by_task") == task["id"]]
                return rows[0] if rows else None
            ch = await wait_for(child_task, timeout=15)

            async def child_completed():
                t = (await client.get(f"/api/tasks/{ch['id']}")).json()["task"]
                return t if t["status"] == "completed" else None
            await wait_for(child_completed, timeout=20)
            child_done.set()

            async def root_done():
                t = (await client.get(f"/api/tasks/{task['id']}")).json()["task"]
                return t if t["status"] in {"completed", "failed", "stopped"} else None
            root = await wait_for(root_done, timeout=30)
            assert root["status"] == "completed", root

            # 1. родитель сам вызвал инструмент; модель видела схемы ТОЛЬКО выданных ей инструментов
            names = {t["function"]["name"] for t in parent.requests[0]["tools"]}
            assert names == {"agents_list", "call_agent", "agents_result"}
            # 2. ребёнок исполнился на СВОЕЙ модели с read-only конвертом: ни terminal.run, ни call_agent
            assert len(child.requests) == 1
            child_tools = {t["function"]["name"] for t in child.requests[0].get("tools") or []}
            assert child_tools <= {"code_search"} and "terminal_run" not in child_tools and "call_agent" not in child_tools
            # 3. родитель получил проверенный результат и receipt, а не слова автора
            third = _last_tool_text(parent.requests[2])
            assert "SUCCEEDED" in third and "PASS" in third and "receipt" in third
            # 4. единый receipt — один и тот же по API (desktop и mobile читают его же)
            rc_resp = (await client.get(f"/api/agents/delegations/{ch['id']}/receipt")).json()
            rc = rc_resp["receipt"]
            assert rc_resp["state"] == "SUCCEEDED" and rc["postcondition"]["verdict"] == "PASS"
            assert rc["model_actual"] == "child-local-scripted" and rc["requested_model"] == "child-local-scripted"
            assert rc["tokens"] == {"in": 120, "out": 12, "source": "provider_usage"}
            assert rc["root_task_id"] == task["id"] == rc["parent_task_id"] and rc["child_task_id"] == ch["id"]
            assert rc["tools_allowed"] == ["code.search"]
            assert rc["receipt_id"] in third
            listing = (await client.get("/api/agents/delegations", params={"parent": task["id"]})).json()["items"]
            assert [(i["child_task_id"], i["state"], i["receipt_id"]) for i in listing] == [(ch["id"], "SUCCEEDED", rc["receipt_id"])]
            # 5. повтор того же намерения (таймаут/двойной тап) не создаёт вторую работу и не гоняет вторую модель
            before = len(await _all_tasks(svc))
            ctx = ToolContext(svc=svc, task={"id": task["id"], "meta": {}}, run_id=1, agent={"id": parent_agent})
            again = await tools_agents.tool_call_agent(dict(args), ctx)
            assert not again.error and again.data["replayed"] is True and again.data["task_id"] == ch["id"]
            assert len(await _all_tasks(svc)) == before and len(child.requests) == 1
    finally:
        child_done.set()
        await svc.stop()
        await parent.stop()
        await child.stop()


async def test_level3_parent_stop_cancels_running_child_and_blocks_new_dispatch(tmp_path):
    release = asyncio.Event()
    args = {"agent_id": 0, "task_description": "долгая проверка", "operation_key": OP_KEY + "-stop", "acceptance_criteria": CRITERIA}
    child_started = asyncio.Event()

    async def child_script(n, body):
        child_started.set()
        await release.wait()                                            # ребёнок «думает», пока владелец не нажмёт STOP
        return _text("ИТОГ: ПРИНЯТО")

    async def parent_script(n, body):
        if n == 1:
            return _tool_call("call_agent", args)
        await asyncio.sleep(30)
        return _text("не должно дойти")

    child, parent = ScriptedModel(child_script, alias="child-hold"), ScriptedModel(parent_script, alias="parent-hold")
    cb, pb = await child.start(), await parent.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            child_agent = await _register(client, cb, "child-hold", "Slow Child", ["code.search"])
            args["agent_id"] = child_agent
            parent_agent = await _register(client, pb, "parent-hold", "Gemma Manager 2", ["call_agent"])
            task = (await client.post("/api/tasks", json={"title": "root", "prompt": "поручи", "agent_id": parent_agent,
                                                          "run_now": True})).json()["task"]
            await _approve_pending_tool(client)
            await asyncio.wait_for(child_started.wait(), timeout=20)    # ребёнок реально исполняется на модели

            async def child_row():
                rows = [t for t in await _all_tasks(svc) if (t["meta"] or {}).get("delegated_by_task") == task["id"]]
                return rows[0] if rows else None
            ch = await wait_for(child_row, timeout=10)
            assert (await client.get(f"/api/tasks/{ch['id']}")).json()["task"]["status"] in {"running", "queued"}

            stop = await client.post(f"/api/tasks/{task['id']}/stop")
            assert stop.status_code == 200, stop.text

            async def both_stopped():
                a = (await client.get(f"/api/tasks/{task['id']}")).json()["task"]["status"]
                b = (await client.get(f"/api/tasks/{ch['id']}")).json()["task"]["status"]
                return (a, b) == ("stopped", "stopped")
            await wait_for(both_stopped, timeout=15)                    # весь связанный набор остановлен одним STOP

            n_tasks = len(await _all_tasks(svc))
            ctx = ToolContext(svc=svc, task={"id": task["id"], "meta": {}}, run_id=1, agent={"id": parent_agent})
            late = await tools_agents.tool_call_agent({**args, "operation_key": OP_KEY + "-late", "task_description": "ещё"}, ctx)
            assert late.error is True and "остановлен" in late.content
            assert len(await _all_tasks(svc)) == n_tasks                # после STOP новых стартов нет
            rc = (await client.get(f"/api/agents/delegations/{ch['id']}/receipt")).json()
            assert rc["state"] == "CANCELLED" and rc["receipt"]["stop_outcome"]["local"] == "cancelled"
    finally:
        release.set()
        await svc.stop()
        await parent.stop()
        await child.stop()
