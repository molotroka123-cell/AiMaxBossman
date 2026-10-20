"""B11 #9 — периодический health-check восстанавливает сервер после сбоя пробы.

Дефект: одна неудачная проба (`probe` → ошибка tools/list, например сервер
занят долгим вызовом) ставила `connected=False`, хотя соединение живо. `tick`
пробовал только `connected` серверы, поэтому больше НИКОГДА не проверял этот
сервер: в UI/БД он навсегда оставался unhealthy, хотя вызовы через тот же
рантайм работали. А при успешной пробе `tick` не писал в БД вовсе — строка,
однажды помеченная unhealthy, не возвращалась к healthy.

SDK — детерминированный двойник (без процессов и сети).
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bcc.db import utcnow
from bcc.features import tools_mcp
from bcc.v2 import mcp_runtime
from bcc.v2.mcp_hub import MCPServerSpec
from bcc.v2.mcp_runtime import MCPRuntime
from bcc.v2.tables import mcp_servers as mcp_servers_t

NAME = "flaky"
SPEC = MCPServerSpec(id=NAME, name=NAME, transport="stdio",
                     command=[sys.executable, "server.py"], timeout_seconds=5)


class _Session:
    failures_left = 0

    def __init__(self, read, write):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        if _Session.failures_left > 0:
            _Session.failures_left -= 1
            raise RuntimeError("server busy")
        return SimpleNamespace(tools=[SimpleNamespace(name="echo", description="", inputSchema={})])


@asynccontextmanager
async def _stdio_client(params):
    yield (object(), object())


@pytest.fixture
async def rt(env, monkeypatch):
    monkeypatch.setattr(mcp_runtime, "load_sdk",
                        lambda: (_Session, _stdio_client, lambda **kw: SimpleNamespace(**kw)))
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name=NAME, transport="stdio", command=list(SPEC.command), url="", cwd="",
            env_keys=[], enabled=True, status="healthy", created_at=utcnow()))
        await s.commit()
    runtime = MCPRuntime()
    await runtime.connect(SPEC)
    env.svc.mcp = runtime
    yield runtime
    await runtime.shutdown()


async def _db_status(env) -> str:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(mcp_servers_t.c.status)
                               .where(mcp_servers_t.c.name == NAME))).first()
    return row[0]


async def test_tick_reprobes_and_recovers_after_failed_probe(env, rt):
    _Session.failures_left = 1
    await tools_mcp.tick(env.svc)                    # проба падает → unhealthy
    assert rt.health(NAME).status == "unhealthy"
    assert await _db_status(env) == "unhealthy"

    await tools_mcp.tick(env.svc)                    # соединение живо → повторная проба
    assert rt.health(NAME).status == "healthy"
    assert rt.health(NAME).connected is True
    assert await _db_status(env) == "healthy"


async def test_statuses_report_running_connection(rt):
    _Session.failures_left = 1
    await rt.probe(NAME)
    st = {s["server"]: s for s in rt.statuses()}
    assert st[NAME]["connected"] is False and st[NAME]["running"] is True
