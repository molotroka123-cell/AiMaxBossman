"""POST /api/mcp/runtime/servers/{ref}/call — неверное тело запроса = 422, а не 500.

Дефект (zone plugins, 2026-10-06): маршрут брал `await request.json()` и сразу
`body.get(...)`. Тело-список, не-JSON, нечисловой `timeout`, `arguments`
не-объект или `tool` не-строка давали 500 «внутренняя ошибка»; при
нечисловом timeout процесс MCP-сервера к тому моменту уже был ЗАПУЩЕН
(`ensure` стоял раньше разбора timeout). Проверка тела обязана идти до запуска.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import utcnow
from bcc.v2.tables import mcp_servers as mcp_servers_t

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


@pytest.fixture
async def server(env):
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="echo-body", transport="stdio", command=[sys.executable, str(FIXTURE)],
            url="", cwd="", env_keys=[], enabled=True, status="unknown", created_at=utcnow()))
        await s.commit()
    yield "echo-body"
    rt = getattr(env.svc, "mcp", None)
    if rt is not None:
        await rt.shutdown()


BAD_BODIES = [
    [1, 2],
    "echo",
    {"tool": 5},
    {"tool": "echo", "timeout": "abc"},
    {"tool": "echo", "timeout": -1},
    {"tool": "echo", "arguments": ["text"]},
]


@pytest.mark.parametrize("body", BAD_BODIES)
async def test_bad_body_is_422_and_no_process_started(env, server, body):
    r = await env.client.post(f"/api/mcp/runtime/servers/{server}/call", json=body)
    assert r.status_code == 422, r.text
    rt = getattr(env.svc, "mcp", None)
    assert rt is None or not rt.health(server).connected   # сервер не поднимали


async def test_non_json_body_is_422(env, server):
    r = await env.client.post(f"/api/mcp/runtime/servers/{server}/call",
                              content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
