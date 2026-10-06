"""validate_url: синтаксически битый URL — это отказ политики, а не сырой ValueError.

Дефект (zone plugins, 2026-10-06): контракт `validate_url` — «бросает
PluginSecurityError при отказе», и все вызывающие (plugin:http.get,
plugin:monitor.feed, telegram egress hook) ловят именно его. `urlparse` на
`http://[::1` бросает голый ValueError, а неверный порт (`:99999`, `:abc`)
проходил проверку и падал уже внутри httpx. В итоге plugin:http.get отвечал
исключением вместо `blocked: …`.
"""
from __future__ import annotations

import pytest

import bcc.features.plugins as P
from bcc.plugin_security import PluginSecurityError, safe_get, validate_url
from bcc.tools import REGISTRY

BAD = ["http://[::1", "http://[not-an-ip]/", "http://example.com:99999/",
       "http://example.com:abc/"]


@pytest.mark.parametrize("url", BAD)
def test_validate_url_malformed_is_policy_error(url):
    with pytest.raises(PluginSecurityError):
        validate_url(url)


@pytest.mark.parametrize("url", BAD)
async def test_safe_get_malformed_is_policy_error(url):
    with pytest.raises(PluginSecurityError):
        await safe_get(url)


@pytest.mark.parametrize("url", BAD)
async def test_http_get_handler_reports_blocked(url):
    await P.setup(None)
    ctx = type("C", (), {"svc": None})()
    res = await REGISTRY.get("plugin:http.get").handler({"url": url}, ctx)
    assert res.error and res.content.startswith("blocked:")


def test_valid_public_url_with_port_still_passes():
    assert validate_url("https://93.184.216.34:8443/x")[1] == "93.184.216.34"
