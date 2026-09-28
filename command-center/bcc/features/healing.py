"""Feature 14 — Self-Healing.

Поверх готовой bcc/v2/recovery.RecoveryPolicy: обнаружение падений endpoint'ов
моделей (окно сетевых ошибок), degraded→recovered цикл, generic report-API для
других подсистем (browser/terminal). Лимит попыток → эскалация человеку.
"""
from __future__ import annotations

import json
import time

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import models as models_t, recovery_attempts as rec_t, settings_kv, utcnow
from ..v2.recovery import RecoveryPolicy
from . import Feature

RULES_KEY = "healing.rules"
router = APIRouter()

# окно ошибок в памяти: model_id → [(ts, sig)]
_error_window: dict[int, list[float]] = {}
_attempts: dict[str, list[float]] = {}   # target → метки времени попыток


async def _rules(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == RULES_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {"window_seconds": 300, "error_threshold": 3, "attempt_limit": 3}


async def _attempt(svc, target_kind: str, target_id, failure: str, action: str,
                   status: str = "started", detail: dict | None = None) -> int:
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(rec_t).values(
            target_kind=target_kind, target_id=target_id if isinstance(target_id, int) else None,
            failure=failure[:500], action=action, status=status,
            detail=detail or {}, created_at=utcnow()))
        rid = int(res.inserted_primary_key[0])
        await s.commit()
    ev = {"started": "recovery.started", "completed": "recovery.completed",
          "escalated": "recovery.escalated"}.get(status, "recovery.started")
    await svc.bus.emit(ev, attempt_id=rid, target=f"{target_kind}:{target_id}",
                       failure=failure[:200], action=action)
    return rid


async def _escalate(svc, preview: str) -> int:
    """One open escalation per target. Every failure past the limit used to create
    ANOTHER identical pending approval (RC 1.9 soak: nine «Модель 4 не восстанавливается»
    in ten minutes). While one is pending the owner already has the question; the
    attempt is still recorded by the caller. After a decision a new one may be raised."""
    from ..db import approvals as approvals_t
    async with svc.db.session() as s:
        open_id = (await s.execute(sa.select(approvals_t.c.id).where(
            approvals_t.c.kind == "healing_escalation", approvals_t.c.status == "pending",
            approvals_t.c.preview == preview).limit(1))).scalar()
    if open_id is not None:
        return int(open_id)
    return int((await svc.approvals.create(kind="healing_escalation", preview=preview))["id"])


async def _model_label(svc, model_id: int) -> str:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(models_t.c.alias, models_t.c.name)
                               .where(models_t.c.id == model_id))).first()
    name = (row and (row[0] or row[1])) or ""
    return f"Модель «{name}» (#{model_id})" if name else f"Модель {model_id}"


def _within_limit(target: str, rules: dict) -> bool:
    now = time.monotonic()
    window = rules.get("window_seconds", 300)
    marks = [t for t in _attempts.get(target, []) if now - t < window]
    marks.append(now)
    _attempts[target] = marks
    return len(marks) <= rules.get("attempt_limit", 3)


async def _on_failure(svc):
    async def on_failure(task, run_id, error):
        rules = await _rules(svc)
        if "network" not in error.lower() and "не ответил" not in error and "нет связи" not in error:
            return
        # найдём модель агента
        async with svc.db.session() as s:
            from ..db import agents as agents_t
            agent = (await s.execute(sa.select(agents_t.c.model_id).where(
                agents_t.c.id == task["agent_id"]))).first()
        model_id = agent._mapping["model_id"] if agent else None
        if not model_id:
            return
        now = time.monotonic()
        window = rules.get("window_seconds", 300)
        marks = [t for t in _error_window.get(model_id, []) if now - t < window]
        marks.append(now)
        _error_window[model_id] = marks
        if len(marks) < rules.get("error_threshold", 3):
            return
        # порог: re-check модели
        target = f"model:{model_id}"
        if not _within_limit(target, rules):
            await _attempt(svc, "model", model_id, error, "escalate", "escalated")
            await _escalate(svc, f"{await _model_label(svc, model_id)} не восстанавливается")
            return
        await _attempt(svc, "model", model_id, error, "retry", "started")
        try:
            health = await svc.registry.check_model(model_id)
        except Exception:
            health = {"status": "error"}
        if health.get("status") != "online":
            async with svc.db.session() as s:
                await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                    status="error", status_detail="degraded: endpoint недоступен"))
                await s.commit()
            await svc.bus.emit("model.degraded", id=model_id)
    return on_failure


LOCAL_PROBE_SECONDS = 60


async def probe_local_models(svc, rules: dict | None = None) -> list[int]:
    """RC19: a local endpoint's «online» is a measurement, not a memory.

    Local llama.cpp servers on :8081/:8082 were stopped, yet their models kept
    status «online» (only a failing run or a manual «Проверить» re-checked
    them), so the UI and the Smart Router trusted endpoints that no longer
    existed. Every local model whose online/offline status is older than
    `local_probe_seconds` is re-checked through the same `check_model`
    (GET /models of the local server: no inference, no load, no cloud).
    A dead endpoint becomes «offline»; a revived one «online» again. Models in
    «error»/«unknown» are left to their own paths (degraded re-check above,
    an explicit test). Returns the ids that were probed.
    """
    from ..db import providers as providers_t
    from ..v2.model_router import derive_local
    rules = rules or {}
    every = float(rules.get("local_probe_seconds", LOCAL_PROBE_SECONDS) or 0)
    if every <= 0:
        return []
    now = utcnow()
    async with svc.db.session() as s:
        rows = (await s.execute(
            sa.select(models_t.c.id, models_t.c.kind, models_t.c.last_check,
                      providers_t.c.kind.label("provider_kind"),
                      providers_t.c.base_url.label("provider_base_url"))
            .select_from(models_t.join(providers_t, models_t.c.provider_id == providers_t.c.id))
            .where(models_t.c.status.in_(("online", "offline"))))).fetchall()
    probed: list[int] = []
    for r in rows:
        m = r._mapping
        local, _why = derive_local(m["kind"], m["provider_kind"], m["provider_base_url"])
        if not local:
            continue
        last = m["last_check"]               # naive UTC, like utcnow()
        if last is not None and (now - last).total_seconds() < every:
            continue
        try:
            await svc.registry.check_model(int(m["id"]))
        except Exception:  # noqa: BLE001 — one broken row must not stop the others
            continue
        probed.append(int(m["id"]))
    return probed


async def _tick(svc):
    """Degraded-модели: периодический re-check; online → recovery.completed."""
    await probe_local_models(svc, await _rules(svc))
    async with svc.db.session() as s:
        degraded = (await s.execute(sa.select(models_t.c.id).where(
            models_t.c.status == "error",
            models_t.c.status_detail.like("degraded%")))).fetchall()
    for r in degraded:
        mid = r._mapping["id"]
        try:
            health = await svc.registry.check_model(mid)
        except Exception:
            continue
        if health.get("status") == "online":
            _error_window.pop(mid, None)
            _attempts.pop(f"model:{mid}", None)
            await _attempt(svc, "model", mid, "endpoint восстановлен", "retry", "completed")


@router.post("/healing/report")
async def report(request: Request):
    """Единая точка: другие подсистемы (browser/terminal) сообщают о сбое."""
    svc = request.app.state.svc
    body = await request.json()
    rules = await _rules(svc)
    target_kind = body.get("target_kind", "component")
    target_id = body.get("target_id")
    target = f"{target_kind}:{target_id}"
    action = {"browser": "restart_component", "terminal": "restart_component"}.get(
        target_kind, "retry")
    if not _within_limit(target, rules):
        rid = await _attempt(svc, target_kind, target_id, body.get("failure", ""),
                             "escalate", "escalated")
        await _escalate(svc, f"{target} не восстанавливается")
        return {"attempt_id": rid, "status": "escalated"}
    rid = await _attempt(svc, target_kind, target_id, body.get("failure", ""), action, "started")
    return {"attempt_id": rid, "status": "started", "action": action}


@router.get("/healing/attempts")
async def attempts(request: Request, limit: int = 100):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(rec_t).order_by(rec_t.c.id.desc())
                                .limit(min(limit, 200)))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/healing/rules")
async def get_rules(request: Request):
    return await _rules(request.app.state.svc)


@router.patch("/healing/rules")
async def patch_rules(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    rules = await _rules(svc)
    rules.update(body or {})
    enc = svc.vault.encrypt(json.dumps(rules))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == RULES_KEY))
        await s.execute(sa.insert(settings_kv).values(key=RULES_KEY, value_enc=enc))
        await s.commit()
    return rules


async def _setup(svc):
    svc.engine.add_hook("on_failure", await _on_failure(svc))


FEATURE = Feature(name="healing", router=router, setup=_setup, tick=_tick, tick_seconds=30.0)
