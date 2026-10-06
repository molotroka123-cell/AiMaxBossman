"""MCP: сервер с именем из цифр не должен путаться с id строки другого сервера.

Дефект (zone plugins, 2026-10-06): `_server_row(svc, ref)` трактует любую
строку из цифр как ЧИСЛОВОЙ id. HTTP-маршруты так и задуманы
(`/servers/{id|name}`), но внутренние вызовы передают ИМЯ сервера (id в
рантайме = имя): `_emit_failure` после падения инструмента и периодический
`tick`. Для сервера с именем «1» они находили строку с id=1 — другой сервер —
и помечали unhealthy ЕГО, а упавший оставался «healthy» в UI и роутере.
`POST /api/mcp/servers` имя из цифр принимает.
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import utcnow
from bcc.features import tools_mcp
from bcc.v2.mcp_runtime import MCPRuntime, ServerHealth
from bcc.v2.tables import mcp_servers as mcp_servers_t


async def _add(env, name: str) -> int:
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(mcp_servers_t).values(
            name=name, transport="stdio", command=["python", "server.py"], url="", cwd="",
            env_keys=[], enabled=True, status="healthy", created_at=utcnow()))
        await s.commit()
        return int(res.inserted_primary_key[0])


async def _statuses(env) -> dict[str, str]:
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(mcp_servers_t.c.name, mcp_servers_t.c.status))).fetchall()
    return {r[0]: r[1] for r in rows}


async def _pair(env) -> tuple[int, int]:
    first = await _add(env, "alpha")
    # имя второго сервера совпадает с id первого
    second = await _add(env, str(first))
    assert second != first
    return first, second


class _DeadRuntime(MCPRuntime):
    """Рантайм, в котором сервер `dead` подключён, но упал."""

    def __init__(self, dead: str) -> None:
        super().__init__()
        self.dead = dead

    def health(self, server_id: str) -> ServerHealth:
        if str(server_id) == self.dead:
            return ServerHealth(server_id=self.dead, status="unhealthy", detail="boom")
        return super().health(server_id)

    def statuses(self) -> list[dict]:
        return [{"server": self.dead, "connected": True}]

    async def probe(self, server_id: str) -> ServerHealth:
        return self.health(server_id)


async def test_call_failure_marks_the_numeric_named_server(env):
    first, _ = await _pair(env)
    env.svc.mcp = _DeadRuntime(str(first))
    await tools_mcp._emit_failure(env.svc, str(first), "echo", "boom")
    st = await _statuses(env)
    assert st[str(first)] == "unhealthy"      # упал сервер с именем "1"…
    assert st["alpha"] == "healthy"           # …а не сервер с id=1


async def test_tick_marks_the_numeric_named_server(env):
    first, _ = await _pair(env)
    env.svc.mcp = _DeadRuntime(str(first))
    await tools_mcp.tick(env.svc)
    st = await _statuses(env)
    assert st[str(first)] == "unhealthy"
    assert st["alpha"] == "healthy"


async def test_http_route_still_accepts_numeric_id(env):
    first, _ = await _pair(env)
    r = await env.client.get(f"/api/mcp/runtime/servers/{first}/health")
    assert r.status_code == 200
    assert r.json()["server"] == "alpha"      # маршрут по-прежнему понимает id
