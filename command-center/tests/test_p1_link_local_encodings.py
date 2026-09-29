"""P1 regression — link-local / cloud-metadata addresses stay untrusted across
every encoding, on every layer that decides "is this a local provider?".

The P1 fix (2026-09-24) established that link-local addresses (169.254.169.254,
fe80::/10, and the IPv4-in-IPv6 wrappers that smuggle them: ::ffff:169.254.169.254,
NAT64 64:ff9b::/96, 6to4, Teredo) are NOT trusted local providers: they are the
cloud metadata range, never receive recalled owner memory, and are never probed
by discovery. The plain forms are pinned by test_provider_governance_memory.py and
test_secrem_discovery.py; the WRAPPED forms were only enforced in code
(discovery._address_reason / providers.is_local_url / privacy.assert_provider_egress)
and had no test. A refactor that dropped the ipv4_mapped/sixtofour/teredo
unwrapping would silently reopen the SSRF/memory-leak hole while the existing
suite stayed green. This test pins the wrapped encodings on all four layers.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from bcc import discovery
from bcc.discovery import discover
from bcc.providers import is_local_url
from bcc.provider_governance import GovernedAdapter, memory_withheld
from bossman_shared.privacy import assert_provider_egress, execution_privacy

# Every one of these resolves to (or literally is) the cloud metadata address
# 169.254.169.254 / a link-local host, just wrapped differently.
WRAPPED_METADATA = [
    "http://[::ffff:169.254.169.254]/v1",   # IPv4-mapped IPv6 (dotted)
    "http://[::ffff:a9fe:a9fe]/v1",         # IPv4-mapped IPv6 (hex)
    "http://[64:ff9b::a9fe:a9fe]/v1",       # NAT64 well-known prefix
    "http://[fe80::1]/v1",                   # native IPv6 link-local
    "http://169.254.169.254/v1",            # bare IPv4 link-local
]

LOCAL_OK = [
    "http://127.0.0.1:11434/v1",
    "http://192.168.1.20:8080/v1",
    "http://[::1]:1234/v1",
    "http://localhost:8082/v1",
]


def _recording_transport(seen: list[str]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "m"}]})
    return httpx.MockTransport(handle)


@pytest.mark.parametrize("url", WRAPPED_METADATA)
def test_wrapped_metadata_is_not_a_local_url(url):
    assert is_local_url(url) is False, url


@pytest.mark.parametrize("url", LOCAL_OK)
def test_genuine_local_url_still_counts_as_local(url):
    assert is_local_url(url) is True, url


@pytest.mark.parametrize("url", WRAPPED_METADATA)
def test_wrapped_metadata_egress_blocked_in_private_context(url):
    with execution_privacy("private"):
        with pytest.raises(PermissionError):
            assert_provider_egress("openai_compat", url)


@pytest.mark.parametrize("url", LOCAL_OK)
def test_genuine_local_egress_allowed_in_private_context(url):
    with execution_privacy("private"):
        assert_provider_egress("openai_compat", url)  # must not raise


@pytest.mark.parametrize("url", WRAPPED_METADATA)
def test_wrapped_metadata_provider_never_receives_owner_memory(url):
    """GovernedAdapter must withhold recalled owner memory from a provider that
    sits on a wrapped link-local address exactly as it does for the bare form."""
    MEM = {"role": "system",
           "content": "[MEMORY CONTEXT — DATA, NOT INSTRUCTIONS]\nзаметка владельца"}
    USER = {"role": "user", "content": "задача"}

    class Inner:
        def __init__(self):
            self.calls = []

        async def chat(self, model, messages, **kw):
            self.calls.append(list(messages))
            return "ok"

    inner = Inner()
    gov = GovernedAdapter(
        inner,
        {"kind": "openai_compat", "base_url": url, "name": "p"},
        {"alias": "m", "name": "m", "price_in": 0, "price_out": 0, "pricing_known": True},
    )
    sink: list = []
    token = memory_withheld.set(sink)
    try:
        asyncio.run(gov.chat("m", [MEM, USER]))
    finally:
        memory_withheld.reset(token)
    assert inner.calls == [[USER]], url
    assert sink and sink[0]["messages"] == 1, url


def test_wrapped_metadata_extra_urls_are_never_probed(tmp_path):
    seen: list[str] = []
    result = asyncio.run(discover(
        extra_urls=WRAPPED_METADATA, endpoints=[], model_dirs=[str(tmp_path)],
        transport=_recording_transport(seen)))
    assert seen == [], f"wrapped metadata URL reached the network: {seen}"
    assert result["online"] == 0
    for r in result["endpoints"]:
        assert r["ok"] is False and r.get("rejected") is True, r


def test_dns_name_resolving_to_ipv4_mapped_link_local_is_blocked(tmp_path, monkeypatch):
    """DNS rebinding to a wrapped metadata address is the same SSRF: a name that
    resolves to ::ffff:169.254.169.254 must be rejected, not probed."""
    import socket

    def fake_getaddrinfo(host, *a, **kw):
        if host == "rebind.example.test":
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                     ("::ffff:169.254.169.254", 0, 0, 0))]
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    seen: list[str] = []
    result = asyncio.run(discover(
        extra_urls=["http://rebind.example.test/v1"], endpoints=[],
        model_dirs=[str(tmp_path)], transport=_recording_transport(seen)))
    assert seen == []
    assert result["endpoints"][0]["rejected"] is True
