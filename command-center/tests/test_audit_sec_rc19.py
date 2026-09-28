"""RC19 security audit — регрессии найденных дыр API/UI.

Каждый тест падал до исправления и описывает конкретный путь атаки или сбоя.
"""
from __future__ import annotations

import pytest


# ------------------------------------------------ P1-1: WS и чужой Origin

def _login(client, svc):
    assert client.post("/api/login", json={"token": svc.auth.token}).status_code == 200


def test_ws_refuses_cookie_from_another_local_origin(env):
    """Страница на другом порту того же хоста (SameSite порт не различает)
    несёт cookie владельца; ленту событий она читать не должна."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    with TestClient(env.app) as client:
        _login(client, env.svc)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                    "/api/events", headers={"Origin": "http://testserver:5173"}) as ws:
                ws.receive_json()
        assert exc.value.code == 4403
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/events", headers={"Origin": "null"}) as ws:
                ws.receive_json()


def test_ws_accepts_same_origin_and_non_browser_clients(env):
    """Собственный UI (тот же host:port) и CLI без Origin продолжают работать."""
    from fastapi.testclient import TestClient
    with TestClient(env.app) as client:
        _login(client, env.svc)
        with client.websocket_connect(
                "/api/events", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json()["kind"] == "hello"
        with client.websocket_connect("/api/events") as ws:
            assert ws.receive_json()["kind"] == "hello"


# ------------------------------------------------ P2-1: DNS rebinding (Host)

async def test_rebinding_host_is_refused_before_any_endpoint(env):
    """Сайт attacker.example, перепривязанный на 127.0.0.1, для браузера —
    тот же origin; без проверки Host он читал путь к токену и жёг лимит входа
    владельца (lockout по IP 127.0.0.1)."""
    import httpx
    transport = httpx.ASGITransport(app=env.app, client=("127.0.0.1", 5555))
    async with httpx.AsyncClient(transport=transport, base_url="http://attacker.example:8800") as c:
        hint = await c.get("/api/login-hint")
        assert hint.status_code == 421 and "token_file" not in hint.text
        for _ in range(25):
            assert (await c.post("/api/login", json={"token": "x"})).status_code == 421
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8800") as c:
        assert (await c.get("/api/login-hint")).json()["token_file"]
        assert (await c.post("/api/login", json={"token": env.svc.auth.token})).status_code == 200


@pytest.mark.parametrize("host", [
    "127.0.0.1:8800", "localhost:8800", "[::1]:8800", "100.101.102.103:8800",
    "192.168.1.20", "bossman:8800", "desk.local", "pc.tail1234.ts.net", "app.localhost",
])
def test_addresses_and_non_public_names_pass(host):
    from bcc.api import host_allowed
    assert host_allowed(host)


@pytest.mark.parametrize("host", ["attacker.example", "evil.com:8800", "127.0.0.1.nip.io:8800"])
def test_public_dns_names_fail_unless_configured(host):
    from bcc.api import host_allowed
    assert not host_allowed(host)
    name = host.split(":")[0]
    assert host_allowed(host, frozenset({name}))
    assert host_allowed(host, frozenset({"*." + name.split(".", 1)[1]}))


def test_allowed_hosts_env_and_bind_address_are_honoured(tmp_path, monkeypatch):
    from bcc.api import _configured_hosts
    from .conftest import make_settings
    monkeypatch.setenv("BCC_ALLOWED_HOSTS", " Bossman.Example.org , *.corp.test ")
    settings = make_settings(tmp_path)
    settings.host = "my-pc.lan"
    assert _configured_hosts(settings) >= {"bossman.example.org", "*.corp.test", "my-pc.lan"}


def test_ws_with_rebinding_host_is_closed(env):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    with TestClient(env.app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("ws://attacker.example/api/events") as ws:
                ws.receive_json()
        assert exc.value.code == 4421


# ------------------------------------------------ P1-2: аренда после отзыва решения

async def _approved_lease(env, *, uses=50):
    from bcc import approval_scope as scope
    from .helpers import make_stack
    stack = await make_stack(env.client)
    tid, aid = stack["task"]["id"], stack["agent"]["id"]
    appr = await env.svc.approvals.create(kind="tool", preview="p", task_id=tid)
    await env.svc.approvals.decide(appr["id"], True, "owner")
    sc = scope.Scope(tool="terminal.run", effect_class=scope.WRITE, scope_key="sandbox",
                     agent_id=aid, task_id=tid)
    lease = await scope.grant(env.svc, approval=appr, scope=sc, max_uses=uses, ttl_seconds=3600)
    return appr, sc, lease


async def test_revoking_the_approval_revokes_the_lease_it_granted(env):
    from bcc import approval_scope as scope
    appr, sc, lease = await _approved_lease(env)
    res = await env.client.post(f"/api/approvals/{appr['id']}/revoke", json={})
    assert res.status_code == 200 and res.json()["status"] == "revoked"
    assert await scope.consume(env.svc, sc) is None
    rows = await scope.listing(env.svc, task_id=sc.task_id, active_only=False)
    assert [r["status"] for r in rows if r["id"] == lease["id"]] == ["revoked"]


async def test_a_lease_whose_approval_is_no_longer_live_is_not_spendable(env):
    """Защита в глубину: даже непогашенная строка аренды не работает, если
    её одобрение отозвано/отклонено в обход Approvals.revoke."""
    import sqlalchemy as sa
    from bcc import approval_scope as scope
    from bcc.db import approvals as approvals_t
    appr, sc, _ = await _approved_lease(env)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(approvals_t).where(approvals_t.c.id == appr["id"])
                        .values(status="consumed"))
        await s.commit()
    assert await scope.consume(env.svc, sc) is not None      # consumed — нормальная жизнь
    async with env.svc.db.session() as s:
        await s.execute(sa.update(approvals_t).where(approvals_t.c.id == appr["id"])
                        .values(status="revoked"))
        await s.commit()
    assert await scope.consume(env.svc, sc) is None


async def test_stopping_a_task_revokes_its_leases(env):
    from bcc import approval_scope as scope
    _appr, sc, _ = await _approved_lease(env)
    await env.client.post(f"/api/tasks/{sc.task_id}/stop")
    assert await scope.consume(env.svc, sc) is None
    assert await scope.listing(env.svc, task_id=sc.task_id) == []


# ------------------------------------------------ P1-3: ресурс политики и лишний ключ

def _spec(name="web.open"):
    from bcc.tools import ToolSpec

    async def handler(args, ctx):
        return None
    return ToolSpec(name=name, description="", handler=handler, default_effect="auto")


@pytest.mark.parametrize("decoy", ["cmd", "command"])
def test_a_decoy_argument_does_not_dodge_an_owner_deny(decoy):
    """Модель добавляла `cmd: ok` к web.open — ресурсом становилось «ok», и
    DENY владельца на *secret* молча не срабатывал."""
    from bcc.tools import decide_effect
    rules = [{"tool": "*", "resource": "*secret*", "effect": "deny"}]
    assert decide_effect(_spec(), {"url": "https://x/secret"}, {}, rules)[0] == "deny"
    assert decide_effect(_spec(), {"url": "https://x/secret", decoy: "ok"}, {}, rules)[0] == "deny"


def test_a_decoy_argument_does_not_borrow_an_owner_auto():
    """Ослабляющее правило требует, чтобы под него попал КАЖДЫЙ ресурс вызова."""
    from bcc.tools import decide_effect
    spec = _spec("browser.open")
    spec.default_effect = "ask"
    rules = [{"tool": "browser.open", "resource": "https://docs.example/*", "effect": "auto"}]
    assert decide_effect(spec, {"url": "https://docs.example/a"}, {}, rules)[0] == "auto"
    assert decide_effect(spec, {"url": "https://evil.example/", "cmd": "https://docs.example/x"},
                         {}, rules)[0] == "ask"


def test_an_ask_rule_tightens_on_any_resource():
    from bcc.tools import decide_effect
    rules = [{"tool": "*", "resource": "*wallet*", "effect": "ask"}]
    assert decide_effect(_spec(), {"url": "https://x/wallet", "name": "ok"}, {}, rules)[0] == "ask"
