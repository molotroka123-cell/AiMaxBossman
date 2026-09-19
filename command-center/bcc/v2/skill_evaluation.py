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

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Mapping

import sqlalchemy as sa

from bossman_shared.objective_activation import (ActivationDecision, ActivationError,
                                                 BroadActivationGate,
                                                 cohort_reports_from_facts)
from bossman_shared.objective_canary import (FAILED, PENDING, CanaryError,
                                             CanaryPlan, CanaryPolicy,
                                             CanaryVerdict)
from bossman_shared.objective_store import ObjectiveStore, ObjectiveStoreError

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
        cutoff = (await s.execute(sa.select(sa.func.max(runs_t.c.id)))).scalar() or 0
        eid = int((await s.execute(sa.insert(evals_t).values(
            skill_id=skill_id, baseline_version_id=baseline_version_id,
            candidate_version_id=candidate_version_id, status="collecting",
            metrics={"canary": {"cutoff_run_id": int(cutoff)}},
            created_at=utcnow(), updated_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
        row = (await s.execute(sa.select(evals_t).where(evals_t.c.id == eid))).first()
    # ИСТОРИЧЕСКОГО ЗАПОЛНЕНИЯ ЗДЕСЬ НЕТ, И ЭТО НАМЕРЕННО.
    #
    # Подписать задним числом старые строки `task_runs` — значит выдать доверенную
    # канареечную улику за факты, которые никто не наблюдал как канарейку.
    # Полномочие снова стало бы выводимым из общей истории задач, просто на шаг
    # раньше. Вместо этого замораживается ОТСЕЧКА: членами когорты могут стать
    # только прогоны кандидата, завершившиеся ПОСЛЕ заведения сравнения. Всё, что
    # было до, — история, а не канарейка.
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

    # Цифры — ещё не разрешение. Широкая активация проходит через канареечную
    # дверь, и отказ двери НЕ превращается в тихий PROMOTE: он опускает вердикт
    # до человека вместе с машиночитаемой причиной.
    gate = None
    grant = None
    canary_run_id = ""
    canary: dict[str, Any] | None = None
    if verdict == PROMOTE:
        gate, canary_run_id, decision = await canary_decision(svc, ev, cand_row)
        canary = _canary_facts(decision, canary_run_id, _cutoff_of(ev))
        if not decision.allowed:
            verdict = HUMAN_REVIEW
            reason = (f"{reason}; широкая активация закрыта канареечной дверью: "
                      f"{decision.reason}")
            gate = None
        else:
            grant = decision.grant

    canary = canary or {"cutoff_run_id": _cutoff_of(ev)}
    metrics = {"baseline": baseline, "candidate": candidate, "widened": widened,
               "min_runs": MIN_RUNS, "improve_delta": IMPROVE_DELTA,
               "regress_delta": REGRESS_DELTA, "canary": canary,
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
        await _apply_promotion(svc, ev["skill_id"], ev["candidate_version_id"],
                               gate=gate, run_id=canary_run_id, grant=grant)
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


# ---------- P0-1: канареечная дверь перед широкой активацией ----------
#
# `_apply_promotion` переключает `skills.current_version_id`, а колонки когорты в
# схеме нет: кандидат достаётся ВСЕМУ парку сразу. Это и есть настоящая широкая
# активация, и до сих пор она шла мимо `authorize_broad_activation` — гейт был
# написан, привязан и покрыт тестами, но ни одного производственного вызывающего
# не имел. Теперь единственный путь к переключению лежит через дверь.
#
# Канареечное окно — ВСЕ терминальные прогоны кандидата. Раньше брались первые
# `CANARY_WINDOW`, и это была дыра: кандидат «шесть успехов, потом четыре
# падения» судился по здоровому префиксу, все члены когорты отчитывались
# здоровыми, и версия с 40% падений уезжала на весь парк. Деградация после
# первых прогонов обязана быть видна двери. Когорта выбирается из
# окна детерминированно (`plan_canary`), каждый её член обязан отчитаться
# ПРИВЯЗАННОЙ уликой, и правила примитива действуют дословно: молчание — не
# успех, одно падение закрывает выпуск, неразрешимая улика закрывает выпуск.

CANARY_WINDOW = MIN_RUNS
# Личность СЛУЖБЫ, а не операционного процесса: перезапуск того же деплоя обязан
# доверять собственным долговечным уликам, а чужая служба — нет.
PROMOTION_IDENTITY = os.environ.get("BCC_PROMOTION_IDENTITY", "bcc.skill-promotion")
CANARY_STORE_ENV = "BCC_CANARY_STORE"
CANARY_KEY_FILE = "v5_canary_evidence.key"
_EPOCH = datetime(1970, 1, 1)


def _epoch(moment: datetime) -> float:
    """Наивный UTC в секунды. Без часового пояса — значение не «плывёт» между
    процессами, а улика судится по времени, а не по настроению машины."""
    return (moment - _EPOCH).total_seconds()


def _canary_store_path(svc) -> Path:
    override = os.environ.get(CANARY_STORE_ENV)
    if override:
        return Path(override).expanduser()
    return Path(svc.settings.data_dir) / "v5_canary.sqlite3"


def _evidence_key(svc) -> bytes:
    """Ключ улик живёт в data_dir рядом с базой и переживает перезапуск.

    Без долговечного ключа улика, выпущенная до перезапуска, после него не
    разрешится, и служба перестанет доверять собственным фактам. Ключ создаётся
    один раз, режимом 0600, и никогда не перевыпускается молча.
    """
    path = Path(svc.settings.data_dir) / CANARY_KEY_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, os.urandom(32))
            finally:
                os.close(fd)
        except FileExistsError:
            pass                                      # другой процесс успел раньше
    key = path.read_bytes()
    if len(key) < 16:
        raise ActivationError("canary evidence key is too short to be trusted")
    return key


def _gate(svc) -> BroadActivationGate:
    return BroadActivationGate(ObjectiveStore(_canary_store_path(svc)),
                               evidence_key=_evidence_key(svc),
                               process_identity=PROMOTION_IDENTITY,
                               owner_id=str(getattr(svc, "owner_id", None) or "owner"))


def candidate_revision(version_row: dict[str, Any]) -> str:
    """Личность РЕВИЗИИ: содержимое версии, а не её номер.

    Правка строки версии (инструменты, права, отпечаток) меняет ревизию,
    поэтому улика о прежнем содержимом эту активацию не открывает.
    """
    tools, perms = _capabilities(version_row)
    body = json.dumps({"id": int(version_row["id"]), "version": version_row.get("version"),
                       "tools": sorted(tools), "permissions": sorted(perms),
                       "fingerprint": (version_row.get("permissions") or {}).get("fingerprint")
                       if isinstance(version_row.get("permissions"), dict) else None},
                      sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _cutoff_of(ev: Mapping[str, Any]) -> int:
    """Отсечка когорты, замороженная при заведении сравнения."""
    metrics = ev.get("metrics")
    canary = metrics.get("canary") if isinstance(metrics, Mapping) else None
    if isinstance(canary, Mapping):
        try:
            return int(canary.get("cutoff_run_id") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


async def canary_window(svc, version_id: int, *, after: int = 0
                        ) -> list[tuple[str, bool | None]]:
    """ПРОСПЕКТИВНЫЕ терминальные прогоны версии: (член когорты, здоров ли).

    `after` — отсечка: прогоны с меньшим идентификатором завершились ДО того, как
    когорта была заморожена, и потому канареечной властью быть не могут. Это и
    закрывает подпись истории задним числом.

    `stopped`/`queued`/`running` сюда не попадают: это не исход. Член без исхода —
    молчание, и дверь на нём закрыта.
    """
    async with svc.db.session() as s:
        rows = (await s.execute(
            sa.select(runs_t.c.id, runs_t.c.status)
            .select_from(runs_t.join(tasks_t, tasks_t.c.id == runs_t.c.task_id))
            .where(sa.and_(tasks_t.c.skill_version_id == version_id,
                           runs_t.c.id > int(after)))
            .order_by(runs_t.c.id))).fetchall()
    # ЧЛЕНСТВО — ПО ПОЗИЦИИ, А НЕ ПО ИСХОДУ. Ещё не завершившийся прогон остаётся
    # членом когорты со здоровьем None: это МОЛЧАНИЕ, и дверь на нём закрыта.
    # Если бы незавершённый член просто пропускался, когорта добиралась бы
    # следующими прогонами, и «ждём исход» незаметно превратилось бы в «возьмём
    # тех, кто уже ответил» — то есть снова в выбор здоровых.
    out: list[tuple[str, bool | None]] = []
    for rid, status in rows:
        text = str(status)
        healthy = True if text == "completed" else (False if text == "failed" else None)
        out.append((f"run:{int(rid)}", healthy))
    return out


# МЕСТА В КОГОРТЕ, А НЕ ИМЕНА ПРОГОНОВ.
#
# Когорта обязана быть заморожена ДО того, как её члены отчитаются. Если бы
# членство звалось `run:<id>`, состав был бы известен только после того, как
# нужные прогоны уже завершились: писатель на третьем прогоне и решение на
# пятом строили бы РАЗНЫЕ планы, а значит и разные `run_id`, и отчёты писателя
# оказывались бы уликой чужого прогона. Места же известны в момент заведения
# сравнения, поэтому `run_id` неизменен с самого начала: каждый прогон
# отчитывается о СВОЁМ месте в свой терминальный момент, а место без отчёта —
# это молчание, которое ничем нельзя добрать задним числом.
CANARY_SLOTS = tuple(f"slot:{i}" for i in range(1, CANARY_WINDOW + 1))


def _slot_of(position: int) -> str:
    """Место когорты по позиции прогона после отсечки (1-based)."""
    return f"slot:{int(position)}"


def _cohort_plan(gate: BroadActivationGate, ev: Mapping[str, Any],
                 cand_row: Mapping[str, Any]) -> CanaryPlan:
    """ОДИН план для писателя и для решения.

    Он не зависит от того, какие прогоны уже случились: население — места,
    а не прогоны. Поэтому писатель и решение всегда находят один и тот же
    долговечный прогон.
    """
    # КОГОРТА — ВЕСЬ ИЗМЕРЕННЫЙ НАБОР, а не выборка из него. Доля по умолчанию
    # взяла бы три места из пяти, и молчание двух оставшихся было бы невидимо:
    # «мы туда не смотрели» превратилось бы в «претензий нет». При нулевом
    # допуске отвечать обязаны все места окна.
    return gate.plan(subject=f"skill:{int(ev['skill_id'])}",
                     revision=candidate_revision(cand_row),
                     members=list(CANARY_SLOTS),
                     started_at=_epoch(ev.get("created_at") or utcnow()) - 1.0,
                     policy=CanaryPolicy(min_cohort=CANARY_WINDOW,
                                         max_cohort=CANARY_WINDOW),
                     run_nonce=f"skill-eval:{int(ev['id'])}")


async def record_canary_outcome(svc, version_id: int, run_id: int, status: str) -> None:
    """Записать канареечную улику ОДИН РАЗ — когда член когорты стал терминальным.

    Единственное место, где рождается канареечное здоровье. Решение потом только
    ЧИТАЕТ эти долговечные отчёты. Молчание не успех: место без исхода не
    получает отчёта, и дверь на нём закрыта.
    """
    if str(status) not in ("completed", "failed"):
        return
    healthy = str(status) == "completed"
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(evals_t).where(sa.and_(
            evals_t.c.status == "collecting",
            evals_t.c.candidate_version_id == version_id)))).fetchall()
    for row in rows:
        await _record_member_outcome(svc, dict(row._mapping), int(run_id), healthy)


async def _record_member_outcome(svc, ev: dict[str, Any], run_id: int,
                                 healthy: bool) -> None:
    """Единственный писатель канареечной улики. Идемпотентен по месту когорты."""
    cand_row = await _version_row(svc, int(ev["candidate_version_id"]))
    if cand_row is None:
        return
    member = ""
    try:
        window = await canary_window(svc, int(ev["candidate_version_id"]),
                                     after=_cutoff_of(ev))
        names = [m for m, _ in window]
        try:
            position = names.index(f"run:{int(run_id)}") + 1
        except ValueError:
            return                                    # прогон до отсечки — не член
        if position > CANARY_WINDOW:
            return                                    # за пределами когорты
        member = _slot_of(position)
        gate = _gate(svc)
        plan = _cohort_plan(gate, ev, cand_row)
        if member not in plan.cohort:
            return
        gate.open(plan, candidate=candidate_revision(cand_row))
        if member in gate.reported_members(plan.run_id):
            return                                    # ровно один раз
        at = _epoch(utcnow())
        if healthy:
            gate.report_healthy(plan.run_id, member, at=at,
                                detail=f"terminal outcome: completed (run:{int(run_id)})")
        else:
            gate.report_unhealthy(plan.run_id, member, at=at,
                                  detail=f"terminal outcome: failed (run:{int(run_id)})")
    except (ActivationError, CanaryError, ObjectiveStoreError) as exc:
        await svc.bus.emit("skill.canary.error",
                           evaluation_id=int(ev.get("id") or 0),
                           member=member or f"run:{int(run_id)}", error=str(exc)[:300])


async def canary_decision(svc, ev: dict[str, Any], candidate_row: dict[str, Any]
                          ) -> tuple[BroadActivationGate | None, str, ActivationDecision]:
    """Пройти дверь для ЭТОЙ пары (скилл, версия-кандидат).

    Прогон детерминирован: он строится из идентификатора сравнения и времени его
    заведения, поэтому пересчёт находит ТОТ ЖЕ прогон и те же долговечные
    отчёты, а перезапуск процесса ничего не теряет и ничего не обнуляет.
    """
    subject = f"skill:{int(ev['skill_id'])}"
    revision = candidate_revision(candidate_row)
    window = await canary_window(svc, int(ev["candidate_version_id"]),
                                 after=_cutoff_of(ev))
    started = ev.get("created_at") or utcnow()
    now = _epoch(utcnow())
    # Места когорты — РОВНО первые MIN_RUNS проспективных позиций после отсечки.
    # Позиция занята прогоном по порядку идентификаторов; незанятое место —
    # молчание. Именно поэтому население плана — места, а не прогоны: состав
    # заморожен при заведении сравнения, до того как хоть кто-то отчитался,
    # и писатель улики строит ТОТ ЖЕ план, что и это решение.
    occupied = [(_slot_of(i + 1), healthy)
                for i, (_member, healthy) in enumerate(window[:CANARY_WINDOW])]
    try:
        gate = _gate(svc)
        plan = _cohort_plan(gate, ev, candidate_row)
    except (ActivationError, CanaryError) as exc:
        # Окна нет или оно короче когорты — активация не разрешена. Мало данных
        # это не «претензий нет», это «не проверено».
        return None, "", ActivationDecision("", False, f"canary_unavailable:{exc}")

    # Прогон, о котором это сравнение УЖЕ отчиталось. Если он записан, а в
    # долговечном хранилище его нет, значит состояние потеряно между запусками:
    # заводить его заново здесь означало бы выдать «проверено» за «перепроверю
    # по тем же фактам». Дверь на потерянном прогоне закрыта.
    recorded = _recorded_canary_run(ev)
    try:
        if recorded:
            if recorded != plan.run_id:
                return None, "", ActivationDecision(
                    "", False, f"canary_run_mismatch:{recorded}")
            gate.state(plan.run_id)                   # бросит, если строки нет
        # Прогон заводится (идемпотентно), но отчёты СЮДА не пишутся. Улика
        # рождается один раз — когда член когорты достиг терминального исхода
        # (`record_canary_outcome`). Выводить здоровье из общей истории задач в
        # момент решения нельзя: тогда процесс, потерявший долговечное
        # состояние, «воскресил» бы успех теми же фактами, и канареечная улика
        # перестала бы быть властью. Прогон без отчётов — МОЛЧАНИЕ, не успех.
        gate.open(plan, candidate=revision)
    except (ActivationError, CanaryError, ObjectiveStoreError) as exc:
        return None, "", ActivationDecision(plan.run_id, False,
                                            f"canary_unavailable:{exc}")
    # ВЕТО ПО ИЗВЕСТНОМУ ПРОВАЛУ КАНДИДАТА.
    #
    # Когорта — первые пятеро, поэтому провал шестого раньше был невидим:
    # «шесть успехов, потом четыре падения» проезжал дверь, потому что выбранные
    # пятеро здоровы. Это атака на здоровый префикс. Провал кандидата, известный
    # ДО широкой активации, закрывает выпуск независимо от состава когорты:
    # «мы туда не смотрели» — не то же самое, что «там всё хорошо».
    # МОЛЧАНИЕ ЧЛЕНА КОГОРТЫ. Улика, разошедшаяся с авторитетной строкой прогона,
    # устарела: член, который ПРЯМО СЕЙЧАС не дошёл до исхода, молчит, даже если
    # про него лежит более ранний отчёт «здоров». Проверяется применимость улики,
    # а не выводится здоровье: сам вердикт по-прежнему собирается из долговечных
    # отчётов, просто неприменимая улика не открывает дверь.
    cohort_set = set(plan.cohort)
    # ПОЛНЫЙ ИЗМЕРЕННЫЙ НАБОР + НУЛЕВОЙ ДОПУСК (политика владельца на заморозку).
    #
    # Молчит не только незанятое место когорты: молчит ЛЮБОЙ прогон измеренного
    # набора, не дошедший до исхода. Ограничивать проверку когортой значило бы
    # мерить продвижение по первым пятерым и не смотреть на остальных, а «мы туда
    # не смотрели» — не то же самое, что «там всё хорошо». Статистически
    # выборочная канарейка может вернуться после заморозки; здесь отвечают все.
    seen = dict(occupied)
    silent = tuple(sorted(
        set(m for m in cohort_set if seen.get(m) is None)
        | set(m for m, healthy in window if healthy is None)))
    if silent:
        verdict = CanaryVerdict(
            state=PENDING, reason="canary_incomplete",
            revision_digest=plan.revision_digest,
            reported=tuple(m for m, _ in occupied), silent=silent, attested=True)
        return gate, plan.run_id, ActivationDecision(
            plan.run_id, False, f"canary_incomplete:{','.join(silent)}", verdict)
    failed = tuple(sorted(m for m, healthy in window if healthy is False))
    if failed:
        # Вердикт СОБИРАЕТСЯ здесь, а не берётся из хранилища: это отчёт двери о
        # собственном отказе, а не подделанная улика. Ledger не переписывается —
        # провал кандидата, известный по авторитетной строке прогона, просто
        # закрывает выпуск, даже если про него уже лежит более ранняя улика
        # «здоров»: улика, разошедшаяся с исходом прогона, устарела, а устаревшая
        # улика не полномочие.
        verdict = CanaryVerdict(
            state=FAILED, reason="canary_candidate_failed",
            revision_digest=plan.revision_digest,
            reported=tuple(m for m, _ in occupied), unhealthy=failed, attested=True)
        return gate, plan.run_id, ActivationDecision(
            plan.run_id, False, f"canary_failed:{','.join(failed)}", verdict)
    return gate, plan.run_id, gate.authorize(plan.run_id, now=now)


def _recorded_canary_run(ev: Mapping[str, Any]) -> str:
    """Идентификатор канареечного прогона, записанный прошлым пересчётом.

    Он живёт в метриках сравнения и потому переживает перезапуск процесса
    вместе с самим сравнением. Его расхождение с долговечным хранилищем —
    признак потери состояния, а не повод завести прогон заново.
    """
    metrics = ev.get("metrics")
    if not isinstance(metrics, Mapping):
        return ""
    canary = metrics.get("canary")
    if not isinstance(canary, Mapping):
        return ""
    return str(canary.get("run_id") or "")


def _canary_facts(decision: ActivationDecision, run_id: str,
                  cutoff_run_id: int = 0) -> dict[str, Any]:
    verdict = decision.verdict
    return {"cutoff_run_id": int(cutoff_run_id), "run_id": run_id, "allowed": decision.allowed, "reason": decision.reason,
            "state": getattr(verdict, "state", None),
            "unhealthy": list(getattr(verdict, "unhealthy", ()) or ()),
            "silent": list(getattr(verdict, "silent", ()) or ()),
            "unattested": [list(pair) for pair in getattr(verdict, "unattested", ()) or ()]}


async def _apply_promotion(svc, skill_id: int, version_id: int, *,
                           gate: BroadActivationGate | None, run_id: str,
                           grant: Any) -> None:
    """Единственное, что делает PROMOTE. Ровно одно поле — и только по разрешению.

    Разрешение расходуется ПЕРЕД переключением: закрытая строка прогона без
    переключения — несостоявшаяся активация, а переключение без закрытой строки
    было бы активацией, о которой никто не узнает.
    """
    if gate is None:
        raise ActivationError("broad activation requires a canary gate")
    plan = gate.consume(run_id, grant, now=_epoch(utcnow()),
                        detail=f"skill:{skill_id} version:{version_id}")
    async with svc.db.session() as s:
        await s.execute(sa.update(skills_t).where(skills_t.c.id == skill_id).values(
            current_version_id=version_id))
        await s.commit()
    await svc.bus.emit("skill.version.promoted", skill_id=skill_id, version_id=version_id,
                       canary_run_id=run_id, cohort=list(plan.cohort))


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
        # Человек решает СПОРНОЕ, но не отменяет канарейку: одобрение владельца
        # не делает непроверенного кандидата проверенным. Дверь одна и здесь.
        cand_row = await _version_row(svc, ev["candidate_version_id"])
        if cand_row is None:
            raise KeyError("версия сравнения исчезла")
        gate, run_id, decision = await canary_decision(svc, ev, cand_row)
        if not decision.allowed:
            # ОДОБРЕНИЕ ЧЕЛОВЕКА НЕ ДЕЛАЕТ НЕПРОВЕРЕННОГО ПРОВЕРЕННЫМ.
            #
            # Человек полномочен в спорном измерении — «цифры в пределах шума,
            # брать ли». Он не полномочен объявить здоровым член когорты,
            # который упал, промолчал или не оставил улики: это вопрос факта, а
            # не воли. Поэтому отказ двери здесь — не исключение, оборвавшее
            # вызов, а ЗАПИСАННЫЙ исход: решение человека фиксируется, продвижение
            # не происходит, и причина отказа остаётся в сравнении, чтобы
            # владелец видел основание, а не только слово «нет».
            values["verdict"] = HUMAN_REVIEW
            values["applied"] = False
            values["reason"] = (f"{ev['reason']} → одобрено человеком ({by}), но широкая "
                                f"активация закрыта канареечной дверью: {decision.reason}")
            metrics = dict(ev.get("metrics") or {})
            metrics["canary"] = _canary_facts(decision, run_id, _cutoff_of(ev))
            values["metrics"] = metrics
        else:
            await _apply_promotion(svc, ev["skill_id"], ev["candidate_version_id"],
                                   gate=gate, run_id=run_id, grant=decision.grant)
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


__all__ = ["record_canary_outcome", "MIN_RUNS", "IMPROVE_DELTA", "REGRESS_DELTA", "PROMOTE", "REJECT", "HUMAN_REVIEW",
           "CANARY_WINDOW", "PROMOTION_IDENTITY", "version_metrics", "widened_capabilities",
           "decide", "open_evaluation", "refresh", "refresh_for_version",
           "apply_human_decision", "candidate_revision", "canary_window", "canary_decision"]
