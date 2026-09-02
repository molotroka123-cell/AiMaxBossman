"""F-010 — навигация браузера: пустой allowlist больше не значит «куда угодно».

Модель угрозы: планировщик скомпрометирован и просит browser.open на
metadata-endpoint облака / loopback / внутреннюю сеть. По умолчанию такие цели
запрещены; владелец может осознанно включить их для локальной разработки
(BCC_BROWSER_ALLOW_PRIVATE=1).
"""
from __future__ import annotations

import socket

import pytest

from bcc.features import tools_browser
from bcc.tools import ToolContext
from bcc.v2.browser_control import (BrowserManager, BrowserPolicy, BrowserPolicyDenied,
                                    BrowserRuntimeSession)

PRIVATE_TARGETS = [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8800/api/agents",
    "http://localhost/",
    "http://10.0.0.1/",
    "http://172.16.5.5/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "http://[fc00::1]/",
    "http://0.0.0.0/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "file:///etc/passwd",
    "ftp://example.com/",
    "http://user:pw@example.com/",
]


@pytest.fixture(autouse=True)
def _no_owner_override(monkeypatch):
    monkeypatch.delenv("BCC_BROWSER_ALLOW_PRIVATE", raising=False)


def test_repro_default_policy_denies_private_and_metadata_targets():
    """REPRO F-010: allowed_domains пуст → раньше domain_allowed возвращал True."""
    pol = BrowserPolicy.from_dict({})
    for url in PRIVATE_TARGETS:
        assert pol.decision("navigate", url=url) == "deny", url
        assert not pol.domain_allowed(url), url


def test_public_http_stays_auto():
    pol = BrowserPolicy.from_dict({})
    assert pol.decision("navigate", url="http://example.com/") == "auto"
    assert pol.decision("navigate", url="https://docs.python.org/3/") == "auto"


def test_explicit_allowlist_keeps_suffix_matching_but_never_private():
    pol = BrowserPolicy.from_dict({"allowed_domains": ["example.com", "127.0.0.1"]})
    assert pol.decision("navigate", url="https://sub.example.com/x") == "auto"
    assert pol.decision("navigate", url="https://example.com/") == "auto"
    assert pol.decision("navigate", url="https://other.com/") == "deny"
    # даже явно перечисленный loopback не проходит без BCC_BROWSER_ALLOW_PRIVATE
    assert pol.decision("navigate", url="http://127.0.0.1/") == "deny"


def test_owner_override_allows_private_for_local_dev(monkeypatch):
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")
    pol = BrowserPolicy.from_dict({})
    assert pol.decision("navigate", url="http://127.0.0.1:8000/") == "auto"
    assert pol.decision("navigate", url="http://169.254.169.254/") == "auto"
    # схемы вне http(s) не открываются и с переопределением
    assert pol.decision("navigate", url="file:///etc/passwd") == "deny"


def test_variant_numeric_host_forms_resolve_to_loopback_and_are_refused():
    """Вариант: «2130706433» и «0x7f000001» не выглядят как IP, но резолвятся в 127.0.0.1."""
    pol = BrowserPolicy.from_dict({})
    for url in ("http://2130706433/", "http://0x7f000001/", "http://127.1/"):
        assert pol.navigation_refusal(url), url


def test_variant_hostname_resolving_to_private_is_refused(monkeypatch):
    def fake_getaddrinfo(host, *a, **kw):
        if host == "internal.corp":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.16.0.1", 0)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]
        if host == "public.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.16.0.1", 0))]
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    pol = BrowserPolicy.from_dict({})
    # ВСЕ адреса проверяются: одна приватная A-запись — отказ (анти-rebinding)
    assert pol.navigation_refusal("http://internal.corp/")
    assert pol.navigation_refusal("http://public.example/") == ""
    assert pol.navigation_refusal("http://nxdomain.invalid/")


# ------------------------------------------------ рантайм: goto не вызывается

class _Page:
    def __init__(self):
        self.url = "about:blank"
        self.visited: list[str] = []

    async def goto(self, url, **kw):
        self.visited.append(url)
        self.url = url

    async def title(self):
        return "t"


class _Ctx:
    pages = [None]


def _fake_manager(tmp_path, policy: BrowserPolicy, sid: int = 7) -> tuple[BrowserManager, _Page]:
    mgr = BrowserManager(tmp_path / "browser")
    page = _Page()
    mgr._sessions[sid] = BrowserRuntimeSession(id=sid, policy=policy, context=_Ctx(),
                                               page=page)

    async def snapshot(session_id, **kw):
        return {"session_id": session_id, "url": page.url, "title": "t", "text": "",
                "interactive": []}
    mgr.snapshot = snapshot
    return mgr, page


async def test_runtime_navigate_to_blocked_target_does_not_touch_page(tmp_path):
    mgr, page = _fake_manager(tmp_path, BrowserPolicy.from_dict({}))
    with pytest.raises(BrowserPolicyDenied):
        await mgr.navigate(7, "http://169.254.169.254/", actor="agent", approved=True)
    with pytest.raises(BrowserPolicyDenied):
        await mgr.navigate(7, "http://2130706433/", actor="agent", approved=True)
    assert page.visited == []


async def test_browser_open_tool_returns_error_and_does_not_navigate(env, tmp_path, monkeypatch):
    """Инструмент модели: error=True, страница не тронута — даже при approved=True."""
    mgr, page = _fake_manager(tmp_path, BrowserPolicy.from_dict({}))
    monkeypatch.setattr(tools_browser, "_mgr", lambda svc: mgr)

    async def fixed_session(ctx, args):
        return 7
    monkeypatch.setattr(tools_browser, "_session_for", fixed_session)
    ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={})
    res = await tools_browser._open({"url": "http://127.0.0.1:8800/api/settings"}, ctx)
    assert res.error is True and "политик" in res.content
    assert page.visited == []
