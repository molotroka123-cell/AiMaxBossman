"""Secret scrub must cover every env name the plugin actually reads a key from.

Reproduced 2026-10-08 on this checkout: with only the legacy
BOSSMAN_OPENROUTER_API_KEY set, `_cred("OPENROUTER_API_KEY")` returned that key
(the alias is honoured), but `_known_secret_values()` looked only at the canonical
name, so a page echoing the key came back from http.get unredacted.
"""
from __future__ import annotations

import httpx
import pytest

import bcc.features.plugins as P
from bcc.tools import REGISTRY, ToolContext, execute_tool

LEGACY_KEY = "sk-or-v1-legacyALIASvalue0123456789abcdef"


@pytest.fixture
def only_legacy_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("BOSSMAN_OPENROUTER_API_KEY", LEGACY_KEY)
    assert P._cred("OPENROUTER_API_KEY") == LEGACY_KEY      # the plugin really uses it


def test_legacy_alias_value_is_a_known_secret(only_legacy_key):
    assert LEGACY_KEY in P._known_secret_values()


def test_unrelated_env_values_are_not_treated_as_secrets(monkeypatch, only_legacy_key):
    """Negative control: widening to aliases must not sweep in arbitrary env vars."""
    monkeypatch.setenv("SOME_UNRELATED_SETTING", "plain-public-value-42")
    assert "plain-public-value-42" not in P._known_secret_values()


async def test_http_get_does_not_echo_the_legacy_key(only_legacy_key, monkeypatch):
    async def fake_safe_get(url, **kw):
        return httpx.Response(200, content=f"leaked: {LEGACY_KEY} end".encode())
    monkeypatch.setattr(P, "safe_get", fake_safe_get)
    await P.setup(None)
    ctx = ToolContext(svc=None, task={}, run_id=1, agent={}, workspace="", call_id="redact")
    r = await execute_tool(REGISTRY.get("plugin:http.get"), {"url": "https://example.com/"}, ctx)
    assert not r.error, r.content
    assert LEGACY_KEY not in r.content
    assert "leaked:" in r.content and "end" in r.content      # the rest of the page survives
