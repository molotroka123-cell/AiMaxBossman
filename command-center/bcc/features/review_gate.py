"""Feature 08 — Automatic Reviewer Gate.

Поверх готовой bcc/v2/reviewer_gate.ReviewGate и хука engine.gate_completion:
задача не станет completed без PASS; FAIL → фидбек и повтор (лимит), потом
эскалация в waiting_approval + approval. Ревьюер: модель агента-ревьюера или
детерминированная проверка критерия (без модели).
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import agents as agents_t, tasks as tasks_t, utcnow
from ..v2.reviewer_gate import ReviewGate
from ..v2.tables import evaluations as evals_t
from . import Feature

router = APIRouter()


async def _task_meta(svc, task_id: int) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == task_id))).first()
    return (row._mapping["meta"] if row and isinstance(row._mapping["meta"], dict) else {}) or {}


async def _set_meta(svc, task_id: int, meta: dict) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            meta=meta, updated_at=utcnow()))
        await s.commit()


async def _verdict(svc, review: dict, answer: str) -> tuple[bool, str]:
    """PASS/FAIL. С reviewer_agent_id — реальный вызов модели; иначе критерий как подстрока."""
    reviewer_id = review.get("reviewer_agent_id")
    criteria = review.get("criteria", "")
    if reviewer_id:
        async with svc.db.session() as s:
            agent = (await s.execute(sa.select(agents_t).where(
                agents_t.c.id == reviewer_id))).first()
        if agent is not None:
            try:
                adapter, model = await svc.registry.adapter_for(agent._mapping["model_id"])
                prompt = (f"Ты — ревьюер. Критерии: {criteria}\n\nРезультат кодера:\n{answer}\n\n"
                          "Ответь первым словом PASS или FAIL, затем причину.")
                res = await adapter.chat(model["name"], [{"role": "user", "content": prompt}],
                                         max_tokens=200)
                text = (res.text or "").strip()
                passed = text.upper().startswith("PASS")
                return passed, text[:400]
            except Exception as exc:
                return False, f"ревьюер недоступен: {exc}"
    # детерминированная проверка
    if criteria and criteria.lower() in (answer or "").lower():
        return True, f"критерий «{criteria}» найден"
    return False, f"критерий «{criteria}» не выполнен"


async def _record_eval(svc, task_id: int, run_id: int, iteration: int,
                       passed: bool, feedback: str) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.insert(evals_t).values(
            task_id=task_id, run_id=run_id, iteration=iteration,
            passed=passed, feedback=feedback, created_at=utcnow()))
        await s.commit()


async def _gate(svc):
    async def gate_completion(task, run_id, answer):
        meta = await _task_meta(svc, task["id"])
        review = meta.get("review")
        if not review:
            return None
        max_iter = int(review.get("max_review_retries", 2)) + 1
        gate = ReviewGate(max_iterations=max_iter, iteration=int(meta.get("review_attempts", 0)))
        gate.submit_for_review()
        passed, feedback = await _verdict(svc, review, answer)
        status = gate.review_result(passed, feedback)
        meta["review_attempts"] = gate.iteration
        meta.setdefault("review_history", []).append(
            {"iteration": gate.iteration, "passed": passed, "feedback": feedback})
        await _set_meta(svc, task["id"], meta)
        await _record_eval(svc, task["id"], run_id, gate.iteration, passed, feedback)
        if status == "passed":
            return {"verdict": "pass"}
        if status == "fix":
            return {"verdict": "fail", "feedback": feedback, "requeue": True}
        # waiting_approval — эскалация, лимит исчерпан
        await svc.approvals.create(kind="review_escalation", task_id=task["id"], run_id=run_id,
                                   preview=f"Ревью не пройдено {gate.iteration} раз.\n{feedback}")
        return {"verdict": "fail", "requeue": False, "status": "waiting_approval",
                "reasons": feedback}
    return gate_completion


async def _tick(svc):
    """Manual override: одобренная эскалация ревью → задача принудительно completed."""
    async with svc.db.session() as s:
        from ..db import approvals as appr_t
        rows = (await s.execute(sa.select(appr_t).where(
            appr_t.c.kind == "review_escalation", appr_t.c.status == "approved"))).fetchall()
    for r in rows:
        a = dict(r._mapping)
        task_id = a["task_id"]
        async with svc.db.session() as s:
            t = (await s.execute(sa.select(tasks_t.c.status).where(
                tasks_t.c.id == task_id))).first()
            if t and t._mapping["status"] not in ("completed", "cancelled"):
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
                    status="completed", updated_at=utcnow()))
                # погасим approval, чтобы не срабатывать повторно
                await s.execute(sa.update(appr_t).where(appr_t.c.id == a["id"]).values(
                    kind="review_escalation_done"))
                await s.commit()
                await svc.bus.emit("task.completed", task_id=task_id, run_id=a["run_id"],
                                   override=True)


@router.post("/review/enable")
async def enable_review(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    task_id = body.get("task_id")
    if not task_id:
        raise HTTPException(422, {"message": "нужно task_id"})
    meta = await _task_meta(svc, task_id)
    meta["review"] = {"reviewer_agent_id": body.get("reviewer_agent_id"),
                      "criteria": body.get("criteria", ""),
                      "max_review_retries": int(body.get("max_review_retries", 2))}
    await _set_meta(svc, task_id, meta)
    return {"ok": True, "review": meta["review"]}


@router.get("/review/status")
async def review_status(request: Request, task_id: int):
    svc = request.app.state.svc
    meta = await _task_meta(svc, task_id)
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(evals_t).where(
            evals_t.c.task_id == task_id).order_by(evals_t.c.id))).fetchall()
    return {"attempts": meta.get("review_attempts", 0),
            "history": meta.get("review_history", []),
            "evaluations": [dict(r._mapping) for r in rows]}


async def _setup(svc):
    svc.engine.add_hook("gate_completion", await _gate(svc))


FEATURE = Feature(name="review_gate", router=router, setup=_setup, tick=_tick, tick_seconds=10.0)
