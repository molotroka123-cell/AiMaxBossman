"""EH-04 (TRUTH-003 §10) — ЕДИНСТВЕННАЯ точка, где задача становится `completed`.

    finalize_task(engine, run_id, task_id, answer=…, usage=…, verdicts=…)

Проверяет перед записью статуса:
  1. ни один гейт не вернул FAIL;
  2. fence движка актуален (FL-01) — зомби-воркер не финализирует;
  3. объявленные обязательные эффекты (`tasks.meta.review.evidence`,
     `tasks.meta.required_effects`) подтверждены СВЕЖИМ наблюдением пост-состояния
     (`bcc/v2/verification.verify_all`), а не ответом инструмента/модели;
  4. нет незакрытых approval'ов по вызовам инструментов этого run'а;
  5. свежесть: наблюдение сделано после последнего вызова инструмента run'а.
Отказ финализации — не «failed» и не «completed»: решение владельцу
(waiting_approval + review_escalation), как при упавшем гейте.

`finalize_override(svc, task_id, approval)` — единственный путь мимо свежей
верификации: решение ЧЕЛОВЕКА по review_escalation, помечается override=True.
Структурный тест `tests/test_no_direct_completed_writes.py` запрещает любую
другую запись `status="completed"` для `tasks`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from .db import fetch_one, tasks as tasks_t, tool_calls as tool_calls_t, utcnow
from .v2.verification import parse_expected, verify_all

REVIEW_KIND = "review_escalation"


@dataclass
class FinalizeDecision:
    ok: bool
    reason: str = ""
    checks: dict[str, Any] = field(default_factory=dict)


def _required_expectations(task: dict) -> list:
    meta = dict(task.get("meta") or {})
    raw = list(((meta.get("review") or {}).get("evidence")) or []) + list(meta.get("required_effects") or [])
    return parse_expected(raw)


async def finalize_task(engine, run_id: int, task_id: int, *, answer: str, usage: dict[str, Any],
                        verdicts: list[Any] | None = None) -> FinalizeDecision:
    svc = engine.services
    checks: dict[str, Any] = {"verdicts_fail": 0, "expectations": 0, "verification": "NOT_REQUIRED",
                              "open_approvals": 0, "fresh": True}
    for res in verdicts or []:
        if isinstance(res, dict) and str(res.get("verdict", "")).upper() == "FAIL":
            checks["verdicts_fail"] += 1
    if checks["verdicts_fail"]:
        return FinalizeDecision(False, "gate verdict FAIL", checks)

    # 2. fence — FencedOut пробрасывается: зомби ничего не пишет (обрабатывает execute())
    await engine.assert_fence(run_id)

    async with engine.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id) or {}
        rows = (await s.execute(sa.select(tool_calls_t.c.status, tool_calls_t.c.finished_at).where(
            tool_calls_t.c.run_id == run_id))).fetchall()
    checks["open_approvals"] = sum(1 for r in rows if r[0] == "pending_approval")
    if checks["open_approvals"]:
        return FinalizeDecision(False, "tool call still waiting for approval", checks)
    last_effect = max((r[1] for r in rows if r[1] is not None), default=None)

    expected = _required_expectations(task)
    checks["expectations"] = len(expected)
    if expected:
        try:
            from .features.tools_terminal import _roots
            roots = await _roots(svc) if svc is not None else []
        except Exception:  # noqa: BLE001
            roots = []
        status, reason, results = await verify_all(expected, svc=svc, task=task, roots=roots)
        checks["verification"] = status
        checks["verification_reason"] = reason[:300]
        observed = [r.observed.observed_at for r in results if r.observed is not None]
        if last_effect is not None and observed and min(observed) < last_effect.timestamp():
            checks["fresh"] = False
            return FinalizeDecision(False, "STALE_EVIDENCE_REJECTED: observation predates the last tool effect", checks)
        if status != "VERIFIED":
            return FinalizeDecision(False, f"required effects not verified: {reason}", checks)

    await engine._finish(run_id, task_id, "completed", result=answer, **usage)
    await engine.bus.emit("task.finalized", task_id=task_id, run_id=run_id, checks=checks, override=False)
    return FinalizeDecision(True, "finalized", checks)


async def finalize_override(svc, task_id: int, *, approval: dict) -> bool:
    """Человек одобрил review_escalation: единственный путь мимо свежей верификации."""
    async with svc.db.session() as s:
        t = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).first()
        if not t or t._mapping["status"] in ("completed", "cancelled"):
            return False
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            status="completed", updated_at=utcnow()))
        await s.commit()
    await svc.bus.emit("task.finalized", task_id=task_id, run_id=approval.get("run_id"), override=True,
                       approval_id=approval.get("id"), decided_by=approval.get("decided_by"))
    await svc.bus.emit("task.completed", task_id=task_id, run_id=approval.get("run_id"), override=True)
    return True
