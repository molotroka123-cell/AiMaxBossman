"""Feature 03 — AI Governor.

Наблюдатель поверх готовой логики bcc/v2/governor: считает повторяющиеся ошибки,
runaway retries, простой (no-progress), облачный перерасход; вмешивается (stop/
ask_human) с записью в interventions и событием governor.intervention.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import (interventions as interv_t, run_events as run_events_t, settings_kv,
                  task_runs as runs_t, tasks as tasks_t, utcnow)
from ..v2.governor import GovernorState, GovernorThresholds
from . import Feature

RULES_KEY = "governor.rules"
router = APIRouter()


def _sig(error: str) -> str:
    """Нормализуем текст ошибки: убираем числа/пути, чтобы «та же» ошибка совпадала."""
    import re
    s = re.sub(r"\d+", "N", (error or "").lower())
    s = re.sub(r"/[^\s]+", "/P", s)
    return s.strip()[:120]


async def _rules(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == RULES_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {"repeated_error_limit": 3, "no_progress_steps": 6, "max_retries": 5}


async def _record(svc, target_kind: str, target_id: int, reason: str, action: str,
                  detail: dict | None = None) -> int:
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(interv_t).values(
            target_kind=target_kind, target_id=target_id, reason=reason,
            action=action, detail=detail or {}, created_at=utcnow()))
        iid = int(res.inserted_primary_key[0])
        await s.commit()
    await svc.bus.emit("governor.intervention", intervention_id=iid,
                       target_kind=target_kind, target_id=target_id,
                       reason=reason, action=action)
    return iid


async def _state(svc) -> GovernorState:
    r = await _rules(svc)
    return GovernorState(thresholds=GovernorThresholds(
        repeated_error_limit=r.get("repeated_error_limit", 3),
        no_progress_steps=r.get("no_progress_steps", 6),
        max_retries=r.get("max_retries", 5)))


async def _on_failure(svc):
    async def on_failure(task, run_id, error):
        # окно ошибок этого прогона из run_events
        async with svc.db.session() as s:
            rows = (await s.execute(sa.select(run_events_t.c.message)
                                    .where(run_events_t.c.run_id == run_id,
                                           run_events_t.c.level == "error")
                                    .order_by(run_events_t.c.id))).fetchall()
        st = await _state(svc)
        # Governor — backstop для runaway-задач: если max_retries задачи не больше
        # порога, движок сам доведёт её до failed — не вмешиваемся (не подменяем
        # failed на stopped). Ловим только те, что движок гонял бы слишком долго.
        max_retries = int(task.get("max_retries") or 0)
        engine_handles = max_retries <= st.thresholds.repeated_error_limit
        verdict = "none"
        for m in rows:
            verdict = st.record_error(_sig(m._mapping["message"]))
        verdict = st.record_error(_sig(error))
        if verdict != "none" and not engine_handles:
            await _record(svc, "task", task["id"],
                          f"повтор одной ошибки ≥{st.thresholds.repeated_error_limit}",
                          "stopped", {"error": _sig(error)})
            await svc.engine.stop(task["id"])         # прекращаем бесконечный цикл
        # облачный бюджет задачи
        meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
        budget = meta.get("cloud_budget_usd")
        if budget is not None:
            async with svc.db.session() as s:
                spent = (await s.execute(sa.select(sa.func.coalesce(
                    sa.func.sum(runs_t.c.cost_usd), 0.0)).where(
                    runs_t.c.task_id == task["id"]))).scalar_one()
            if spent > float(budget):
                await _record(svc, "task", task["id"],
                              f"облачный бюджет исчерпан: ${spent:.4f} > ${budget}",
                              "stopped", {"spent": spent})
                await svc.engine.stop(task["id"])
    return on_failure


def _step_fingerprint(message: dict) -> str:
    """Отпечаток шага модели.

    V2.1: у ответа с tool_calls content пуст, поэтому по одному тексту все такие
    шаги выглядели одинаково — и Governor останавливал миссию, которая как раз
    активно работала инструментами. Считаем прогрессом РАЗНЫЕ вызовы: имя
    инструмента + аргументы. Повтор одного и того же вызова прогрессом не
    считается — это и есть настоящее зацикливание.
    """
    calls = message.get("tool_calls") or []
    if calls:
        parts = []
        for c in calls:
            fn = c.get("function") or {}
            parts.append(f"{fn.get('name')}({str(fn.get('arguments'))[:160]})")
        return "|".join(parts)
    return str(message.get("content") or "")[:160]


async def _on_step(svc):
    async def on_step(task, run_id, checkpoint):
        """No-progress: последние K шагов модели неотличимы друг от друга.

        Источник истины — сама история прогона (она в БД и переживает рестарт),
        а не счётчик в памяти: GovernorState создаётся заново на каждый вызов,
        поэтому копить прогресс в нём бессмысленно.
        """
        st = await _state(svc)
        window = max(2, int(st.thresholds.no_progress_steps))
        steps = [m for m in (checkpoint.get("messages") or []) if m.get("role") == "assistant"]
        if len(steps) < window:
            return
        tail = [_step_fingerprint(m) for m in steps[-window:]]
        if len(set(tail)) > 1:
            return                      # шаги различаются — работа идёт
        await _record(svc, "task", task["id"],
                      f"нет прогресса: {window} одинаковых шагов подряд", "paused",
                      {"fingerprint": tail[0][:200]})
        await svc.engine.pause(task["id"])
    return on_step


async def _tick(svc):
    """Зависшие run'ы: running с истёкшей арендой втрое — эскалация человеку."""
    from datetime import timedelta
    cutoff = utcnow() - timedelta(seconds=svc.engine.lease_seconds * 3)
    async with svc.db.session() as s:
        stuck = (await s.execute(sa.select(runs_t.c.id, runs_t.c.task_id).where(
            runs_t.c.status == "running", runs_t.c.worker_lease_until < cutoff))).fetchall()
    for r in stuck:
        await _record(svc, "run", r._mapping["id"], "зависший прогон", "escalated")
        await svc.approvals.create(kind="governor", preview=f"Прогон {r._mapping['id']} завис",
                                   task_id=r._mapping["task_id"], run_id=r._mapping["id"])


async def _setup(svc):
    svc.engine.add_hook("on_failure", await _on_failure(svc))
    svc.engine.add_hook("on_step", await _on_step(svc))


@router.get("/governor/interventions")
async def list_interventions(request: Request, limit: int = 100):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(interv_t).order_by(interv_t.c.id.desc())
                                .limit(min(limit, 200)))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/governor/rules")
async def get_rules(request: Request):
    return await _rules(request.app.state.svc)


@router.patch("/governor/rules")
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


FEATURE = Feature(name="governor", router=router, setup=_setup, tick=_tick, tick_seconds=30.0)
