"""Jeff closeout 10.10, gap 5: research resolves the host and refuses non-public addresses BEFORE fetching.

Fakes only: an injected resolver and an httpx MockTransport that records every request (no DNS, no network).
Resolver cases drafted by GLM-5.3 Flash, checked and extended here.
"""
from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import httpx
import pytest

from bcc.pit.j2 import research as rs


def run(coro):
    return asyncio.run(coro)


def resolver(mapping):
    calls: list[tuple[str, int]] = []

    async def resolve(host, port):
        calls.append((host, port))
        value = mapping[host]
        if isinstance(value, BaseException):
            raise value
        return list(value)
    resolve.calls = calls
    return resolve


@pytest.mark.parametrize("addresses", [
    ["127.0.0.1"], ["10.0.0.5"], ["192.168.1.10"], ["172.16.3.4"], ["169.254.169.254"], ["100.64.0.1"],
    ["0.0.0.0"], ["::1"], ["::ffff:127.0.0.1"], ["fe80::1%eth0"], ["fc00::1"], ["224.0.0.1"],
    ["2002:7f00:0001::1"],                       # 6to4 wrapping 127.0.0.1
    ["93.184.216.34", "10.0.0.1"],               # one private record is enough to refuse
    [],
])
def test_non_public_resolution_is_refused(addresses):
    assert run(rs.resolve_public("evil.example", 443, resolver=resolver({"evil.example": addresses}))) is False


def test_public_resolution_is_allowed():
    fake = resolver({"example.org": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]})
    assert run(rs.resolve_public("example.org", 443, resolver=fake)) is True
    assert fake.calls == [("example.org", 443)]


def test_resolver_error_is_refused():
    fake = resolver({"nx.example": socket.gaierror(8, "nodename nor servname")})
    assert run(rs.resolve_public("nx.example", 443, resolver=fake)) is False


def test_ip_literal_is_checked_without_resolving():
    async def boom(host, port):
        raise AssertionError("an IP literal must not be resolved")
    assert run(rs.resolve_public("8.8.8.8", 443, resolver=boom)) is True
    assert run(rs.resolve_public("192.168.1.1", 443, resolver=boom)) is False
    assert run(rs.resolve_public("[::1]", 443, resolver=boom)) is False


def test_guarded_target_uses_the_url_port():
    fake = resolver({"example.org": ["93.184.216.34"]})
    assert run(rs.guarded_target("http://example.org:8080/x", resolver=fake)) == "http://example.org:8080/x"
    assert fake.calls == [("example.org", 8080)]
    assert run(rs.guarded_target("https://example.org/", resolver=fake)) == "https://example.org/"
    assert fake.calls[-1] == ("example.org", 443)


def _runtime_with_recorder():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<html><body><p>Public page text.</p></body></html>")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return SimpleNamespace(models=SimpleNamespace(remote=client)), seen, client


def test_default_fetch_never_connects_to_a_name_that_resolves_private():
    runtime, seen, client = _runtime_with_recorder()
    fetch = rs._default_fetch(runtime, resolver=resolver({"rebind.example": ["127.0.0.1"],
                                                           "meta.example": ["169.254.169.254"]}))

    async def scenario():
        try:
            for url in ("https://rebind.example/admin", "http://meta.example/latest/meta-data/"):
                with pytest.raises(ValueError, match="blocked url"):
                    await fetch(url)
        finally:
            await client.aclose()
    run(scenario())
    assert seen == []                            # refused before any connection


def test_default_fetch_still_fetches_a_public_host():
    runtime, seen, client = _runtime_with_recorder()
    fetch = rs._default_fetch(runtime, resolver=resolver({"example.org": ["93.184.216.34"]}))

    async def scenario():
        try:
            return await fetch("https://example.org/page")
        finally:
            await client.aclose()
    body = run(scenario())
    assert "Public page text." in body
    assert seen == ["https://example.org/page"]


def test_research_desk_reports_a_blocked_fetch_as_failure_not_crash():
    runtime, seen, client = _runtime_with_recorder()
    fetch = rs._default_fetch(runtime, resolver=resolver({"intranet.example": ["10.1.2.3"]}))

    async def search(query):
        return [{"title": "Внутренний", "url": "https://intranet.example/doc", "snippet": "описание из выдачи"}]

    async def scenario():
        try:
            return await rs.ResearchDesk(search, fetch).research("что такое intranet example")
        finally:
            await client.aclose()
    report = run(scenario())
    assert seen == []
    assert any("не открылась" in f for f in report.uncertainty)
    assert not any(s.fetched for s in report.sources)
