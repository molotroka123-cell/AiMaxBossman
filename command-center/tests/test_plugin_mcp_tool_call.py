"""plugin:mcp.tool_call (tree leaf plugin-6) — real call through the existing MCP path.

Before 2026-10-08 the capability was served by the generic stub and always
answered NOT_TESTED_LIVE: an approved call never reached the configured server.
Now it reuses `tools_mcp` (server row, launch allowlist, runtime, output limit).

No mock of the unit under test: a real stdio MCP server built on the official SDK
(tests/fixtures/mcp_echo_server.py) counts every executed tool in a file, so the
negative controls prove that a refused call did NOT run on the server.
Policy ASK is applied by the engine before the handler (asserted separately).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

import bcc.features.plugins as P
from bcc.db import settings_kv, utcnow
from bcc.tools import REGISTRY, ToolContext, decide_effect, execute_tool
from bcc.v2.mcp_runtime import sdk_available
from bcc.v2.tables import mcp_servers as mcp_servers_t

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
needs_sdk = pytest.mark.skipif(not sdk_available(), reason="official MCP SDK not installed")


def _ctx(svc=None) -> ToolContext:
    return ToolContext(svc=svc, task={}, run_id=1, agent={}, workspace="", call_id="mcp-call")


async def _call(args: dict, svc=None):
    await P.setup(None)
    return await execute_tool(REGISTRY.get("plugin:mcp.tool_call"), args, _ctx(svc))


async def _add_server(svc, name: str, command: list[str], env_keys=(), enabled=True) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name=name, transport="stdio", command=command, url="", cwd="",
            env_keys=list(env_keys), enabled=enabled, status="unknown", created_at=utcnow()))
        await s.commit()


async def _set_policy(svc, policy: dict) -> None:
    enc = svc.vault.encrypt(json.dumps(policy))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == "mcp.policy"))
        await s.execute(sa.insert(settings_kv).values(key="mcp.policy", value_enc=enc))
        await s.commit()


def _executed(counter: Path) -> list[str]:
    return counter.read_text(encoding="utf-8").splitlines() if counter.exists() else []


async def test_tool_call_has_a_real_handler_and_stays_ask():
    await P.setup(None)
    spec = REGISTRY.get("plugin:mcp.tool_call")
    assert spec.handler is P._h_mcp_tool_call
    assert spec.default_effect == "ask" and spec.idempotent is False
    effect, _ = decide_effect(spec, {}, {})
    assert effect == "ask"


@pytest.mark.parametrize("args", [{}, {"server": "echo"}, {"tool": "echo"}])
async def test_missing_required_args_are_refused_by_the_engine(args):
    r = await _call(args)
    assert r.error and "отсутствуют обязательные аргументы" in r.content


@pytest.mark.parametrize("args", [
    {"server": "echo", "tool": ""}, {"server": "echo", "tool": 5}, {"server": " ", "tool": "echo"},
    {"server": "echo", "tool": "echo"},          # no Command Center services in ctx
])
async def test_bad_inputs_are_refused_by_the_handler(args):
    r = await _call(args)
    assert r.error and r.content.startswith("blocked:")
    assert r.data["performed"] is False


@needs_sdk
async def test_tool_call_runs_on_the_configured_server(env, tmp_path, monkeypatch):
    counter = tmp_path / "calls.txt"
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(counter))
    await _add_server(env.svc, "echo", [sys.executable, str(FIXTURE)], env_keys=["MCP_ECHO_COUNTER"])
    try:
        r = await _call({"server": "echo", "tool": "echo", "args": {"text": "привет"}}, svc=env.svc)
        assert not r.error, r.content
        assert "эхо: привет" in r.content
        assert r.external is True and r.data["server"] == "echo" and r.data["tool"] == "echo"
        assert _executed(counter) == ["echo"]
    finally:
        rt = getattr(env.svc, "mcp", None)
        if rt is not None:
            await rt.shutdown()


@needs_sdk
async def test_owner_deny_policy_is_not_bypassed_by_the_plugin(env, tmp_path, monkeypatch):
    """Negative control: `deny` for mcp:echo:secret stays a refusal through the plugin,
    and the server never executes the tool; a non-denied tool still runs."""
    counter = tmp_path / "calls.txt"
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(counter))
    await _add_server(env.svc, "echo", [sys.executable, str(FIXTURE)], env_keys=["MCP_ECHO_COUNTER"])
    await _set_policy(env.svc, {"mcp:echo:secret": "deny"})
    try:
        d = await _call({"server": "echo", "tool": "secret", "args": {"text": "x"}}, svc=env.svc)
        assert d.error and "mcp.policy=deny" in d.content and d.data["performed"] is False
        assert "secret" not in _executed(counter)
        ok = await _call({"server": "echo", "tool": "echo", "args": {"text": "y"}}, svc=env.svc)
        assert not ok.error, ok.content
        assert _executed(counter) == ["echo"]
    finally:
        rt = getattr(env.svc, "mcp", None)
        if rt is not None:
            await rt.shutdown()


@needs_sdk
async def test_undeclared_tool_and_unknown_server_are_denied(env, tmp_path, monkeypatch):
    counter = tmp_path / "calls.txt"
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(counter))
    await _add_server(env.svc, "echo", [sys.executable, str(FIXTURE)], env_keys=["MCP_ECHO_COUNTER"])
    try:
        u = await _call({"server": "echo", "tool": "rm_rf_everything"}, svc=env.svc)
        assert u.error and "не объявляет" in u.content and u.data["performed"] is False
        n = await _call({"server": "nope", "tool": "echo"}, svc=env.svc)
        assert n.error and "не настроен" in n.content
        bad = await _call({"server": "echo", "tool": "echo", "args": ["not", "an", "object"]}, svc=env.svc)
        assert bad.error and bad.content.startswith("blocked:")
        assert _executed(counter) == []
    finally:
        rt = getattr(env.svc, "mcp", None)
        if rt is not None:
            await rt.shutdown()


@needs_sdk
async def test_disabled_server_and_command_outside_allowlist_are_not_launched(env):
    await _add_server(env.svc, "off", [sys.executable, str(FIXTURE)], enabled=False)
    await _add_server(env.svc, "evil", ["bash", "-c", "echo hi"])
    off = await _call({"server": "off", "tool": "echo", "args": {"text": "x"}}, svc=env.svc)
    assert off.error and "выключен" in off.content
    evil = await _call({"server": "evil", "tool": "echo"}, svc=env.svc)
    assert evil.error and evil.content.startswith("blocked:")
    rt = getattr(env.svc, "mcp", None)
    h = rt.health("evil") if rt is not None else None
    assert h is None or h.connected is False      # the bash command was never started
