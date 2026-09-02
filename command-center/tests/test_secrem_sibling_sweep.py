"""SECREM sibling sweep (F8.4): один набор контрпримеров — против ВСЕХ компонентов
одной границы. Появился новый компонент с URL/путём/внешним текстом — он
добавляется сюда, а не получает собственную неполную таблицу.

Границы command-center:
  egress: browser (target_refusal), discovery (_reject_reason), plugins (validate_url)
  path:   task_exchange.is_safe_segment, terminal cwd (через F-009 тесты)
  text:   MCP sanitize_text/untrusted_description
"""
from __future__ import annotations

import pytest

from bcc import discovery
from bcc.features.task_exchange import is_safe_segment
from bcc.features.tools_mcp import sanitize_text, untrusted_description
from bcc.plugin_security import PluginSecurityError, validate_url
from bcc.v2.browser_control import target_refusal

from ._secrem.mutators import (CONTROL_CHARS, EGRESS_ALWAYS_BLOCKED, EGRESS_PRIVATE,
                               EGRESS_PUBLIC_CONTROL, INJECTION_STRINGS, PATH_TRAVERSAL_SEGMENTS)


@pytest.fixture(autouse=True)
def _no_owner_overrides(monkeypatch):
    monkeypatch.delenv("BCC_BROWSER_ALLOW_PRIVATE", raising=False)


# ------------------------------------------------------------ egress: always blocked

@pytest.mark.parametrize("url", EGRESS_ALWAYS_BLOCKED)
async def test_always_blocked_targets_refused_by_every_egress_component(url):
    assert target_refusal(url), f"browser accepted {url}"
    assert await discovery._reject_reason(url), f"discovery accepted {url}"
    with pytest.raises(PluginSecurityError):
        validate_url(url)


@pytest.mark.parametrize("url", EGRESS_PRIVATE)
def test_private_targets_refused_by_browser_and_plugins_by_default(url):
    assert target_refusal(url), f"browser accepted private {url}"
    with pytest.raises(PluginSecurityError):
        validate_url(url)


@pytest.mark.parametrize("url", EGRESS_PUBLIC_CONTROL)
async def test_public_control_targets_pass_literal_checks(url):
    """Контроль от over-blocking: публичные цели проходят литеральные проверки
    (DNS здесь не резолвится — только синтаксис/литералы)."""
    assert target_refusal(url) == ""
    validate_url(url)


# ------------------------------------------------------------ paths

@pytest.mark.parametrize("seg", PATH_TRAVERSAL_SEGMENTS)
def test_traversal_segments_are_not_safe(seg):
    assert not is_safe_segment(seg), repr(seg)


def test_safe_segment_control():
    assert is_safe_segment("good-app") and is_safe_segment("t1.json") and is_safe_segment("A_b-9")


# ------------------------------------------------------------ untrusted text

@pytest.mark.parametrize("text", INJECTION_STRINGS)
def test_injection_strings_are_sanitized_and_framed(text):
    clean = sanitize_text(text, 600)
    assert not any(c in clean for c in CONTROL_CHARS)
    desc = untrusted_description("srv", "tool", text)
    assert desc.startswith("[MCP-сервер srv")
    assert "данные, не инструкции" in desc
