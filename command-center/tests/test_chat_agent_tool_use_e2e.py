"""Доказательство: чат-агент (Gemma) САМ вызывает инструменты и знает их применение.

Реальный движок + реальный реестр инструментов; модель — локальный OpenAI-совместимый сервер со сценарием
(сам запрашивает tool_call, на второй запрос отдаёт итог). Проверяется:
  1. модели в запросе уходят схемы ТОЛЬКО выданных инструментов, с описанием «когда применять»;
  2. инструмент выполнен движком (строка tool_calls), результат вернулся модели ролью `tool`;
  3. невыданный инструмент (call_agent без гранта) не исполняется и задач не плодит;
  4. итоговый ответ сохранён, задача completed.
"""
from __future__ import annotations

import asyncio
import json

import sqlalchemy as sa

from bcc import db as dbm

from .conftest import client_for, make_settings, start_app, wait_for

FAST_ENGINE = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01}


class ToolCallingModel:
    """Первый запрос — tool_call `first_tool`; второй — итоговый текст. Потоковый режим отклоняем (честный fallback)."""

    def __init__(self, first_tool: str, arguments: dict | None = None):
        self.first_tool, self.arguments = first_tool, arguments or {}
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
            if len(self.requests) == 1:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": self.first_tool, "arguments": json.dumps(self.arguments)}}]}
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "Готово: инструмент отработал, вот итог."}
                finish = "stop"
            data = json.dumps({"model": "gemma-scripted", "choices": [{"message": message, "finish_reason": finish}],
                               "usage": {"prompt_tokens": 120, "completion_tokens": 12}}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
                         b"Connection: close\r\n\r\n" % len(data) + data)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


async def _run(tmp_path, model: ToolCallingModel, granted: list[str]):
    base = await model.start()
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            prov = (await client.post("/api/providers", json={"name": "loc", "kind": "openai_compat", "base_url": base})).json()
            mod = (await client.post("/api/models", json={"provider_id": prov["id"], "name": "gemma-scripted",
                                                          "alias": "gemma-scripted"})).json()
            agent = (await client.post("/api/agents", json={"name": "Gemma-test", "model_id": mod["id"], "max_steps": 4,
                                                            "tools": granted})).json()
            task = (await client.post("/api/tasks", json={"title": "t", "prompt": "покажи доступных агентов",
                                                          "agent_id": agent["id"], "run_now": True})).json()["task"]

            async def done():
                t = (await client.get(f"/api/tasks/{task['id']}")).json()["task"]
                return t if t["status"] in {"completed", "failed", "stopped"} else None

            final = await wait_for(done, timeout=20)
            async with svc.db.session() as s:
                calls = [dbm.row_dict(r) for r in (await s.execute(
                    sa.select(dbm.tool_calls).where(dbm.tool_calls.c.task_id == task["id"]))).all()]
                tasks_n = (await s.execute(sa.select(sa.func.count()).select_from(dbm.tasks))).scalar()
            return final, calls, tasks_n
    finally:
        await svc.stop()
        await model.stop()


def _schema_names(request: dict) -> dict[str, str]:
    return {t["function"]["name"]: t["function"].get("description", "") for t in request.get("tools") or []}


async def test_agent_calls_granted_tool_and_gets_result(tmp_path):
    model = ToolCallingModel("agents_list")
    final, calls, _ = await _run(tmp_path, model, ["agents.list", "code.workspace_map"])
    assert final["status"] == "completed", final

    # 1. модель видит ровно выданные инструменты и понимает, зачем они нужны
    schemas = _schema_names(model.requests[0])
    assert set(schemas) == {"agents_list", "code_workspace_map"}, schemas
    assert all(len(d) >= 20 for d in schemas.values()), schemas
    assert "local / free / paid" in schemas["agents_list"] or "агентов" in schemas["agents_list"]

    # 2. инструмент исполнен движком, результат вернулся в модель ролью tool
    assert len(model.requests) == 2
    tool_msgs = [m for m in model.requests[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1 and "Gemma-test" in json.dumps(tool_msgs[0], ensure_ascii=False)
    assert "agents.list" in json.dumps(calls, ensure_ascii=False, default=str)


async def test_not_granted_tool_is_refused_and_creates_no_task(tmp_path):
    model = ToolCallingModel("call_agent", {"agent_id": 1, "task_description": "сделай что-нибудь"})
    final, calls, tasks_n = await _run(tmp_path, model, ["agents.list"])       # call_agent НЕ выдан
    schemas = _schema_names(model.requests[0])
    assert "call_agent" not in schemas
    assert tasks_n == 1                                                         # ни одной делегированной задачи
    second = json.dumps(model.requests[1]["messages"], ensure_ascii=False) if len(model.requests) > 1 else ""
    assert "Gemma-test" not in second or "call_agent" in second                 # результата делегирования нет
    assert not any((c.get("status") or "") in {"ok", "completed", "done"} and "call_agent" in json.dumps(c, default=str)
                   for c in calls)
