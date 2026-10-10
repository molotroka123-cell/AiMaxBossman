"""Secret scrub must cover the key the plugin reads from the VAULT, not only env.

Hypothesis from the plugins-zone audit (2026-10-08), reproduced on 1eac8ac:
an OpenRouter key entered in the UI lives encrypted in the vault (provider row),
`resolve_cred("OPENROUTER_API_KEY", svc)` returns it, but `_known_secret_values()`
looked only at the environment. A page / repo file / browser page / model answer /
provider error text echoing that vault-only key came back from the plugin
unredacted. Every handler that scrubs output now uses the svc-aware set.

The key is a fake fixture value; it is never printed by the code under test.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest
import sqlalchemy as sa

import bcc.features.plugins as P
from bcc import providers as prov
from bcc.db import providers as providers_t
from bcc.features import coding_tasks, tools_browser
from bcc.tools import REGISTRY, ToolContext, ToolResult, execute_tool
from bcc.v2 import openrouter_ext
from bcc.v2 import openrouter_identity as identity

VAULT_KEY = "sk-or-v1-vaultONLYvalue0123456789abcdef"  # ci-secret-scan: allow (fake fixture value)
UNRELATED = "plain-public-value-42"


@pytest.fixture
async def vault_only_key(env, monkeypatch, tmp_path):
    """OpenRouter key stored ONLY in the vault (as the UI Connect stores it)."""
    monkeypatch.delenv(identity.ENV_API_KEY, raising=False)
    for legacy in identity.LEGACY_ENV_API_KEYS:
        monkeypatch.delenv(legacy, raising=False)
    # the owner key file is a separate source; keep it out so the key is vault-only
    monkeypatch.setattr(coding_tasks, "OWNER_KEYS_FILE", tmp_path / "absent-provider-keys.env")
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(providers_t).values(
            name="OpenRouter", kind="openai_compat", base_url=openrouter_ext.DEFAULT_BASE,
            api_key_enc=env.svc.vault.encrypt(VAULT_KEY)))
        pid = int(res.inserted_primary_key[0])
        await s.commit()
    await identity.remember_provider(env.svc.db, env.svc.vault, pid)
    # preconditions: the plugin really uses this key, and env does not know it
    assert await P.resolve_cred("OPENROUTER_API_KEY", env.svc) == VAULT_KEY
    assert VAULT_KEY not in P._known_secret_values()
    await P.setup(None)
    return env.svc


def _ctx(svc, task=None) -> ToolContext:
    return ToolContext(svc=svc, task=task or {}, run_id=1, agent={}, workspace="", call_id="vault-redact")


@pytest.mark.parametrize("tool", ["plugin:http.get", "plugin:monitor.feed"])
async def test_http_get_does_not_echo_a_vault_only_key(vault_only_key, monkeypatch, tool):
    async def fake_safe_get(url, **kw):
        return httpx.Response(200, content=f"leaked: {VAULT_KEY} | {UNRELATED} end".encode())
    monkeypatch.setattr(P, "safe_get", fake_safe_get)
    r = await execute_tool(REGISTRY.get(tool), {"url": "https://example.com/"}, _ctx(vault_only_key))
    assert not r.error, r.content
    assert VAULT_KEY not in r.content
    # negative control: the rest of the page, including unrelated values, survives
    assert "leaked:" in r.content and UNRELATED in r.content and "end" in r.content


async def test_github_repo_read_does_not_echo_a_vault_only_key(vault_only_key, monkeypatch):
    payloads = {
        "https://api.github.com/repos/o/r": {"full_name": "o/r", "description": f"d {VAULT_KEY} {UNRELATED}"},
        "https://api.github.com/repos/o/r/contents/a.txt": {
            "type": "file", "encoding": "base64", "size": 80, "sha": "abc",
            "content": base64.b64encode(f"key={VAULT_KEY}\n{UNRELATED}\n".encode()).decode()},
    }

    async def fake_safe_get(url, **kw):
        return httpx.Response(200, json=payloads[url])
    monkeypatch.setattr(P, "safe_get", fake_safe_get)
    spec = REGISTRY.get("plugin:github.repo_read")
    for args in ({"repo": "o/r"}, {"repo": "o/r", "path": "a.txt"}):
        r = await execute_tool(spec, args, _ctx(vault_only_key))
        assert not r.error, r.content
        assert VAULT_KEY not in r.content, args
        assert UNRELATED in r.content, args


async def test_browser_open_does_not_echo_a_vault_only_key(vault_only_key, monkeypatch):
    monkeypatch.delenv("BCC_BROWSER_ALLOW_PRIVATE", raising=False)

    async def fake_open(args, ctx):
        return ToolResult(content=f"Заголовок: Page\nbody {VAULT_KEY} {UNRELATED}",
                          one_line="browser.open", data={"url": args["url"], "session_id": "s1"})
    monkeypatch.setattr(tools_browser, "_open", fake_open)
    r = await execute_tool(REGISTRY.get("plugin:browser.open"), {"url": "https://93.184.215.14/"},
                           _ctx(vault_only_key, task={"id": 1}))
    assert not r.error, r.content
    assert VAULT_KEY not in r.content
    assert UNRELATED in r.content and r.data["title"] == "Page"


class _EchoAdapter:
    def __init__(self, *, text: str = "", error: str = ""):
        self.text, self.error = text, error

    async def chat(self, model, messages, **kw):
        if self.error:
            raise prov.ProviderError(self.error)
        return prov.ChatResult(text=self.text, tokens_in=1, tokens_out=1, model=model)


async def test_local_model_answer_does_not_echo_a_vault_only_key(vault_only_key, monkeypatch):
    adapter = _EchoAdapter(text=f"echo {VAULT_KEY} {UNRELATED}")
    monkeypatch.setattr(prov, "build_adapter", lambda *a, **k: adapter)
    r = await execute_tool(REGISTRY.get("plugin:ollama.chat"),
                           {"model": "llama3", "messages": [{"role": "user", "content": "hi"}]},
                           _ctx(vault_only_key))
    assert not r.error, r.content
    assert VAULT_KEY not in r.content and UNRELATED in r.content


async def test_openrouter_error_text_does_not_echo_the_key(vault_only_key, monkeypatch):
    adapter = _EchoAdapter(error=f"провайдер отказал (401): bad key {VAULT_KEY} {UNRELATED}")
    seen_keys = []

    def build(kind, base_url="", api_key=None, transport=None):
        seen_keys.append(api_key)
        return adapter
    monkeypatch.setattr(prov, "build_adapter", build)
    r = await execute_tool(REGISTRY.get("plugin:openrouter.chat"),
                           {"model": "x/y:free", "messages": [{"role": "user", "content": "hi"}]},
                           _ctx(vault_only_key))
    assert seen_keys == [VAULT_KEY]          # the plugin really called with the vault key
    assert r.error and r.data["performed"] is False
    assert VAULT_KEY not in r.content
    assert "401" in r.content and UNRELATED in r.content


async def test_telegram_status_does_not_echo_a_vault_only_key(vault_only_key, monkeypatch, tmp_path):
    home = tmp_path / "tg"
    home.mkdir()
    cfg = home / "config.json"
    cfg.write_text(json.dumps({"enabled": True, "bot_username": f"{VAULT_KEY}", "people": []}), "utf-8")
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(cfg))
    r = await execute_tool(REGISTRY.get("plugin:telegram.status"), {}, _ctx(vault_only_key))
    assert not r.error, r.content
    assert VAULT_KEY not in r.content
    assert '"poller"' in r.content


async def test_secret_set_is_env_only_without_services(monkeypatch):
    """No svc (e.g. the dashboard probe) — the set is exactly the env set, nothing invented."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-envKEYvalue0123456789abcdef")  # ci-secret-scan: allow
    monkeypatch.setenv("SOME_UNRELATED_SETTING", UNRELATED)
    got = await P._secret_values_for(None)
    assert got == P._known_secret_values()
    assert UNRELATED not in got


async def test_unrelated_vault_values_are_not_secrets(vault_only_key):
    """Negative control: only manifest creds are collected, not arbitrary stored values."""
    async with vault_only_key.db.session() as s:
        await s.execute(sa.insert(providers_t).values(
            name="Other", kind="openai_compat", base_url="https://other.example/v1",
            api_key_enc=vault_only_key.vault.encrypt(UNRELATED)))
        await s.commit()
    got = await P._secret_values_for(vault_only_key)
    assert VAULT_KEY in got
    assert UNRELATED not in got
