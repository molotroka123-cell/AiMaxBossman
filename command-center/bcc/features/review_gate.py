"""Feature 08 — Automatic Reviewer Gate (F-012: свежие доказательства).

Поверх готовой bcc/v2/reviewer_gate.ReviewGate и хука engine.gate_completion:
задача не станет completed без VERIFIED. Что изменилось по находке Fable 5.1
F-012 (verification spoofing):

  БЫЛО: PASS, если строка критерия встречается в ответе воркера, или если
        LLM-ревьюер ответил текстом, начинающимся с «PASS». Оба сигнала —
        текст, который контролирует та же модель, что и «делала работу»:
        эхо критерия = completed, «PASS: …» = completed.

  СТАЛО: единственный источник VERIFIED — bcc/v2/verification: ОЖИДАЕМОЕ
        внешнее состояние (review.evidence: файл/строка БД/страница) сравнивается
        с НАБЛЮДЁННЫМ ЗАНОВО. Текст (ответ воркера, вердикт LLM-ревьюера,
        критерий) может только ВЕТИРОВАТЬ (FAILED → фидбек и повтор), но
        никогда не подтверждать. Нет структурированных ожиданий или наблюдение
        невозможно → UNVERIFIED → задача уходит человеку (waiting_approval,
        approval kind=review_escalation): самоотчёт не повышается до VERIFIED.

Обратная совместимость: /api/review/enable принимает прежние поля и новое
`evidence: [{kind, target, expect}]`; review без evidence больше не завершает
задачу автоматически — это осознанное изменение контракта (см.
docs/security/BOSSMAN_SECURITY_REMEDIATION_FINAL.md, F-012).
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import agents as agents_t, tasks as tasks_t, utcnow
from ..v2.reviewer_gate import ReviewGate
from ..v2.tables import evaluations as evals_t
from ..v2.verification import parse_expected, verify_all
from . import Feature

router = APIRouter()

# Статусы вердикта (совпадают с bcc/v2/verification.Status)
VERIFIED, FAILED, UNVERIFIED = "VERIFIED", "FAILED", "UNVERIFIED"


async def _task_meta(svc, task_id: int) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == task_id))).first()
    return (row._mapping["meta"] if row and isinstance(row._mapping["meta"], dict) else {}) or {}


async def _set_meta(svc, task_id: int, meta: dict) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            meta=meta, updated_at=utcnow()))
        await s.commit()


async def _evidence_roots(svc):
    """Корни, внутри которых верификатор имеет право перечитывать файлы: те же,
    что у terminal.run (owner-настройка) — модель не может указать в evidence
    путь вне разрешённой области."""
    from .tools_terminal import _roots
    return await _roots(svc)


async def _llm_veto(svc, review: dict, answer: str) -> str | None:
    """Мнение LLM-ревьюера — только как ВЕТО. Возвращает текст причины, если
    ревьюер сказал FAIL; None — если PASS/недоступен/не задан. «PASS» отсюда
    ничего не подтверждает: это тот же класс самоотчёта, что и ответ воркера."""
    reviewer_id = review.get("reviewer_agent_id")
    if not reviewer_id:
        return None
    criteria = review.get("criteria", "")
    async with svc.db.session() as s:
        agent = (await s.execute(sa.select(agents_t).where(
            agents_t.c.id == reviewer_id))).first()
    if agent is None:
        return None
    try:
        adapter, model = await svc.registry.adapter_for(agent._mapping["model_id"])
        prompt = (f"Ты — ревьюер. Критерии: {criteria}\n\nРезультат кодера:\n{answer}\n\n"
                  "Ответь первым словом PASS или FAIL, затем причину.")
        res = await adapter.chat(model["name"], [{"role": "user", "content": prompt}],
                                 max_tokens=200)
        text = (res.text or "").strip()
    except Exception as exc:  # noqa: BLE001 — недоступный ревьюер не подтверждает и не ветирует
        return None if not criteria else None
    if text.upper().startswith("FAIL"):
        return text[:400]
    return None


async def _verdict(svc, review: dict, answer: str, *, task: dict) -> tuple[str, str, list]:
    """→ (VERIFIED | FAILED | UNVERIFIED, feedback, results).

    Порядок: вето текста (LLM-ревьюер сказал FAIL) → свежая верификация по
    структурированным ожиданиям. Ни ответ воркера, ни «PASS» ревьюера, ни
    подстрока критерия НЕ участвуют в подтверждении."""
    veto = await _llm_veto(svc, review, answer)
    if veto:
        return FAILED, f"ревьюер: {veto}", []
    expected = parse_expected(review.get("evidence"))
    roots = await _evidence_roots(svc)
    status, reason, results = await verify_all(expected, svc=svc, task=task, roots=roots)
    return status, reason, results


async def _record_eval(svc, task_id: int, run_id: int, iteration: int,
                       passed: bool, feedback: str, artifacts: list | None = None) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.insert(evals_t).values(
            task_id=task_id, run_id=run_id, iteration=iteration,
            passed=passed, feedback=feedback, artifacts=artifacts or [],
            created_at=utcnow()))
        await s.commit()


def _evidence_artifacts(results: list) -> list[dict]:
    out = []
    for r in results:
        out.append({"status": r.status, "kind": r.expected.kind if r.expected else "",
                    "target": r.expected.target if r.expected else "",
                    "reason": r.reason,
                    "evidence": [{"source": e.source, "detail": e.detail, "hash": e.hash}
                                 for e in r.evidence]})
    return out


async def _gate(svc):
    async def gate_completion(task, run_id, answer):
        meta = await _task_meta(svc, task["id"])
        review = meta.get("review")
        if not review:
            return {"verdict": "NOT_APPLICABLE"}
        max_iter = int(review.get("max_review_retries", 2)) + 1
        gate = ReviewGate(max_iterations=max_iter, iteration=int(meta.get("review_attempts", 0)))
        gate.submit_for_review()
        status, feedback, results = await _verdict(svc, review, answer, task=task)
        artifacts = _evidence_artifacts(results)
        passed = status == VERIFIED
        if status == UNVERIFIED:
            # Нельзя ни подтвердить, ни опровергнуть → человеку. Повтор воркера
            # бессмыслен: новых ДОКАЗАТЕЛЬСТВ он не породит, только новый текст.
            gate.status = "waiting_approval"
            gate_status = "waiting_approval"
        else:
            gate_status = gate.review_result(passed, feedback)
        meta["review_attempts"] = gate.iteration
        meta.setdefault("review_history", []).append(
            {"iteration": gate.iteration, "passed": passed, "status": status,
             "feedback": feedback})
        await _set_meta(svc, task["id"], meta)
        await _record_eval(svc, task["id"], run_id, gate.iteration, passed,
                           f"{status}: {feedback}", artifacts)
        if gate_status == "passed":
            return {"verdict": "PASS", "reasons": feedback}
        if gate_status == "fix":
            return {"verdict": "FAIL", "feedback": feedback, "requeue": True}
        # waiting_approval — эскалация: лимит исчерпан ИЛИ верификация невозможна
        head = ("Верификация невозможна (UNVERIFIED) — нужна независимая проверка человеком."
                if status == UNVERIFIED else f"Ревью не пройдено {gate.iteration} раз.")
        await svc.approvals.create(kind="review_escalation", task_id=task["id"], run_id=run_id,
                                   preview=f"{head}\n{feedback}")
        return {"verdict": "FAIL", "requeue": False, "status": "waiting_approval",
                "reasons": f"{status}: {feedback}"}
    return gate_completion


async def _tick(svc):
    """Manual override: одобренная эскалация ревью → задача принудительно completed.
    Это решение ЧЕЛОВЕКА (approval), а не самоотчёт — единственный путь мимо
    свежей верификации; помечается override=True в событии."""
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
            # погасим approval, чтобы не срабатывать повторно; сама запись completed —
            # только через каноническую точку (EH-04), как решение человека (override)
            await s.execute(sa.update(appr_t).where(appr_t.c.id == a["id"]).values(
                kind="review_escalation_done"))
            await s.commit()
        if t and t._mapping["status"] not in ("completed", "cancelled"):
            from ..finalize import finalize_override
            await finalize_override(svc, task_id, approval=a)


@router.post("/review/enable")
async def enable_review(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    task_id = body.get("task_id")
    if not task_id:
        raise HTTPException(422, {"message": "нужно task_id"})
    evidence = body.get("evidence") or []
    if not isinstance(evidence, list):
        raise HTTPException(422, {"message": "evidence должен быть списком {kind,target,expect}"})
    meta = await _task_meta(svc, task_id)
    meta["review"] = {"reviewer_agent_id": body.get("reviewer_agent_id"),
                      "criteria": body.get("criteria", ""),
                      "evidence": [e for e in evidence if isinstance(e, dict)],
                      "max_review_retries": int(body.get("max_review_retries", 2))}
    await _set_meta(svc, task_id, meta)
    return {"ok": True, "review": meta["review"],
            "note": ("без evidence задача не завершится автоматически: "
                     "UNVERIFIED → эскалация человеку (F-012)")}


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
