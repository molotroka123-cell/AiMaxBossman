"""B11 #4 — ручной вызов POST /api/mcp/runtime/servers/{ref}/call ограничен и
очищен так же, как вызов инструмента моделью.

Дефект: маршрут отдавал `res.text` и `res.structured` целиком: ответ чужого
сервера на мегабайты уходил в браузер/пульт, а секрет из ответа (эхо env_keys,
Bearer) — в HTTP-ответ без чистки. Путь модели (`_handler_for`) уже держал
MCP_OUTPUT_LIMIT и bounded_structured.
"""
from __future__ import annotations

import sys

import sqlalchemy as sa

from bcc.db import utcnow
from bcc.features.tools_mcp import MCP_OUTPUT_LIMIT
from bcc.v2.mcp_runtime import MCPCallResult, MCPRuntime
from bcc.v2.tables import mcp_servers as mcp_servers_t

NAME = "bulky"


class _BulkyRuntime(MCPRuntime):
    def __init__(self, result: MCPCallResult) -> None:
        super().__init__()
        self.result = result

    async def ensure(self, spec):
        self.remember(spec)
        return None

    async def call_tool(self, server_id, tool, arguments=None, *, timeout=None):
        return self.result


async def _add(env) -> None:
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name=NAME, transport="stdio", command=[sys.executable, "server.py"], url="",
            cwd="", env_keys=[], enabled=True, status="unknown", created_at=utcnow()))
        await s.commit()


async def test_manual_call_text_and_structured_are_bounded(env):
    await _add(env)
    huge = {"rows": [{"i": i, "blob": "x" * 100} for i in range(3000)]}
    env.svc.mcp = _BulkyRuntime(MCPCallResult(text="A" * 50_000, structured=huge))
    r = await env.client.post(f"/api/mcp/runtime/servers/{NAME}/call", json={"tool": "dump"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["text"]) <= MCP_OUTPUT_LIMIT
    assert body["truncated"] is True
    assert body["structured"] is None and body["structured_omitted"] is True


async def test_manual_call_result_is_redacted(env):
    await _add(env)
    token = "Bearer " + "abcDEF0123456789xyz"
    env.svc.mcp = _BulkyRuntime(MCPCallResult(text=f"auth {token}",
                                              structured={"header": token}))
    r = await env.client.post(f"/api/mcp/runtime/servers/{NAME}/call", json={"tool": "dump"})
    assert r.status_code == 200, r.text
    assert "abcDEF0123456789xyz" not in r.text


async def test_small_manual_result_unchanged(env):
    await _add(env)
    env.svc.mcp = _BulkyRuntime(MCPCallResult(text="эхо: привет", structured={"a": 1}))
    r = await env.client.post(f"/api/mcp/runtime/servers/{NAME}/call", json={"tool": "echo"})
    body = r.json()
    assert body["text"] == "эхо: привет" and body["structured"] == {"a": 1}
    assert body["truncated"] is False and body["structured_omitted"] is False
