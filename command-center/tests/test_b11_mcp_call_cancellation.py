"""B11 #3 — таймаут/отмена MCP-вызова отменяет сам вызов, а не только ожидание.

Дефект: `_Connection.request` при таймауте отменял лишь свой future, а
задача-водитель (`_pump`) продолжала ждать зависший `call_tool`. Очередь
сервера последовательная, поэтому ОДИН зависший инструмент блокировал все
следующие вызовы (и health-probe) этого сервера навсегда: каждый следующий
вызов тоже упирался в таймаут. Отмена вызывающего (Hard Cancel, таймаут
execute_tool) не доходила до вызова вовсе.

SDK подменяется детерминированным двойником (без процессов и сети): форма
контракта та же — `stdio_client` даёт (read, write), `ClientSession` —
async-контекст с `initialize/call_tool/list_tools`.
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from bcc.v2 import mcp_runtime
from bcc.v2.mcp_hub import MCPServerSpec
from bcc.v2.mcp_runtime import MCPCallError, MCPRuntime

SPEC = MCPServerSpec(id="fake", name="fake", transport="stdio",
                     command=[sys.executable, "server.py"], timeout_seconds=5)


class _Session:
    cancelled: list[str] = []

    def __init__(self, read, write):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name="echo", description="", inputSchema={})])

    async def call_tool(self, name, arguments):
        if name == "hang":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                _Session.cancelled.append(name)
                raise
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"ok {name}")])


@asynccontextmanager
async def _stdio_client(params):
    yield (object(), object())


@pytest.fixture
async def rt(monkeypatch):
    _Session.cancelled = []
    monkeypatch.setattr(mcp_runtime, "load_sdk",
                        lambda: (_Session, _stdio_client, lambda **kw: SimpleNamespace(**kw)))
    runtime = MCPRuntime()
    await runtime.connect(SPEC)
    yield runtime
    await runtime.shutdown()


async def test_timed_out_call_is_cancelled_and_server_stays_usable(rt):
    with pytest.raises(MCPCallError, match="таймаут"):
        await rt.call_tool("fake", "hang", {}, timeout=0.2)
    # следующий вызов не стоит в очереди за зависшим
    res = await asyncio.wait_for(rt.call_tool("fake", "echo", {}, timeout=2.0), 3.0)
    assert res.text == "ok echo"
    await asyncio.sleep(0)
    assert _Session.cancelled == ["hang"]


async def test_caller_cancellation_reaches_the_in_flight_call(rt):
    caller = asyncio.create_task(rt.call_tool("fake", "hang", {}, timeout=30.0))
    await asyncio.sleep(0.05)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    res = await asyncio.wait_for(rt.call_tool("fake", "echo", {}, timeout=2.0), 3.0)
    assert res.text == "ok echo"
    assert _Session.cancelled == ["hang"]
    assert rt.health("fake").status == "healthy"
