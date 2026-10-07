import json

import httpx

import bcc.features.plugins as P
from bcc.tools import REGISTRY, ToolContext, execute_tool


async def _call(name, args):
    await P.setup(None)
    return await execute_tool(REGISTRY.get(name), args, ToolContext(svc=None, task={}, run_id=1, agent={}, workspace="", call_id="t"))


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network")
    monkeypatch.setattr(httpx.AsyncClient, "send", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)


async def test_telegram_status_has_real_handler():
    await P.setup(None)
    spec = REGISTRY.get("plugin:telegram.status")
    assert spec.handler is P._h_telegram_status
    assert spec.default_effect == "auto"


async def test_telegram_status_reads_local_config_without_token_or_network(tmp_path, monkeypatch):
    home = tmp_path / "tg"
    home.mkdir()
    cfg = home / "config.json"
    cfg.write_text(json.dumps({
        "enabled": True,
        "bot_username": "demo_bot",
        "people": [{"user_id": 111222333, "role": "owner"}, {"user_id": 444, "role": "guest"}],
        "core_token": "SECRET-CORE-123",
    }), "utf-8")
    (home / "credentials.enc").write_text("123456789:AAFAKE-token-value-should-never-leak", "utf-8")
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(cfg))
    _no_network(monkeypatch)
    r = await _call("plugin:telegram.status", {})
    assert not r.error, r.content
    st = r.data["status"]
    assert r.data["performed"] is True
    assert st["configured"] is True and st["enabled"] is True and st["token_stored"] is True
    assert st["bot_username"] == "demo_bot" and st["owner_configured"] is True and st["people_count"] == 2
    assert st["poller"] == "stopped"
    for s in ("AAFAKE", "SECRET-CORE", "111222333"): 
        assert s not in r.content


async def test_telegram_status_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(tmp_path / "none" / "config.json"))
    r = await _call("plugin:telegram.status", {})
    assert not r.error
    assert r.data["status"]["configured"] is False
    assert r.data["status"]["token_stored"] is False
    assert r.data["performed"] is True


async def test_telegram_status_bad_config_is_error(tmp_path, monkeypatch):
    home = tmp_path / "tg"
    home.mkdir()
    cfg = home / "config.json"
    cfg.write_text("not json", "utf-8")
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(cfg))
    r = await _call("plugin:telegram.status", {})
    assert r.error
    assert r.data["performed"] is False


# --- plugin:browser.open (real handler over the existing browser tool) ---

import pytest

from bcc.tools import decide_effect
from bcc.features import tools_browser
from .helpers import make_stack


async def test_browser_open_is_real_and_ask():
    await P.setup(None)
    spec = REGISTRY.get("plugin:browser.open")
    assert spec.handler is P._h_browser_open
    assert spec.default_effect == "ask"
    assert decide_effect(spec, {}, {})[0] == "ask"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8801/api/health",
    "http://localhost/",
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/",
    "file:///C:/Windows/win.ini",
    "javascript:alert(1)",
    "http://user:pw@example.com/",
])
async def test_browser_open_refuses_unsafe_targets(url, monkeypatch):
    monkeypatch.delenv("BCC_BROWSER_ALLOW_PRIVATE", raising=False)
    called = []
    monkeypatch.setattr(tools_browser, "_open", lambda *a, **k: called.append(1))
    r = await _call("plugin:browser.open", {"url": url})
    assert r.error and r.content.startswith("blocked:"), r.content
    assert r.data["performed"] is False
    assert called == []


async def test_browser_open_needs_a_task(monkeypatch):
    monkeypatch.delenv("BCC_BROWSER_ALLOW_PRIVATE", raising=False)
    r = await _call("plugin:browser.open", {"url": "https://93.184.215.14/"})
    assert r.error
    assert r.content.startswith("blocked:"), r.content
    assert r.data["performed"] is False


async def test_browser_open_uses_existing_browser_tool(env, monkeypatch):
    class Fake:
        def __init__(self):
            self.calls = []

        def is_live(self, sid):
            return True

        async def start(self, sid, policy, headless=True):
            pass

        async def stop(self, sid):
            pass

        async def navigate(self, sid, url, **kw):
            self.calls.append((url, kw))
            return {"url": url, "title": "Fake Title", "text": "hello plugin body",
                    "interactive": []}

    fake = Fake()
    monkeypatch.setattr(tools_browser, "_mgr", lambda svc: fake)
    stack = await make_stack(env.client)
    await P.setup(None)
    ctx = ToolContext(svc=env.svc, task=stack["task"], run_id=1, agent={}, workspace="", call_id="b")
    r = await execute_tool(REGISTRY.get("plugin:browser.open"), {"url": "https://93.184.215.14/"}, ctx)
    assert not r.error, r.content
    assert r.data["performed"] is True and r.data["title"] == "Fake Title"
    assert "hello plugin body" in r.content
    assert fake.calls[0][0] == "https://93.184.215.14/"
    assert fake.calls[0][1].get("allow_download") is False


def _live_ok():
    from bcc.browser_runtime import chromium_executable
    if not chromium_executable():
        return False
    try:
        return httpx.get("https://example.com/", timeout=5, trust_env=False).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _live_ok(), reason="no chromium or no internet")
async def test_browser_open_live_example_com(env):
    stack = await make_stack(env.client)
    await P.setup(None)
    ctx = ToolContext(svc=env.svc, task=stack["task"], run_id=1, agent={}, workspace="", call_id="b")
    try:
        r = await execute_tool(REGISTRY.get("plugin:browser.open"), {"url": "https://example.com/"}, ctx)
        assert not r.error, r.content
        assert r.data["performed"] is True
        assert "Example Domain" in r.data["title"]
    finally:
        await tools_browser.close_task_sessions(env.svc, stack["task"]["id"])
