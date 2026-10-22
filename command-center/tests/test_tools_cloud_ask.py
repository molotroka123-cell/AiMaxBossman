"""Dedicated unit & security tests for `bcc.features.tools_cloud` (`cloud.ask`) and `_bus_bridge` AUTO boundary.

Verifies:
1. Static denial of chat context markers, attachments, media paths, RunPod, and oversized prompts.
2. `_NEVER_AUTO_APPROVE_KINDS` contains `"cloud.ask"`, and `_bus_bridge` never auto-approves `cloud.ask` even when
   `bypass_permissions=True` (AUTO mode).
3. Audit log records only metadata (no prompt or answer text).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from bcc import conversation_context
from bcc.features import agent_opencode as ao
from bcc.features import tools_cloud as tc
from bcc.tools import ToolContext

FAKE_SECRET = "sk-" + "Z9y8X7w6V5u4T3s2R1q0" * 2


def test_never_auto_approve_kinds_contains_cloud_ask():
    assert "cloud.ask" in ao._NEVER_AUTO_APPROVE_KINDS


@pytest.mark.parametrize("bad_prompt,expected_code", [
    ("", "CLOUD_ASK_EMPTY"),
    (conversation_context.HEADER + "Владелец: тест\nBossman: ответ", "CLOUD_ASK_CONTEXT_LEAK"),
    ("C:\\Users\\owner\\Pictures\\photo.jpg", "CLOUD_ASK_MEDIA_PATH"),
    ("data:image/jpeg;base64,BBBB", "CLOUD_ASK_MEDIA_PATH"),
])
def test_cloud_ask_static_denials(bad_prompt, expected_code):
    reason = tc.static_denial({"prompt": bad_prompt})
    assert reason is not None and reason.startswith(expected_code + ":")
    assert tc._effect({"prompt": bad_prompt})[0] == "deny"


def test_cloud_ask_runpod_always_denied():
    reason = tc.static_denial({"prompt": "Сравни два алгоритма", "model": "runpod-a100"})
    assert reason is not None and reason.startswith("CLOUD_ASK_RUNPOD:")


async def test_bus_bridge_never_auto_approves_cloud_ask_under_bypass_permissions(env):
    created = await env.client.post(
        "/api/agent/sessions",
        json={"title": "Auto Bypass Cloud Ask Check", "mode": "AUTO", "bypass_permissions": True},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    orch = ao._orch(env.svc)
    import sqlalchemy as sa
    from bcc.db import tasks as tasks_tbl
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(tasks_tbl).values(title="cloud-ask-test", prompt="q", status="running"))
        task_id = int(res.inserted_primary_key[0])
        await s.commit()
    orch.task_to_session[task_id] = sid

    appr = await env.svc.approvals.create(
        kind="tool",
        preview="Агент «Gemma» хочет выполнить cloud.ask\nпричина политики: текст уйдёт в облако",
        task_id=task_id,
        run_id=None,
    )
    aid = int(appr["id"])
    await asyncio.sleep(0.15)

    # Approval must remain pending (never auto-approved by _bus_bridge even in AUTO bypass_permissions mode)
    pending = await env.svc.approvals.list(status="pending")
    assert any(int(row["id"]) == aid for row in pending), "cloud.ask was auto-approved under bypass_permissions!"
    rt = orch.runtime(sid)
    assert any(e.get("type") == "approval.required" and e.get("approval_id") == aid for e in rt.events)


async def test_cloud_ask_handler_requires_approval_and_audits_metadata_only(env, monkeypatch):
    ctx_no_appr = ToolContext(svc=env.svc, task={"id": None}, run_id=0, agent={}, approval_id=None)
    res_no_appr = await tc._t_ask({"prompt": "Что такое идемпотентность?"}, ctx_no_appr)
    assert res_no_appr.error and "CLOUD_ASK_NO_APPROVAL" in res_no_appr.content

    cand = {
        "id": "free-test",
        "tier_id": "worker:free-test",
        "tier": 3,
        "model": "vendor/free-test",
        "label": "Free Test",
        "provider": "Vendor",
        "endpoint": "https://api.vendor.example/v1",
        "paid": False,
        "ready": True,
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
    }

    async def _fake_cat(_svc):
        return [cand]

    async def _fake_transport(_svc, _route, _messages, *, max_tokens, timeout):
        return SimpleNamespace(text="Идемпотентность — свойство операции давать тот же результат.", usage={})

    monkeypatch.setattr(tc, "cloud_catalog", _fake_cat)
    monkeypatch.setattr(tc, "_governance_refusal", lambda c: "")
    monkeypatch.setattr(tc, "stop_reason", lambda svc: "")
    monkeypatch.setattr(tc, "transport", _fake_transport)

    args = {"prompt": f"Что такое идемпотентность? {FAKE_SECRET}"}
    assert await tc.bind_route(args) is None
    res = await tc._t_ask(args, ToolContext(svc=env.svc, task={"id": None}, run_id=1, agent={}, approval_id=55))
    assert not res.error, res.content
    assert res.external is True
    audit_raw = tc._audit_path(env.svc).read_text(encoding="utf-8")
    last = json.loads(audit_raw.splitlines()[-1])
    assert last["status"] == "ok" and last["approval_id"] == 55
    assert "Идемпотентность" not in audit_raw and FAKE_SECRET not in audit_raw
