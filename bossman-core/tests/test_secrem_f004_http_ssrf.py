"""SECREM F-004 — инструмент `http`: egress-политика против SSRF.

REPRO (Fable 5.1): `http` брал args["url"] как есть и ходил по нему —
loopback/RFC1918/metadata/file:// — и следовал редиректам. Теперь: схема,
хост, резолв, каждый редирект — через check_url; отказ = данные для модели,
в сеть не ходим (MockTransport фиксирует, что запросов не было).
"""
from __future__ import annotations

import socket

import httpx
import pytest

from bossman.toolkit import REGISTRY, ToolContext
from bossman.toolkit import net

BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://127.0.0.1:8765/v1/models",
    "http://localhost:8800/api/agents",
    "http://[::1]/",
    "http://10.0.0.1/", "http://172.16.1.1/", "http://192.168.0.1/",
    "http://0.0.0.0/",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "http://user:pw@example.com/",
    "http://[fc00::1]/",
    "http://100.100.100.200/latest/meta-data/",
]


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(agent="test", workdir=tmp_path)


@pytest.fixture
def transport(monkeypatch):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        loc = request.url.params.get("redir")
        if loc:
            return httpx.Response(302, headers={"location": loc})
        return httpx.Response(200, json={"ok": True, "url": str(request.url)})
    monkeypatch.setattr(net, "_TRANSPORT", httpx.MockTransport(handle))
    return seen


@pytest.fixture
def dns(monkeypatch):
    table = {"public.example": ["104.16.0.1"], "internal.corp": ["104.16.0.1", "10.1.2.3"],
             "meta.example": ["169.254.169.254"]}

    def fake(host, *a, **kw):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]]
        raise socket.gaierror("nxdomain")
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    monkeypatch.delenv("BOSSMAN_HTTP_ALLOW_HOSTS", raising=False)
    monkeypatch.delenv("BOSSMAN_HTTP_ALLOW_PRIVATE", raising=False)


@pytest.mark.parametrize("url", BLOCKED)
async def test_repro_blocked_targets_never_hit_network(ctx, transport, dns, url):
    res = await net.http({"url": url}, ctx)
    assert res.error is True and "egress" in res.content, (url, res.content)
    assert transport == [], f"запрос ушёл в сеть: {url}"


async def test_public_host_is_fetched(ctx, transport, dns):
    res = await net.http({"url": "http://public.example/api", "fields": ["ok"]}, ctx)
    assert res.error is False and len(transport) == 1
    assert "статус: 200" in res.content


def test_variant_hostname_with_one_private_record_is_refused(dns):
    with pytest.raises(net.EgressDenied):
        net.check_url("http://internal.corp/")
    with pytest.raises(net.EgressDenied):
        net.check_url("http://meta.example/")
    with pytest.raises(net.EgressDenied, match="не резолвится"):
        net.check_url("http://nxdomain.invalid/")
    assert net.check_url("http://public.example/x") == "http://public.example/x"


async def test_variant_redirect_to_private_is_refused_and_hops_bounded(ctx, transport, dns):
    res = await net.http({"url": "http://public.example/a?redir=http://127.0.0.1:8765/v1"}, ctx)
    assert res.error is True and "egress" in res.content
    assert len(transport) == 1                     # первый хоп ушёл, второй — нет
    transport.clear()
    # бесконечный публичный редирект — обрывается на MAX_REDIRECTS
    url = "http://public.example/loop?redir=http://public.example/loop%3Fredir%3Dhttp%3A%2F%2Fpublic.example%2Floop"
    res = await net.http({"url": "http://public.example/loop?redir=" + url}, ctx)
    assert len(transport) <= net.MAX_REDIRECTS + 1


def test_owner_allowlist_and_private_override(monkeypatch, dns):
    monkeypatch.setenv("BOSSMAN_HTTP_ALLOW_HOSTS", "internal.corp,.svc.local")
    assert net.check_url("http://internal.corp/") == "http://internal.corp/"
    assert net.check_url("http://api.svc.local/") == "http://api.svc.local/"
    with pytest.raises(net.EgressDenied):
        net.check_url("http://169.254.169.254/")       # metadata не входит в allowlist
    monkeypatch.setenv("BOSSMAN_HTTP_ALLOW_PRIVATE", "1")
    assert net.check_url("http://10.9.9.9/") == "http://10.9.9.9/"
    with pytest.raises(net.EgressDenied, match="metadata"):
        net.check_url("http://169.254.169.254/")       # metadata закрыт и при ALLOW_PRIVATE


def test_http_tool_requires_confirmation_by_default():
    assert REGISTRY["http"].confirm_default is True
