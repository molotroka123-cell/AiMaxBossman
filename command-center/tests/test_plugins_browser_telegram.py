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
