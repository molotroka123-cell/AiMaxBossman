"""MP21-A: process-wide local-only switch (BOSSMAN_LOCAL_ONLY).

Before this switch the provider egress boundary only closed for a request that
carried `private`/`local_only` privacy; with no per-request mark every cloud
provider stayed reachable, so "offline mode" was a report, not a guarantee.

No network: the cloud adapter uses httpx.MockTransport and counts calls; the
loopback case must still go through.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from bcc.providers import AnthropicAdapter, OpenAICompatAdapter
from bossman_shared import privacy
from bossman_shared.privacy import assert_provider_egress, execution_privacy


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(privacy.LOCAL_ONLY_ENV, raising=False)


def test_switch_off_keeps_cloud_reachable():
    assert_provider_egress("openai_compat", "https://openrouter.ai/api/v1/chat/completions")
    assert privacy.local_only_switch() is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_switch_on_blocks_cloud_kinds_and_remote_hosts(monkeypatch, value):
    monkeypatch.setenv(privacy.LOCAL_ONLY_ENV, value)
    assert privacy.local_only_switch() is True
    for kind, url in (("openrouter", "https://openrouter.ai/api/v1"),
                      ("anthropic", "https://api.anthropic.com/v1/messages"),
                      ("openai_compat", "https://api.example.com/v1/chat/completions"),
                      ("openai_compat", "http://169.254.169.254/latest")):
        with pytest.raises(PermissionError, match="BOSSMAN_LOCAL_ONLY"):
            assert_provider_egress(kind, url)


def test_switch_on_allows_loopback_and_lan(monkeypatch):
    monkeypatch.setenv(privacy.LOCAL_ONLY_ENV, "1")
    assert_provider_egress("openai_compat", "http://127.0.0.1:11434/v1/chat/completions")
    assert_provider_egress("openai_compat", "http://localhost:8080/v1/models")
    assert_provider_egress("openai_compat", "http://192.168.1.20:8000/v1/models")


def test_unknown_value_does_not_enable(monkeypatch):
    monkeypatch.setenv(privacy.LOCAL_ONLY_ENV, "maybe")
    assert privacy.local_only_switch() is False


def test_switch_cannot_be_loosened_by_request_scope(monkeypatch):
    monkeypatch.setenv(privacy.LOCAL_ONLY_ENV, "1")
    with execution_privacy("public"):
        with pytest.raises(PermissionError):
            assert_provider_egress("anthropic", "https://api.anthropic.com/v1/messages")


def test_adapter_refuses_before_any_request_leaves(monkeypatch):
    monkeypatch.setenv(privacy.LOCAL_ONLY_ENV, "1")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"data": []})

    cloud = OpenAICompatAdapter(base_url="https://openrouter.ai/api/v1", api_key="x",
                                transport=httpx.MockTransport(handler))
    with pytest.raises(PermissionError):
        asyncio.run(cloud.health())
    anth = AnthropicAdapter(api_key="x", transport=httpx.MockTransport(handler))
    with pytest.raises(PermissionError):
        asyncio.run(anth.health())
    assert seen == []

    local = OpenAICompatAdapter(base_url="http://127.0.0.1:11434/v1", api_key="x",
                                transport=httpx.MockTransport(handler))
    asyncio.run(local.health())
    assert len(seen) == 1


def test_offline_report_and_provenance_name_the_switch(monkeypatch):
    from bcc import run_provenance
    from bcc.features import offline_mode
    assert offline_mode.local_only_enforced() is False
    monkeypatch.setenv(privacy.LOCAL_ONLY_ENV, "1")
    assert offline_mode.local_only_enforced() is True
    assert run_provenance._privacy_mode() == "OFFLINE"
