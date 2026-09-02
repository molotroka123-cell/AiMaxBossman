"""SECREM F-015 — владелец-маршруты не верят самоутверждённым флагам.

REPRO (Fable 5.1): POST /api/terminal/run и /api/browser/sessions/{id}/act
принимали `approved: true` из тела запроса как подтверждение (и любой `actor`).
Теперь подтверждение — запись approvals(kind, preview) со статусом approved,
предъявляемая как approval_id и потребляемая один раз; preview детерминирован
(команда+cwd+режим / действие+цель), так что одобрение одного действия нельзя
переиспользовать для другого. Заодно HTTP-маршрут терминала перестал брать
[cwd] как корень для sandbox (F-009).
"""
from __future__ import annotations

import pytest

from bcc.features import browser as browser_feat
from bcc.v2.browser_control import BrowserApprovalRequired


def _aid(payload: dict) -> int:
    """HTTPException(202, {...}) может прийти как {"detail": {...}} или плоско."""
    inner = payload.get("error") or payload.get("detail") or payload
    return int(inner["approval_id"])


async def _approve(env, aid: int) -> None:
    r = await env.client.post(f"/api/approvals/{aid}", json={"approve": True, "by": "тест"})
    assert r.status_code == 200, r.text


async def test_repro_terminal_self_asserted_approved_is_refused(env):
    d = env.settings.data_dir
    d.mkdir(parents=True, exist_ok=True)
    await env.client.post("/api/terminal/roots", json={"roots": [str(d)]})
    body = {"command": "echo hi", "mode": "project_host", "cwd": str(d)}
    r = await env.client.post("/api/terminal/run", json={**body, "approved": True})
    assert r.status_code == 403, r.text
    assert "approval_id" in r.text
    # без флага — честный 202 с заведённым approval
    r = await env.client.post("/api/terminal/run", json=body)
    assert r.status_code == 202
    assert (await env.client.get("/api/approvals")).json()[0]["kind"] == "terminal"


async def test_variant_approval_bound_to_preview_and_single_use(env):
    d = env.settings.data_dir
    d.mkdir(parents=True, exist_ok=True)
    await env.client.post("/api/terminal/roots", json={"roots": [str(d)]})
    body = {"command": "echo one", "mode": "project_host", "cwd": str(d)}
    aid = _aid((await env.client.post("/api/terminal/run", json=body)).json())
    await _approve(env, aid)
    # одобрение для «echo one» не подходит к «echo two» (другой preview) → новый approval
    r = await env.client.post("/api/terminal/run",
                              json={**body, "command": "echo two", "approval_id": aid})
    assert r.status_code == 202, r.text
    # и не подходит к другому cwd/режиму
    r = await env.client.post("/api/terminal/run",
                              json={**body, "mode": "system_admin", "approval_id": aid})
    assert r.status_code == 202
    # правильный preview — выполняется
    r = await env.client.post("/api/terminal/run", json={**body, "approval_id": aid})
    assert r.status_code == 200, r.text
    # повторное предъявление того же approval_id — потреблён → снова 202
    r = await env.client.post("/api/terminal/run", json={**body, "approval_id": aid})
    assert r.status_code == 202
    rows = (await env.client.get("/api/approvals?status=consumed")).json()
    assert any(a["id"] == aid for a in rows)


async def test_variant_http_sandbox_no_longer_treats_cwd_as_root(env, tmp_path):
    """F-009 в HTTP-маршруте: sandbox с cwd вне корней → 403 (раньше [cwd] был корнем)."""
    d = env.settings.data_dir
    d.mkdir(parents=True, exist_ok=True)
    await env.client.post("/api/terminal/roots", json={"roots": [str(d)]})
    outside = tmp_path.parent / "secrem_http_outside"
    outside.mkdir(exist_ok=True)
    r = await env.client.post("/api/terminal/run",
                              json={"command": "ls", "mode": "sandbox", "cwd": str(outside)})
    assert r.status_code == 403, r.text
    p = (await env.client.post("/api/terminal/preview",
                               json={"command": "ls", "mode": "sandbox", "cwd": str(outside)})).json()
    assert p["decision"] == "deny"


# ------------------------------------------------------------ browser /act

class _FakeMgr:
    def __init__(self):
        self.calls = []

    async def navigate(self, sid, url, *, actor="agent", approved=False):
        if not approved and actor != "human":
            raise BrowserApprovalRequired("navigate")
        self.calls.append((sid, url, actor, approved))
        return {"url": url, "title": "t"}


@pytest.fixture
def fake_mgr(monkeypatch):
    m = _FakeMgr()
    monkeypatch.setattr(browser_feat, "_mgr", lambda svc: m)

    async def rec(*a, **kw):
        return None
    monkeypatch.setattr(browser_feat, "_record", rec)
    return m


async def test_repro_browser_self_asserted_approved_is_refused(env, fake_mgr):
    body = {"action": "navigate", "url": "https://example.com/"}
    r = await env.client.post("/api/browser/sessions/1/act", json={**body, "approved": True})
    assert r.status_code == 403 and fake_mgr.calls == []
    r = await env.client.post("/api/browser/sessions/1/act", json=body)
    assert r.status_code == 202 and fake_mgr.calls == []
    aid = _aid(r.json())
    await _approve(env, aid)
    # одобрение для example.com не годится для другого URL
    r = await env.client.post("/api/browser/sessions/1/act",
                              json={"action": "navigate", "url": "https://evil.example/", "approval_id": aid})
    assert r.status_code == 202 and fake_mgr.calls == []
    r = await env.client.post("/api/browser/sessions/1/act", json={**body, "approval_id": aid})
    assert r.status_code == 200 and fake_mgr.calls[-1][3] is True
    r = await env.client.post("/api/browser/sessions/1/act", json={**body, "approval_id": aid})
    assert r.status_code == 202                                   # одноразовое


async def test_variant_actor_is_restricted(env, fake_mgr):
    r = await env.client.post("/api/browser/sessions/1/act",
                              json={"action": "navigate", "url": "https://example.com/", "actor": "root"})
    assert r.status_code == 422
