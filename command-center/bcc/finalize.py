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

`finalize_override(svc, task_id, approval)` — решение ЧЕЛОВЕКА по review_escalation,
помечается override=True; обязательные эффекты всё равно проверяются заново.
Структурный тест `tests/test_no_direct_completed_writes.py` запрещает любую
другую запись `status="completed"` для `tasks`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
import re
from typing import Any

import sqlalchemy as sa

from .db import fetch_one, tasks as tasks_t, task_runs as runs_t, tool_calls as tool_calls_t, utcnow
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
    expected = parse_expected(raw)
    if len(expected) != len(raw):
        raise ValueError("invalid required effect: no obligation may be discarded")
    return expected


def _effectful(row: dict) -> bool:
    """Use the executor capability, not model wording, as the safety backstop."""
    from .tools import REGISTRY
    if row.get("tool") == "terminal.run":
        from .features.action_contract import _looks_like_mutation
        command = str((row.get("args") or {}).get("command") or "")
        # Shell expansions and write-capable git subcommands must not inherit
        # the action classifier's deliberately permissive "read" heuristic.
        if re.search(r"[>$`]|\bgit\s+(?:config|branch|tag|remote|fetch)\b|--output(?:=|\s)", command):
            return True
        return _looks_like_mutation(command)
    spec = REGISTRY.get(row.get("tool", ""))
    return spec is None or spec.category != "read"


def _effect_problem(rows: list[dict], expected: list, task: dict | None = None) -> str:
    # A retry of the exact action may recover a failed attempt. An unrelated
    # successful probe cannot erase a failed mutation. Read-only diagnostic
    # failures are not task failure evidence.
    latest = {}
    for row in rows:
        if _effectful(row):
            latest[(row.get("tool"), row.get("args_hash") or repr(row.get("args")))] = row
    for row in latest.values():
        if row.get("status") != "executed" or row.get("error"):
            return "effectful tool did not succeed: " + str(row.get("tool"))
        if row.get("tool") == "terminal.run":
            match = re.match(r"exit_code=(-?\d+)\b", str(row.get("result_preview") or ""))
            if match is None or int(match[1]) != 0:
                return "effectful terminal outcome is failed or still unobserved"
    if latest and not expected:
        return "effectful execution has no required post-state verification contract"
    if not expected and task:
        from .features.action_contract import classify_all
        from .features.action_router import classify
        if classify_all(task.get("prompt") or "") or classify(task.get("prompt") or ""):
            return "action task has no required post-state verification contract"
    kinds = {e.kind for e in expected}
    required_kinds = {"terminal": {"file", "terminal", "github"}, "browser": {"browser"},
                      "memory": {"memory", "db"}, "apps": {"app", "process"},
                      "opencode": {"file", "github"}}
    for row in latest.values():
        supported = required_kinds.get(row.get("source"))
        if supported is None or not kinds.intersection(supported):
            return "effectful capability has no matching post-state verifier: " + str(row.get("tool"))
    return ""


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
        run = await fetch_one(s, runs_t, run_id)
        if not task or not run or run["task_id"] != task_id:
            return FinalizeDecision(False, "run does not belong to this task", checks)
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(tool_calls_t).where(
            tool_calls_t.c.run_id == run_id).order_by(tool_calls_t.c.id))).fetchall()]
    checks["open_approvals"] = sum(1 for r in rows if r["status"] in ("pending_approval", "approved"))
    if checks["open_approvals"]:
        return FinalizeDecision(False, "tool call still waiting for approval", checks)
    last_effect = max((r["finished_at"] for r in rows if r["finished_at"] is not None), default=None)

    try:
        expected = _required_expectations(task)
    except (TypeError, ValueError):
        return FinalizeDecision(False, "invalid required effects contract", checks)
    problem = _effect_problem(rows, expected, task)
    if problem:
        return FinalizeDecision(False, problem, checks)
    checks["expectations"] = len(expected)
    if expected:
        import time as _time
        t_verify = _time.monotonic()
        try:
            from .features.tools_terminal import _roots
            roots = await _roots(svc) if svc is not None else []
        except Exception:  # noqa: BLE001
            roots = []
        await engine.bus.emit("observation.started", task_id=task_id, run_id=run_id, expectations=len(expected))
        status, reason, results = await verify_all(expected, svc=svc, task=task, roots=roots)
        checks["verification"] = status
        checks["verification_ms"] = int((_time.monotonic() - t_verify) * 1000)
        await engine.bus.emit("verification.result", task_id=task_id, run_id=run_id, status=status,
                              reason=reason[:300], verification_ms=checks["verification_ms"])
        checks["verification_reason"] = reason[:300]
        observed = [r.observed.observed_at for r in results if r.observed is not None]
        if last_effect is not None and observed and min(observed) < last_effect.replace(tzinfo=timezone.utc).timestamp():
            checks["fresh"] = False
            return FinalizeDecision(False, "STALE_EVIDENCE_REJECTED: observation predates the last tool effect", checks)
        if status != "VERIFIED":
            return FinalizeDecision(False, f"required effects not verified: {reason}", checks)

    await engine._finish(run_id, task_id, "completed", result=answer, **usage)
    await engine.bus.emit("task.finalized", task_id=task_id, run_id=run_id, checks=checks, override=False)
    return FinalizeDecision(True, "finalized", checks)


async def finalize_override(svc, task_id: int, *, approval: dict) -> bool:
    """Human review can waive reviewer judgement, never required world evidence."""
    from .db import approvals as approvals_t
    async with svc.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id)
        authorized = await fetch_one(s, approvals_t, approval.get("id"))
        latest_run = (await s.execute(sa.select(runs_t.c.id).where(
            runs_t.c.task_id == task_id).order_by(runs_t.c.id.desc()).limit(1))).scalar()
        if (not task or task["status"] != "waiting_approval" or not authorized
                or authorized["task_id"] != task_id or authorized["status"] != "approved"
                or authorized["kind"] not in (REVIEW_KIND, "review_escalation_done")
                or authorized["run_id"] != approval.get("run_id")
                or latest_run != authorized["run_id"]):
            return False
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(tool_calls_t).where(
            tool_calls_t.c.run_id == authorized["run_id"]).order_by(tool_calls_t.c.id))).fetchall()]
    try:
        expected = _required_expectations(task)
        reason = _effect_problem(rows, expected, task)
        if any(r["status"] in ("pending_approval", "approved") for r in rows):
            reason = "tool call still waiting for approval"
        if expected and not reason:
            from .features.tools_terminal import _roots
            status, reason, results = await verify_all(expected, svc=svc, task=task, roots=await _roots(svc))
            if status == "VERIFIED":
                reason = ""
                finished = [r["finished_at"].replace(tzinfo=timezone.utc).timestamp()
                            for r in rows if r["finished_at"] is not None]
                observed = [r.observed.observed_at for r in results if r.observed is not None]
                if finished and (not observed or min(observed) < max(finished)):
                    reason = "STALE_EVIDENCE_REJECTED"
    except Exception as exc:  # observation unavailable is not approval evidence
        reason = "required effect verification unavailable: " + type(exc).__name__
    if reason:
        await svc.bus.emit("task.finalize_refused", task_id=task_id, run_id=authorized["run_id"],
                           reason=reason, override=True)
        return False
    async with svc.db.session() as s:
        t = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task_id))).first()
        if not t or t._mapping["status"] in ("completed", "cancelled"):
            return False
        result = await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id,
                                                        tasks_t.c.status == "waiting_approval").values(
            status="completed", updated_at=utcnow()))
        await s.commit()
        if result.rowcount != 1:
            return False
    await svc.bus.emit("task.finalized", task_id=task_id, run_id=approval.get("run_id"), override=True,
                       approval_id=approval.get("id"), decided_by=approval.get("decided_by"))
    await svc.bus.emit("task.completed", task_id=task_id, run_id=approval.get("run_id"), override=True)
    return True
