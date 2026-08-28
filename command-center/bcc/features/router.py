"""Feature 02 — Smart Model Router.

Обвязка готовой чистой логики bcc/v2/model_router.route над реестром моделей:
регистрирует хук engine.pick_model, кладёт объяснение маршрута в task_runs.route
и шлёт router.route_selected. Логику скоринга не переписываем (пак — owner).
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import models as models_t, settings_kv, task_runs as runs_t, tasks as tasks_t
from ..v2.model_router import ModelCandidate, RouteRequest, route
from . import Feature

RULES_KEY = "router.rules"
DEFAULT_RULES = {
    # какие способности требует тип задачи (task.kind → verified/advertised caps)
    "requires": {"coding": ["coding"], "vision": ["vision"], "review": ["coding"]},
    # роль-скоры моделей по типу задачи можно доуточнять из UI; дефолт — из caps
    "prefer_local": True,
}

router = APIRouter()


def _requires(kind: str, rules: dict) -> set[str]:
    return set((rules.get("requires") or {}).get(kind, []))


async def _rules(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == RULES_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return dict(DEFAULT_RULES)


async def _save_rules(svc, rules: dict) -> None:
    enc = svc.vault.encrypt(json.dumps(rules))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == RULES_KEY))
        await s.execute(sa.insert(settings_kv).values(key=RULES_KEY, value_enc=enc))
        await s.commit()


async def _candidates(svc, rules: dict) -> list[ModelCandidate]:
    """Кандидаты из реестра + живой сигнал (health, bench, доля успехов)."""
    async with svc.db.session() as s:
        models = (await s.execute(sa.select(models_t))).fetchall()
        # доля успешных run'ов по alias (historical performance)
        stats = (await s.execute(sa.select(
            runs_t.c.model_alias,
            sa.func.count().label("n"),
            sa.func.sum(sa.case((runs_t.c.status == "completed", 1), else_=0)).label("ok"))
            .where(runs_t.c.model_alias.isnot(None)).group_by(runs_t.c.model_alias))).fetchall()
    success = {r._mapping["model_alias"]: (r._mapping["ok"] or 0) / r._mapping["n"]
               for r in stats if r._mapping["n"]}
    role_scores = rules.get("role_scores") or {}
    out: list[ModelCandidate] = []
    for r in models:
        m = r._mapping
        caps = set((m["caps"] or {}).keys()) if isinstance(m["caps"], dict) else set()
        verified = {k for k, v in (m["caps"] or {}).items() if v} if isinstance(m["caps"], dict) else set()
        bench = m["bench"] if isinstance(m["bench"], dict) else {}
        out.append(ModelCandidate(
            id=m["id"], alias=m["alias"],
            online=m["status"] == "online",
            local=m["kind"] == "local",
            context_window=m["context_window"] or 8192,
            capabilities=caps, verified_capabilities=verified,
            price_in=m["price_in"] or 0.0, price_out=m["price_out"] or 0.0,
            latency_ms=bench.get("latency_ms"), gen_tps=bench.get("gen_tps"),
            success_rate=success.get(m["alias"]),
            role_scores=role_scores.get(m["alias"], {})))
    return out


async def _make_pick_hook(svc):
    async def pick_model(task, agent):
        rules = await _rules(svc)
        meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
        # роутер включается флагом задачи (meta.route=true) или типом kind≠generic;
        # иначе оставляем модель агента (None) — не ломаем существующее поведение
        kind = task.get("kind") or "generic"
        if not (meta.get("route") or kind not in ("generic", None)):
            return None
        budget = meta.get("cloud_budget_usd")
        cloud_allowed = budget is None or budget > 0
        req = RouteRequest(
            task_type=kind, requires=_requires(kind, rules),
            min_context=int(meta.get("min_context") or 0),
            cloud_allowed=cloud_allowed,
            max_price_out=meta.get("max_price_out"),
            available_memory_mb=meta.get("available_memory_mb"),
            prefer_local=bool(rules.get("prefer_local", True)))
        decision = route(req, await _candidates(svc, rules))
        if decision.model is None:
            return None            # никого не выбрали → модель агента
        route_info = {"alias": decision.model.alias, "score": decision.score,
                      "reasons": decision.reasons, "rejected": decision.rejected,
                      "task_type": kind}
        await svc.bus.emit("router.route_selected", task_id=task["id"],
                           model_id=decision.model.id, alias=decision.model.alias,
                           reason="; ".join(decision.reasons)[:300])
        return {"model_id": int(decision.model.id), "route": route_info}
    return pick_model


async def _setup(svc):
    svc.engine.add_hook("pick_model", await _make_pick_hook(svc))


@router.get("/router/rules")
async def get_rules(request: Request):
    return await _rules(request.app.state.svc)


@router.patch("/router/rules")
async def patch_rules(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    rules = await _rules(svc)
    rules.update(body or {})
    await _save_rules(svc, rules)
    return rules


@router.get("/router/explain")
async def explain(request: Request, task_id: int):
    """Объяснение последнего маршрута задачи — из task_runs.route."""
    svc = request.app.state.svc
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t.c.route, runs_t.c.model_alias)
                               .where(runs_t.c.task_id == task_id)
                               .order_by(runs_t.c.id.desc()).limit(1))).first()
    if row is None:
        raise HTTPException(404, {"message": "у задачи нет прогонов"})
    return {"route": row._mapping["route"], "model_alias": row._mapping["model_alias"]}


@router.post("/router/preview")
async def preview(request: Request):
    """Показать выбор роутера для гипотетической задачи, ничего не выполняя."""
    svc = request.app.state.svc
    body = await request.json()
    rules = await _rules(svc)
    kind = body.get("task_type") or "generic"
    req = RouteRequest(task_type=kind, requires=_requires(kind, rules),
                       min_context=int(body.get("min_context") or 0),
                       cloud_allowed=bool(body.get("cloud_allowed", True)),
                       max_price_out=body.get("max_price_out"),
                       available_memory_mb=body.get("available_memory_mb"),
                       prefer_local=bool(rules.get("prefer_local", True)))
    d = route(req, await _candidates(svc, rules))
    return {"selected": d.model.alias if d.model else None, "score": d.score,
            "reasons": d.reasons, "rejected": d.rejected}


FEATURE = Feature(name="router", router=router, setup=_setup)
