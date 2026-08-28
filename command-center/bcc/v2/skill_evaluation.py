"""V2.2 §7 — сравнение версий скилла и ОГРАНИЧЕННЫЕ переходы самообучения.

Зачем отдельный модуль. Таблица `evaluations` привязана к task/run и отвечает
на вопрос «как прошла эта задача». Здесь вопрос другой — «стала ли новая версия
скилла лучше предыдущей», и ключ поэтому версия, а не запуск. До появления
`skill_evaluations` Skill Evaluator'у некуда было писать сравнение, и вердикт
PROMOTE/REJECT опирался только на человека.

Что здесь ОГРАНИЧЕНО — и почему именно так:

  * вердиктов ровно три: PROMOTE, REJECT, HUMAN_REVIEW. Всё, что не проходит
    жёсткие условия PROMOTE или REJECT, уходит человеку, а не решается «на
    глаз». Недостаток данных — это не вердикт: строка честно висит в статусе
    `collecting`, пока не наберётся `MIN_RUNS` запусков С КАЖДОЙ стороны;
  * PROMOTE меняет РОВНО одно поле — `skills.current_version_id`. Он не правит
    файлы скиллов, не расширяет права и не трогает инструменты; откат — это
    обратный PROMOTE, версии никуда не деваются;
  * кандидат, просящий права или инструменты сверх baseline, НИКОГДА не
    получает автоматический PROMOTE: расширение прав в BOSSMAN проходит только
    через approvals, и здесь исключения нет — такой кандидат сразу
    HUMAN_REVIEW;
  * `stopped` не считается: это решение человека остановить задачу, а не
    результат скилла. Иначе один ручной стоп «топил» бы версию.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from ..db import (skill_evaluations as evals_t, skill_versions as skill_versions_t,
                  skills as skills_t, task_runs as runs_t, tasks as tasks_t, utcnow)

# Порог выборки: меньше — и «улучшение» становится шумом одного везучего прогона.
MIN_RUNS = 5
# Насколько доля успеха должна вырасти, чтобы это считалось улучшением, а не дрожью.
IMPROVE_DELTA = 0.10
REGRESS_DELTA = 0.10

PROMOTE = "promote"
REJECT = "reject"
HUMAN_REVIEW = "human_review"


async def version_metrics(svc, version_id: int) -> dict[str, Any]:
    """Факты по версии: сколько раз запускалась, сколько раз дошла до конца.

    Считаем по `task_runs`, а не по `tasks`: у задачи может быть несколько
    попыток, и версия отвечает за каждую.
    """
    async with svc.db.session() as s:
        rows = (await s.execute(
            sa.select(runs_t.c.status, runs_t.c.started_at, runs_t.c.finished_at)
            .select_from(runs_t.join(tasks_t, tasks_t.c.id == runs_t.c.task_id))
            .where(tasks_t.c.skill_version_id == version_id))).fetchall()

    completed = failed = 0
    durations: list[float] = []
    for row in rows:
        m = row._mapping
        if m["status"] == "completed":
            completed += 1
        elif m["status"] == "failed":
            failed += 1
        else:
            continue                                  # stopped/queued/running — не исход
        if m["started_at"] and m["finished_at"]:
            durations.append((m["finished_at"] - m["started_at"]).total_seconds() * 1000)

    total = completed + failed
    return {
        "version_id": version_id,
        "runs": total,
        "completed": completed,
        "failed": failed,
        "success_rate": round(completed / total, 4) if total else None,
        "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
    }


def _capabilities(version_row: dict[str, Any]) -> tuple[set[str], set[str]]:
    tools = {str(t) for t in (version_row.get("required_tools") or [])}
    perms_raw = version_row.get("permissions") or {}
    declared = perms_raw.get("declared") if isinstance(perms_raw, dict) else perms_raw
    perms = {str(p) for p in (declared or [])}
    return tools, perms


def widened_capabilities(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """Что кандидат просит СВЕРХ baseline. Пусто — значит не расширяет прав."""
    base_tools, base_perms = _capabilities(baseline)
    cand_tools, cand_perms = _capabilities(candidate)
    return sorted([f"tool:{t}" for t in cand_tools - base_tools]
                  + [f"permission:{p}" for p in cand_perms - base_perms])


def decide(baseline: dict[str, Any], candidate: dict[str, Any],
           widened: list[str]) -> tuple[str, str | None, str]:
    """(status, verdict, reason). Единственное место, где рождается вердикт."""
    if baseline["runs"] < MIN_RUNS or candidate["runs"] < MIN_RUNS:
        return ("collecting", None,
                f"данных мало: baseline {baseline['runs']}/{MIN_RUNS}, "
                f"кандидат {candidate['runs']}/{MIN_RUNS} завершённых запусков")

    if widened:
        return ("decided", HUMAN_REVIEW,
                "кандидат просит сверх baseline: " + ", ".join(widened)
                + " — расширение прав не применяется автоматически")

    delta = round(candidate["success_rate"] - baseline["success_rate"], 4)
    if delta >= IMPROVE_DELTA:
        return ("decided", PROMOTE,
                f"доля успеха выросла на {delta:+.2f} "
                f"({baseline['success_rate']:.2f} → {candidate['success_rate']:.2f}) "
                f"без расширения прав")
    if delta <= -REGRESS_DELTA:
        return ("decided", REJECT,
                f"доля успеха упала на {delta:+.2f} "
                f"({baseline['success_rate']:.2f} → {candidate['success_rate']:.2f})")
    return ("decided", HUMAN_REVIEW,
            f"разница в пределах шума ({delta:+.2f}) — автоматически не решаем")


async def _version_row(svc, version_id: int) -> dict[str, Any] | None:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(skill_versions_t)
                               .where(skill_versions_t.c.id == version_id))).first()
    return dict(row._mapping) if row else None


async def open_evaluation(svc, *, skill_id: int, baseline_version_id: int,
                          candidate_version_id: int) -> dict[str, Any]:
    """Завести сравнение пары версий (идемпотентно по паре)."""
    if baseline_version_id == candidate_version_id:
        raise ValueError("сравнивать версию саму с собой нечего")
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(evals_t).where(sa.and_(
            evals_t.c.baseline_version_id == baseline_version_id,
            evals_t.c.candidate_version_id == candidate_version_id)))).first()
        if row is not None:
            return dict(row._mapping)
        eid = int((await s.execute(sa.insert(evals_t).values(
            skill_id=skill_id, baseline_version_id=baseline_version_id,
            candidate_version_id=candidate_version_id, status="collecting",
            metrics={}, created_at=utcnow(), updated_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
        row = (await s.execute(sa.select(evals_t).where(evals_t.c.id == eid))).first()
    await svc.bus.emit("skill.evaluation.opened", evaluation_id=eid, skill_id=skill_id,
                       baseline_version_id=baseline_version_id,
                       candidate_version_id=candidate_version_id)
    return dict(row._mapping)


async def refresh(svc, evaluation_id: int) -> dict[str, Any]:
    """Пересчитать метрики и, если условия выполнены, зафиксировать вердикт.

    Уже решённое сравнение не переигрывается: вердикт — событие, а не мнение.
    """
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(evals_t)
                               .where(evals_t.c.id == evaluation_id))).first()
    if row is None:
        raise KeyError(evaluation_id)
    ev = dict(row._mapping)
    if ev["status"] == "decided":
        return ev

    base_row = await _version_row(svc, ev["baseline_version_id"])
    cand_row = await _version_row(svc, ev["candidate_version_id"])
    if base_row is None or cand_row is None:
        raise KeyError("версия сравнения исчезла")

    baseline = await version_metrics(svc, ev["baseline_version_id"])
    candidate = await version_metrics(svc, ev["candidate_version_id"])
    widened = widened_capabilities(base_row, cand_row)
    status, verdict, reason = decide(baseline, candidate, widened)

    metrics = {"baseline": baseline, "candidate": candidate, "widened": widened,
               "min_runs": MIN_RUNS, "improve_delta": IMPROVE_DELTA,
               "regress_delta": REGRESS_DELTA,
               "delta_success_rate": (
                   round(candidate["success_rate"] - baseline["success_rate"], 4)
                   if baseline["success_rate"] is not None
                   and candidate["success_rate"] is not None else None)}

    values: dict[str, Any] = {"status": status, "verdict": verdict, "reason": reason,
                              "metrics": metrics, "updated_at": utcnow()}
    approval_id = ev.get("approval_id")
    applied = bool(ev.get("applied"))

    if verdict == HUMAN_REVIEW and not approval_id:
        appr = await svc.approvals.create(
            kind="skill_promotion",
            preview=(f"Скилл #{ev['skill_id']}: версия {ev['candidate_version_id']} "
                     f"против {ev['baseline_version_id']}.\n{reason}\n"
                     f"baseline {baseline['completed']}/{baseline['runs']}, "
                     f"кандидат {candidate['completed']}/{candidate['runs']}.\n"
                     f"Одобрение переключит текущую версию скилла."))
        approval_id = int(appr.get("id"))
        values["approval_id"] = approval_id

    if verdict == PROMOTE:
        await _apply_promotion(svc, ev["skill_id"], ev["candidate_version_id"])
        values["applied"] = True
        values["decided_by"] = "runtime"
        applied = True
    elif verdict == REJECT:
        values["decided_by"] = "runtime"

    async with svc.db.session() as s:
        await s.execute(sa.update(evals_t).where(evals_t.c.id == evaluation_id).values(**values))
        await s.commit()
        row = (await s.execute(sa.select(evals_t).where(evals_t.c.id == evaluation_id))).first()

    if status == "decided":
        await svc.bus.emit("skill.evaluation.decided", evaluation_id=evaluation_id,
                           skill_id=ev["skill_id"], verdict=verdict, reason=reason,
                           applied=applied, approval_id=approval_id)
    return dict(row._mapping)


async def _apply_promotion(svc, skill_id: int, version_id: int) -> None:
    """Единственное, что делает PROMOTE. Ровно одно поле, обратимо."""
    async with svc.db.session() as s:
        await s.execute(sa.update(skills_t).where(skills_t.c.id == skill_id).values(
            current_version_id=version_id))
        await s.commit()
    await svc.bus.emit("skill.version.promoted", skill_id=skill_id, version_id=version_id)


async def apply_human_decision(svc, evaluation_id: int, *, approve: bool,
                               by: str = "owner") -> dict[str, Any]:
    """Решение человека по HUMAN_REVIEW. Только оно применяет спорный кандидат."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(evals_t)
                               .where(evals_t.c.id == evaluation_id))).first()
    if row is None:
        raise KeyError(evaluation_id)
    ev = dict(row._mapping)
    if ev["verdict"] != HUMAN_REVIEW:
        raise ValueError(f"решение человека применимо только к {HUMAN_REVIEW}, "
                         f"а вердикт «{ev['verdict']}»")
    if ev["applied"]:
        raise ValueError("это сравнение уже применено")

    values: dict[str, Any] = {"decided_by": str(by)[:120], "updated_at": utcnow()}
    if approve:
        await _apply_promotion(svc, ev["skill_id"], ev["candidate_version_id"])
        values["applied"] = True
        values["verdict"] = PROMOTE
        values["reason"] = f"{ev['reason']} → одобрено человеком ({by})"
    else:
        values["verdict"] = REJECT
        values["reason"] = f"{ev['reason']} → отклонено человеком ({by})"

    async with svc.db.session() as s:
        await s.execute(sa.update(evals_t).where(evals_t.c.id == evaluation_id).values(**values))
        await s.commit()
        row = (await s.execute(sa.select(evals_t).where(evals_t.c.id == evaluation_id))).first()
    await svc.bus.emit("skill.evaluation.decided", evaluation_id=evaluation_id,
                       skill_id=ev["skill_id"], verdict=values["verdict"],
                       reason=values["reason"], applied=bool(values.get("applied")),
                       approval_id=ev.get("approval_id"))
    return dict(row._mapping)


async def refresh_for_version(svc, version_id: int) -> list[dict[str, Any]]:
    """Пересчитать все НЕрешённые сравнения, где участвует эта версия."""
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(evals_t.c.id).where(sa.and_(
            evals_t.c.status == "collecting",
            sa.or_(evals_t.c.baseline_version_id == version_id,
                   evals_t.c.candidate_version_id == version_id))))).fetchall()
    return [await refresh(svc, int(r[0])) for r in rows]


__all__ = ["MIN_RUNS", "IMPROVE_DELTA", "REGRESS_DELTA", "PROMOTE", "REJECT", "HUMAN_REVIEW",
           "version_metrics", "widened_capabilities", "decide", "open_evaluation", "refresh",
           "refresh_for_version", "apply_human_decision"]
