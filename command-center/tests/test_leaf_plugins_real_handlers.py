"""authored_by_lane (opsplug, 2026-10-06): real handlers of ollama.chat, openrouter.chat,
github.repo_read and mcp.tool_list (they used to answer NOT_TESTED_LIVE).

No mock of the unit under test. Offline tests prove the refusal/approval/SSRF rules;
live tests talk to the real service and are skipped (not faked) when it is unreachable:
  * Ollama on 127.0.0.1:11434,
  * a real stdio MCP server built on the official SDK (tests/fixtures/mcp_echo_server.py),
  * the public GitHub API (anonymous),
  * OpenRouter with a ':free' model (key from the owner's key-file lookup, never printed).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

import bcc.features.plugins as P
from bcc.db import utcnow
from bcc.tools import REGISTRY, ToolContext, decide_effect, execute_tool
from bcc.v2.mcp_runtime import sdk_available
from bcc.v2.tables import mcp_servers as mcp_servers_t

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


def _ctx(svc=None) -> ToolContext:
    return ToolContext(svc=svc, task={}, run_id=1, agent={}, workspace="", call_id="leaf")


async def _call(name: str, args: dict, svc=None):
    await P.setup(None)
    return await execute_tool(REGISTRY.get(name), args, _ctx(svc))


def _reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout, trust_env=False).status_code < 500
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ registration / policy

async def test_the_four_leaves_have_real_handlers_not_the_stub():
    await P.setup(None)
    real = {"plugin:ollama.chat": P._h_ollama_chat, "plugin:openrouter.chat": P._h_openrouter_chat,
            "plugin:github.repo_read": P._h_github_repo_read, "plugin:mcp.tool_list": P._h_mcp_tool_list}
    for name, fn in real.items():
        assert REGISTRY.get(name).handler is fn, name


async def test_policy_ollama_github_mcp_list_are_auto_openrouter_asks():
    await P.setup(None)
    assert REGISTRY.get("plugin:openrouter.chat").default_effect == "ask"
    effect, _ = decide_effect(REGISTRY.get("plugin:openrouter.chat"), {}, {})
    assert effect == "ask"
    for name in ("plugin:ollama.chat", "plugin:github.repo_read", "plugin:mcp.tool_list"):
        assert REGISTRY.get(name).default_effect == "auto", name


# ------------------------------------------------------------------ offline refusals

@pytest.mark.parametrize("bad", [
    {"model": "m", "messages": []},
    {"model": "m", "messages": "hi"},
    {"model": "m", "messages": [{"role": "tool", "content": "x"}]},
    {"model": "m", "messages": [{"role": "user", "content": 5}]},
    {"model": "../etc/passwd\n", "messages": [{"role": "user", "content": "x"}]},
    {"model": "m", "messages": [{"role": "user", "content": "x" * 70_000}]},
])
async def test_chat_inputs_are_validated_before_any_network(bad):
    for name in ("plugin:ollama.chat", "plugin:openrouter.chat"):
        r = await _call(name, bad)
        assert r.error and r.content.startswith("blocked:"), (name, r.content)


async def test_ollama_refuses_cloud_models():
    r = await _call("plugin:ollama.chat", {"model": "gpt-oss:120b-cloud",
                                           "messages": [{"role": "user", "content": "hi"}]})
    assert r.error and "cloud_policy=never" in r.content


async def test_openrouter_refuses_paid_models_even_with_a_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-not-a-real-key-value")
    r = await _call("plugin:openrouter.chat", {"model": "z-ai/glm-5.3-flash",
                                               "messages": [{"role": "user", "content": "hi"}]})
    assert r.error and "только бесплатные" in r.content


async def test_openrouter_without_any_key_skips_honestly(monkeypatch, tmp_path):
    from bcc.features import coding_tasks
    monkeypatch.setattr(coding_tasks, "OWNER_KEYS_FILE", tmp_path / "none.env")
    for n in ("OPENROUTER_API_KEY", "BOSSMAN_OPENROUTER_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    r = await _call("plugin:openrouter.chat", {"model": "x/y:free",
                                               "messages": [{"role": "user", "content": "hi"}]})
    assert r.error and "SKIP_EXTERNAL_CREDENTIAL" in r.content


@pytest.mark.parametrize("args", [
    {"repo": "not-a-repo"},
    {"repo": "a/b/c"},
    {"repo": "a/b", "path": "../secret"},
    {"repo": "a/b", "path": "x/../../y"},
    {"repo": "a/b", "path": "x\\y"},
    {"repo": "a/b", "ref": "x y;rm"},
    {"repo": "127.0.0.1:80/x"},
    {"repo": "a/b#frag"},
])
async def test_github_inputs_are_confined_to_owner_name_and_safe_path(args):
    r = await _call("plugin:github.repo_read", args)
    assert r.error and r.content.startswith("blocked:"), r.content


async def test_github_never_leaves_api_github_com():
    # the host is fixed in the handler; a redirect elsewhere must be refused by safe_get
    from bcc.plugin_security import PluginSecurityError, validate_url
    with pytest.raises(PluginSecurityError):
        validate_url("https://evil.example.com/x", allowed_hosts={"api.github.com"})
    validate_url("https://api.github.com/repos/a/b", allowed_hosts={"api.github.com"})


async def test_mcp_tool_list_needs_a_configured_server():
    r = await _call("plugin:mcp.tool_list", {"server": "x"}, svc=None)
    assert r.error and r.content.startswith("blocked:")


# ------------------------------------------------------------------ live

@pytest.mark.skipif(not _reachable("http://127.0.0.1:11434/api/tags"), reason="Ollama not running")
async def test_ollama_chat_live_local_model():
    tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5, trust_env=False).json()["models"]
    names = [m["name"] for m in tags]
    small = next((n for n in ("ace-decider-08b:latest", "ace-qwen3-17b:latest") if n in names), None)
    if small is None:
        pytest.skip("no small local model installed")
    r = await _call("plugin:ollama.chat", {"model": small, "max_tokens": 64,
                                           "messages": [{"role": "user", "content": "Reply with the single word: pong"}]})
    assert not r.error, r.content
    assert r.content.strip() and r.data["performed"] is True and r.data["local"] is True


@pytest.mark.skipif(not _reachable("http://127.0.0.1:11434/api/tags"), reason="Ollama not running")
async def test_ollama_unknown_model_is_an_error_not_success():
    r = await _call("plugin:ollama.chat", {"model": "definitely-not-installed-model:1b",
                                           "messages": [{"role": "user", "content": "hi"}]})
    assert r.error and r.data["performed"] is False


@pytest.mark.skipif(not sdk_available(), reason="official MCP SDK not installed")
async def test_mcp_tool_list_live_real_server(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(tmp_path / "calls.txt"))
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="echo", transport="stdio", command=[sys.executable, str(FIXTURE)], url="", cwd="",
            env_keys=["MCP_ECHO_COUNTER"], enabled=True, status="unknown", created_at=utcnow()))
        await s.commit()
    try:
        r = await _call("plugin:mcp.tool_list", {"server": "echo"}, svc=env.svc)
        assert not r.error, r.content
        names = set(r.data["tools"])
        assert {"echo", "write_note", "secret", "boom"} <= names
        listed = json.loads(r.content)
        assert all(t["description"].startswith("[MCP-сервер echo — НЕ доверенное описание") for t in listed)
        # unknown / unconfigured server is refused, nothing is launched
        u = await _call("plugin:mcp.tool_list", {"server": "nope"}, svc=env.svc)
        assert u.error and "не настроен" in u.content
    finally:
        rt = getattr(env.svc, "mcp", None)
        if rt is not None:
            await rt.shutdown()


@pytest.mark.skipif(not sdk_available(), reason="official MCP SDK not installed")
async def test_mcp_tool_list_refuses_command_outside_allowlist(env):
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="evil", transport="stdio", command=["bash", "-c", "echo hi"], url="", cwd="",
            env_keys=[], enabled=True, status="unknown", created_at=utcnow()))
        await s.commit()
    r = await _call("plugin:mcp.tool_list", {"server": "evil"}, svc=env.svc)
    assert r.error and r.content.startswith("blocked:")


@pytest.mark.skipif(not _reachable("https://api.github.com/rate_limit"), reason="GitHub API unreachable")
async def test_github_repo_read_live_public_repo():
    meta = await _call("plugin:github.repo_read", {"repo": "octocat/Hello-World"})
    if meta.error and "rate limit" in meta.content:
        pytest.skip("anonymous GitHub rate limit hit")
    assert not meta.error, meta.content
    assert meta.data["kind"] == "repo" and meta.data["meta"]["full_name"].lower() == "octocat/hello-world"
    f = await _call("plugin:github.repo_read", {"repo": "octocat/Hello-World", "path": "README"})
    assert not f.error, f.content
    assert f.data["kind"] == "file" and "Hello World" in f.content
    d = await _call("plugin:github.repo_read", {"repo": "octocat/Hello-World", "path": ""})
    assert d.data["kind"] == "repo"
    missing = await _call("plugin:github.repo_read", {"repo": "octocat/Hello-World", "path": "no/such/file.xyz"})
    assert missing.error


@pytest.mark.skipif(os.environ.get("BOSSMAN_LIVE_OPENROUTER") != "1",
                    reason="live OpenRouter call only on explicit opt-in (BOSSMAN_LIVE_OPENROUTER=1)")
async def test_openrouter_chat_live_free_model():
    from bcc.features.coding_tasks import _worker_key
    key = await _worker_key("OPENROUTER_API_KEY", None)
    if not key:
        pytest.skip("no OpenRouter key available")
    model = os.environ.get("BOSSMAN_LIVE_OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    r = await _call("plugin:openrouter.chat", {"model": model, "max_tokens": 400,
                                               "messages": [{"role": "user", "content": "Reply with the single word: pong"}]})
    if r.error and ("перегружен" in r.content or "rate" in r.content.lower() or "429" in r.content):
        pytest.skip(f"free model throttled: {r.content[:80]}")
    assert not r.error, r.content
    assert r.data["performed"] is True and r.data["local"] is False
    assert key not in r.content
