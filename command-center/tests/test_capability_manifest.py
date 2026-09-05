"""CapabilitySpec (TRUTH-003 §17): манифест способностей и правило выдачи.

Проверяется ровно то, ради чего он существует:
  1. список выводится из реестра инструментов (не второй источник правды);
  2. способность знает, ЧЕМ доказывается её эффект, и «доказать нечем» — видно;
  3. выдача = capability ∧ policy ∧ runtime, любое «нет» — отказ с причиной.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.capability import CapabilitySpec, from_tool, grant, manifest, runtime_missing
from bcc.db import settings_kv
from bcc.tools import ToolSpec
from bcc.v2.verification import KINDS


def _spec(name, **kw):
    kw.setdefault("description", "x")
    kw.setdefault("handler", lambda **a: {"ok": True})
    return ToolSpec(name=name, **kw)


def test_capability_is_derived_from_the_tool_not_declared_twice():
    t = _spec("terminal.run", category="exec", default_effect="ask", idempotent=False,
              source="terminal", permission="terminal")
    cap = from_tool(t)
    assert cap.capability_id == "terminal.run" and cap.tool == "terminal.run"
    assert cap.effect_class == "exec" and cap.side_effect
    assert cap.idempotency == "non_idempotent" and cap.approval_requirement == "ask"
    assert cap.permission == "terminal" and cap.privacy_requirement == "local_only"
    # доказательство — свежее чтение состояния терминала, а не код возврата
    assert cap.verification_strategy == "terminal" and cap.verification_strategy in KINDS
    assert cap.provable


def test_capability_without_a_verifier_is_marked_unprovable():
    """INV: TOOL_CALLED ≠ SIDE_EFFECT_VERIFIED. Инструмент с эффектом, но без
    наблюдателя пост-состояния, честно помечен `provable=False` — такой способностью
    нельзя закрыть шаг с side effect'ом."""
    cap = from_tool(_spec("mcp:vendor:send_invoice", category="send", source="mcp"))
    assert cap.side_effect and cap.verification_strategy == "" and not cap.provable
    assert cap.privacy_requirement == "any"


def test_denied_policy_blocks_the_grant_and_says_why():
    t = _spec("terminal.run", category="exec", default_effect="ask", permission="terminal")
    cap = from_tool(t)
    agent = {"permissions": ["terminal"]}
    rules = [{"tool": "terminal.run", "effect": "deny", "reason": "владелец запретил"}]
    g = grant(cap, spec=t, args={"command": "ls"}, agent=agent, policy_rules=rules,
              probes={"terminal_roots": True})
    assert not g.granted and g.capability_ok and not g.policy_ok and g.runtime_ok
    assert "политика" in g.reason


def test_missing_runtime_blocks_the_grant_even_when_policy_allows():
    t = _spec("browser.open", category="write", default_effect="auto", source="browser")
    cap = from_tool(t)
    g = grant(cap, spec=t, args={}, agent={"permissions": ["browser"]}, probes={"chromium": False})
    assert not g.granted and g.policy_ok and not g.runtime_ok and "chromium" in g.missing
    ok = grant(cap, spec=t, args={}, agent={"permissions": ["browser"]}, probes={"chromium": True})
    assert ok.granted and ok.runtime_ok


def test_unknown_probe_is_not_a_satisfied_precondition():
    """Неизмеренная предпосылка ≠ выполненная: без probes способность не выдаётся."""
    cap = from_tool(_spec("browser.open", category="write", source="browser"))
    assert runtime_missing(cap, probes=None) == ["chromium"]
    assert runtime_missing(cap, probes={}) == ["chromium"]


def test_unsupported_platform_is_a_runtime_refusal():
    cap = CapabilitySpec(capability_id="apps.open", tool="apps.open", description="",
                         effect_class="write", verification_strategy="app",
                         idempotency="idempotent", approval_requirement="ask", permission="",
                         privacy_requirement="local_only", supported_platforms=("darwin",))
    assert not cap.platform_supported("linux")
    assert runtime_missing(cap, platform="linux") == ["platform:linux"]


def test_unregistered_capability_fails_closed():
    g = grant(None, spec=None, args={}, agent={})
    assert not g.granted and not g.capability_ok and not g.policy_ok and not g.runtime_ok
    assert "не зарегистрирована" in g.reason


async def test_manifest_endpoint_lists_live_tools_with_grants(env):
    body = (await env.client.get("/api/capabilities")).json()
    caps = {c["capability_id"]: c for c in body["capabilities"]}
    from bcc.tools import REGISTRY as TOOLS
    assert caps and set(caps) == set(TOOLS.names())                 # ровно живой реестр
    assert set(body["probes"]) == {"chromium", "terminal_roots"}
    for c in caps.values():
        assert set(c["grant"]) >= {"granted", "capability_ok", "policy_ok", "runtime_ok"}
        assert c["provable"] == (c["verification_strategy"] in KINDS)
        if not c["grant"]["granted"]:
            assert c["grant"]["reason"], f"отказ без причины: {c['capability_id']}"
    assert set(body["provable_kinds"]) == set(KINDS)
    # манифест — не место для секретов
    assert "api_key" not in str(body).lower() and "token" not in str(body).lower()


async def test_terminal_roots_probe_measures_disk_not_settings(env):
    """Предпосылка меряется по факту: объявленный, но несуществующий корень
    способности не даёт."""
    import json
    before = (await env.client.get("/api/capabilities")).json()["probes"]["terminal_roots"]
    assert before is True                       # по умолчанию — data_dir, он существует
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(settings_kv).values(
            key="terminal.roots",
            value_enc=env.svc.vault.encrypt(json.dumps([str(env.settings.data_dir / "нет-такого")]))))
        await s.commit()
    after = (await env.client.get("/api/capabilities")).json()["probes"]["terminal_roots"]
    assert after is False


async def test_manifest_requires_authentication(env):
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=env.app), base_url="http://t") as anon:
        assert (await anon.get("/api/capabilities")).status_code == 401
