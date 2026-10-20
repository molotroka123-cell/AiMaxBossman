"""B11 #10 — plugin:mcp.tool_call не обходит запрет агента на целевой MCP-инструмент.

Дефект: правило агента `{"tool": "mcp:echo:*", "effect": "deny"}` закрывает
прямой вызов `mcp:echo:write_note`, но тот же инструмент вызывался через
`plugin:mcp.tool_call` с аргументами server=echo/tool=write_note: хендлер
проверял только `mcp.policy` владельца, а правила агента — нет. ASK плагина
лишь спрашивал одобрение, хотя по политике агента вызов запрещён вовсе
(DENY — пол, его не снимает никакое одобрение).

Сервер — настоящий stdio MCP на официальном SDK; счётчик в файле доказывает,
что запрещённый вызов на сервер НЕ попал.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

import bcc.features.plugins as P
from bcc.db import utcnow
from bcc.tools import REGISTRY, ToolContext, execute_tool
from bcc.v2.mcp_runtime import sdk_available
from bcc.v2.tables import mcp_servers as mcp_servers_t

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
pytestmark = pytest.mark.skipif(not sdk_available(), reason="official MCP SDK not installed")

DENY_ECHO = {"permissions": {"tool_rules": [
    {"tool": "mcp:echo:write_note", "effect": "deny", "reason": "запись запрещена"}]}}


async def _setup(env, tmp_path, monkeypatch) -> Path:
    counter = tmp_path / "calls.txt"
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(counter))
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="echo", transport="stdio", command=[sys.executable, str(FIXTURE)], url="",
            cwd="", env_keys=["MCP_ECHO_COUNTER"], enabled=True, status="unknown",
            created_at=utcnow()))
        await s.commit()
    await P.setup(None)
    return counter


def _ctx(svc, agent) -> ToolContext:
    return ToolContext(svc=svc, task={}, run_id=1, agent=agent, workspace="", call_id="b11")


async def test_agent_deny_rule_blocks_plugin_route(env, tmp_path, monkeypatch):
    counter = await _setup(env, tmp_path, monkeypatch)
    try:
        r = await execute_tool(REGISTRY.get("plugin:mcp.tool_call"),
                               {"server": "echo", "tool": "write_note", "args": {"text": "x"}},
                               _ctx(env.svc, DENY_ECHO))
        assert r.error and r.content.startswith("blocked:"), r.content
        assert "запрещ" in r.content
        assert r.data["performed"] is False
        lines = counter.read_text(encoding="utf-8").splitlines() if counter.exists() else []
        assert "write_note" not in lines
    finally:
        rt = getattr(env.svc, "mcp", None)
        if rt is not None:
            await rt.shutdown()


async def test_other_tool_of_same_server_still_runs(env, tmp_path, monkeypatch):
    counter = await _setup(env, tmp_path, monkeypatch)
    try:
        r = await execute_tool(REGISTRY.get("plugin:mcp.tool_call"),
                               {"server": "echo", "tool": "echo", "args": {"text": "привет"}},
                               _ctx(env.svc, DENY_ECHO))
        assert not r.error, r.content
        assert "эхо: привет" in r.content
        assert counter.read_text(encoding="utf-8").splitlines() == ["echo"]
    finally:
        rt = getattr(env.svc, "mcp", None)
        if rt is not None:
            await rt.shutdown()
