"""Local operator — owner routes (thin adapter over bcc.local_operator).

  GET  /local-operator/status   what Ollama has installed right now + which local agents already exist
  POST /local-operator/sync     {harness: true} — register the installed local chat models and create the missing
                                local agents («Локально · <model>», «Локально · <model> · ПК», «Bossman Harness»)

Sync only ADDS: models already registered on that Ollama endpoint and agents with the same name are reported and left
as the owner set them. The created agents are ordinary agents: they appear in the chat picker and every run goes
through the engine — tool policy, per-action approvals for computer.act, owner STOP, receipts. There is no second
agent loop here. Mounted under /api with the panel's session/CSRF auth like every feature router; the model has no
tool that reaches these routes.
"""
from __future__ import annotations

import asyncio
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import Feature
from ..db import agents as agents_t, rows_dicts, utcnow
from ..local_operator import inventory as inv_mod
from ..local_operator.provision import (CHAT_PREFIX, HARNESS_NAME, PROVIDER_KIND, PROVIDER_NAME, find_provider,
                                        plan_agents, plan_models)
from ..provider_governance import is_cloud_offload

router = APIRouter()


def _svc(request: Request):
    return request.app.state.svc


async def _probe(svc) -> inv_mod.Inventory:
    # test seam: svc._local_operator_transport (httpx MockTransport); production talks to the real Ollama
    return await inv_mod.probe(transport=getattr(svc, "_local_operator_transport", None))


async def _agents(svc) -> list[dict]:
    async with svc.db.session() as s:
        res = await s.execute(sa.select(agents_t).order_by(agents_t.c.id))
        return rows_dicts(res.fetchall())


def _local_agent_rows(agents: list[dict]) -> list[dict]:
    return [{"id": a.get("id"), "name": a.get("name"), "model_id": a.get("model_id"),
             "tools": a.get("tools") or [], "enabled": a.get("enabled")}
            for a in agents if str(a.get("name") or "").startswith(CHAT_PREFIX) or a.get("name") == HARNESS_NAME]


@router.get("/local-operator/status")
async def status(request: Request):
    svc = _svc(request)
    inv = await _probe(svc)
    providers = await svc.registry.list_providers()
    prov = find_provider(providers, inv.v1_url) if inv.ok else None
    models = await svc.registry.list_models()
    registered = {m["name"]: m["id"] for m in models if prov is not None and m.get("provider_id") == prov.get("id")}
    out = inv.public()
    for m in out["models"]:
        m["model_id"] = registered.get(m["name"])
    return {"ollama": out, "provider_id": prov.get("id") if prov else None,
            "agents": _local_agent_rows(await _agents(svc))}


async def sync(svc, *, harness: bool = True) -> dict[str, Any]:
    """Apply the plan. Returns a report; never raises for an unreachable Ollama (ok=False instead)."""
    lock = getattr(svc, "_local_operator_lock", None)
    if lock is None:
        lock = svc._local_operator_lock = asyncio.Lock()
    async with lock:
        inv = await _probe(svc)
        if not inv.ok:
            return {"ok": False, "code": "OLLAMA_UNREACHABLE", "error": inv.error, "tried": inv.tried,
                    "hint": "запустите Ollama (ollama serve) и повторите"}
        providers = await svc.registry.list_providers()
        prov = find_provider(providers, inv.v1_url)
        provider_created = False
        if prov is None:
            prov = await svc.registry.create_provider(PROVIDER_NAME, PROVIDER_KIND, inv.v1_url)
            provider_created = True
        pid = int(prov["id"])
        plan = plan_models(inv.models, await svc.registry.list_models(), pid)
        for row in plan.models_to_create:
            # last line of defence: the plan already skips :cloud tags; never register a cloud-offload model as local
            if is_cloud_offload({"base_url": inv.v1_url}, row):
                continue
            await svc.registry.create_model(provider_id=pid, **row)
        model_ids = {str(m["name"]): int(m["id"]) for m in await svc.registry.list_models()
                     if m.get("provider_id") == pid}
        for r in plan.model_report:
            if r["status"] == "create":
                r["model_id"] = model_ids.get(r["name"])
        create, agent_report = plan_agents(inv.models, model_ids, await _agents(svc), harness=harness)
        created_ids: dict[str, int] = {}
        async with svc.db.session() as s:
            for spec in create:
                res = await s.execute(sa.insert(agents_t).values(created_at=utcnow(), **spec))
                created_ids[spec["name"]] = int(res.inserted_primary_key[0])
            await s.commit()
        for name, aid in created_ids.items():
            await svc.bus.emit("agent.created", id=aid, name=name)
        for r in agent_report:
            if r["status"] == "create":
                r["agent_id"] = created_ids.get(r["agent"])
        await svc.bus.emit("local_operator.synced", base_url=inv.base_url, models=len(model_ids),
                           agents_created=len(created_ids), provider_created=provider_created)
        return {"ok": True, "base_url": inv.base_url, "version": inv.version, "provider_id": pid,
                "provider_created": provider_created, "models": plan.model_report, "agents": agent_report}


@router.post("/local-operator/sync")
async def http_sync(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    harness = not (isinstance(body, dict) and body.get("harness") is False)
    out = await sync(_svc(request), harness=harness)
    if not out.get("ok"):
        return JSONResponse(out, status_code=503)
    return out


FEATURE = Feature(name="local_operator", router=router)
