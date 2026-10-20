"""B11 #2 — ответ MCP-сервера чистится от секретов до того, как попадёт модели.

Дефект: `tools_mcp._handler_for` отдавал `res.text`/`res.structured` как есть.
Сервер получает значения `env_keys` (например GITHUB_TOKEN) и может вернуть их
эхом — тогда токен уезжал в контекст модели (облачный провайдер) и в журнал
инструментов. Плагины (`http.get`, `github.repo_read`) такое эхо уже чистили,
MCP-путь — нет.
"""
from __future__ import annotations

import sys

from bcc.features.tools_mcp import _handler_for
from bcc.tools import ToolContext
from bcc.v2.mcp_hub import MCPServerSpec
from bcc.v2.mcp_runtime import MCPCallResult

ENV_SECRET = "plain-value-without-known-shape-42"
SPEC = MCPServerSpec(id="leaky", name="leaky", transport="stdio",
                     command=[sys.executable, "server.py"], env_keys=["LEAKY_SERVER_KEY"])


class _FakeRuntime:
    def __init__(self, result):
        self.result = result

    async def ensure(self, spec):
        return None

    async def call_tool(self, server_id, tool, args, **kw):
        return self.result


class _Svc:
    def __init__(self, rt):
        self.mcp = rt


def _ctx(svc):
    return ToolContext(svc=svc, task={"id": 1}, run_id=1, agent={})


async def test_env_key_value_echoed_by_server_is_redacted(monkeypatch):
    monkeypatch.setenv("LEAKY_SERVER_KEY", ENV_SECRET)
    svc = _Svc(_FakeRuntime(MCPCallResult(
        text=f"debug: key={ENV_SECRET!s} raw {ENV_SECRET}",
        structured={"echo": ENV_SECRET, "nested": [f"x {ENV_SECRET}"]})))
    res = await _handler_for(svc, SPEC, "read")({}, _ctx(svc))
    assert ENV_SECRET not in res.content
    assert ENV_SECRET not in str(res.data)
    assert "debug:" in res.content


async def test_token_shapes_in_mcp_text_are_redacted(monkeypatch):
    bearer = "Bearer " + "abcDEF0123456789xyz"
    svc = _Svc(_FakeRuntime(MCPCallResult(text=f"Authorization: {bearer} ok")))
    res = await _handler_for(svc, SPEC, "read")({}, _ctx(svc))
    assert "abcDEF0123456789xyz" not in res.content
    assert res.content.endswith("ok")


async def test_clean_result_is_unchanged():
    svc = _Svc(_FakeRuntime(MCPCallResult(text="эхо: привет", structured={"a": 1})))
    res = await _handler_for(svc, SPEC, "read")({}, _ctx(svc))
    assert res.content == "эхо: привет"
    assert res.data["structured"] == {"a": 1}
