"""RC 1.9 soak (workstream F): «Ждут решения» filled with identical healing escalations.

Repro (60-min soak, one agent bound to an unreachable model): every failed attempt past
the healing limit created ANOTHER pending approval «Модель 4 не восстанавливается» —
nine identical rows within ten minutes, several seconds apart. The owner's decision
queue (and every surface that mirrors it) filled with duplicates of one open question,
naming the model only by its database id.

One open escalation per target: while one is pending, further failures are recorded as
attempts but do not add another approval. Once the owner decides it, a new failure may
escalate again. The preview names the model.
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import models as models_t


async def _pending(env) -> list[dict]:
    rows = (await env.client.get("/api/approvals?status=pending")).json()
    return [a for a in rows if a["kind"] == "healing_escalation"]


async def test_component_escalation_is_not_duplicated_while_pending(env):
    await env.client.patch("/api/healing/rules", json={"attempt_limit": 1, "window_seconds": 300})
    statuses = [(await env.client.post("/api/healing/report",
                                       json={"target_kind": "browser", "target_id": 7, "failure": "crash"})).json()["status"]
                for _ in range(6)]
    assert statuses.count("escalated") == 5          # every escalation is still recorded as an attempt
    pending = await _pending(env)
    assert len(pending) == 1, pending
    attempts = (await env.client.get("/api/healing/attempts")).json()
    assert sum(1 for a in attempts if a["status"] == "escalated") == 5

    # the owner decides it → the next failure may ask again
    await env.client.post(f"/api/approvals/{pending[0]['id']}", json={"approve": False, "by": "owner"})
    await env.client.post("/api/healing/report", json={"target_kind": "browser", "target_id": 7, "failure": "crash"})
    assert len(await _pending(env)) == 1


async def test_model_escalation_names_the_model_and_is_not_duplicated(env):
    from bcc.features import healing

    await env.client.patch("/api/healing/rules", json={"attempt_limit": 1, "error_threshold": 1,
                                                       "window_seconds": 300})
    prov = (await env.client.post("/api/providers", json={"name": "down", "kind": "openai_compat",
                                                           "base_url": "http://127.0.0.1:9/v1"})).json()
    model_id = (await env.client.post("/api/models", json={"provider_id": prov["id"], "name": "dead-model",
                                                           "alias": "мёртвая-модель"})).json()["id"]
    agent_id = (await env.client.post("/api/agents", json={"name": "heal-agent", "model_id": model_id})).json()["id"]
    async with env.svc.db.session() as s:
        alias = (await s.execute(sa.select(models_t.c.alias, models_t.c.name)
                                 .where(models_t.c.id == model_id))).first()
    healing._error_window.clear()
    healing._attempts.clear()
    hook = await healing._on_failure(env.svc)
    for _ in range(6):
        await hook({"id": 1, "agent_id": agent_id}, None, "нет связи с http://127.0.0.1:9: ConnectError")
    pending = await _pending(env)
    assert len(pending) == 1, pending
    label = alias[0] or alias[1]
    assert label in pending[0]["preview"], pending[0]["preview"]
