"""Предпросмотр действия владельца ДО его выполнения, флаг OFF.

Кнопки владельца (docs/ux/OWNER_ACTION_MATRIX.md) действительно меняют состояние
на сервере, но узнать, ЧТО именно изменится, до сих пор можно было только
постфактум — свежим чтением после клика. Здесь ровно тот же вход («вид действия
+ цель») отвечает на вопрос «что произойдёт», не трогая ни одной строки.

Устройство:
  REGISTRY   — вид действия → функция, которая ЧИТАЕТ состояние и возвращает
               Preview: какие строки каких таблиц затронуты (таблица, id, поле,
               было → станет), какие файлы, сколько будет потрачено, обратимо ли.
  build()    — вернёт None, если для вида действия предпросмотра нет.

Два случая, которые нельзя путать (это главное различие модуля):
  changes == []    — предпросмотр ЕСТЬ и говорит: не изменится ни одна строка;
  changes == null  — предпросмотра НЕТ (available=false), и модуль честно об
                     этом говорит вместо того, чтобы выдать пустой список за
                     «изменений не будет».

Зарегистрированы только те действия, чьё изменение выводится чтением БД:
остановка задачи, решение по ожидающему подтверждению, удаление агента и
удаление расписания. Каскады при удалении не выписаны руками, а выведены из
схемы (ForeignKey.ondelete) — так предпросмотр не разъезжается с db.py при
добавлении новых таблиц. Оценок «сколько потратит запуск задачи» здесь нет:
такое число можно только выдумать, а выдуманное число хуже отсутствующего.

Предпросмотр не пишет в БД по построению: все сессии — только SELECT, ни одного
commit. Это проверяется тестом (снимок всех таблиц до и после), а не заявляется.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import agents as agents_t, approvals as approvals_t, fetch_one, metadata
from ..db import schedules as schedules_t, task_runs as runs_t, tasks as tasks_t
from . import Feature

FLAG = "BOSSMAN_ACTION_PREVIEW_ENABLED"
router = APIRouter()

# Причина «предпросмотра нет» — одна строка на весь модуль: клиент должен
# отличать её по флагу available, а не по разбору текста.
UNAVAILABLE = "предпросмотр для этого вида действия не зарегистрирован"

# Границы обхода каскадов: предпросмотр обязан отвечать быстро и не тащить в
# ответ тысячи строк. Упёрлись в границу — честно ставим truncated.
MAX_ROWS_PER_TABLE = 200
MAX_DEPTH = 3

# Поля, по которым строка узнаётся человеком. Значений полей строки в ответ не
# кладём (в providers лежит зашифрованный ключ) — только эта короткая подпись.
LABEL_COLUMNS = ("title", "name", "alias", "kind", "key")


class PreviewError(Exception):
    """Внятный отказ предпросмотра: несуществующая цель, недостающий параметр."""

    def __init__(self, message: str, status: int = 404, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.hint = hint


@dataclass
class Change:
    """Одно предстоящее изменение одной строки.

    after_known=False означает «значение станет известно в момент выполнения»
    (например, отметка времени). Это не то же самое, что after=None: None —
    настоящий NULL, который проставит ON DELETE SET NULL.
    """
    table: str
    row_id: Any
    op: str                     # update | delete | insert
    field: str                  # имя колонки; "*" — вся строка
    before: Any = None
    after: Any = None
    after_known: bool = True
    label: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {"table": self.table, "row_id": self.row_id, "op": self.op, "field": self.field,
                "before": self.before, "after": self.after, "after_known": self.after_known,
                "label": self.label, "note": self.note}


@dataclass
class Preview:
    """Ответ предпросмотра. changes=[] и «предпросмотра нет» — разные вещи:
    второе описывается отсутствием записи в REGISTRY, а не пустым Preview.

    spend_usd — ПРЯМЫЕ траты самого действия; косвенные последствия (движок
    продолжит разблокированную задачу и потратит на неё) в число не сводятся и
    перечислены в warnings словами. spend_known=False сказало бы «не знаю»;
    у зарегистрированных действий прямые траты равны нулю и это посчитано."""
    action: str
    summary: str
    changes: list[Change] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    spend_usd: float = 0.0
    spend_known: bool = True
    reversible: bool = True
    reversible_note: str = ""
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False

    def as_dict(self) -> dict:
        return {"available": True, "action": self.action, "summary": self.summary,
                "changes": [c.as_dict() for c in self.changes],
                "change_count": len(self.changes),
                "files": self.files, "spend_usd": self.spend_usd,
                "spend_known": self.spend_known, "reversible": self.reversible,
                "reversible_note": self.reversible_note, "warnings": self.warnings,
                "truncated": self.truncated}


Handler = Callable[[Any, int, dict], Awaitable[Preview]]
REGISTRY: dict[str, Handler] = {}


def register(action: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        REGISTRY[action] = fn
        return fn
    return deco


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


async def build(svc, action: str, target_id: int | None, params: dict) -> Preview | None:
    """Предпросмотр действия или None, если для этого вида его нет.

    None — единственный способ сказать «не знаю»: пустой Preview значил бы
    «ничего не изменится», а это утверждение, за которое модуль отвечает."""
    handler = REGISTRY.get(action)
    if handler is None:
        return None
    if target_id is None:
        raise PreviewError("нужен target_id: цель действия", status=400,
                           hint=f"{action} применяется к конкретной строке")
    return await handler(svc, int(target_id), params or {})


# ---------- каскады из схемы ----------

def _label(row: dict) -> str:
    for col in LABEL_COLUMNS:
        value = row.get(col)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    return ""


def _referencing(table_name: str) -> list[tuple[sa.Column, str]]:
    """Колонки других таблиц, ссылающиеся на <table>.id, с их ondelete.

    Читаем из metadata, а не из списка в голове: новая таблица в db.py
    автоматически попадает в предпросмотр удаления."""
    out: list[tuple[sa.Column, str]] = []
    for table in metadata.tables.values():
        for col in table.c:
            for fk in col.foreign_keys:
                target = fk.column
                if target.table.name == table_name and target.name == "id":
                    out.append((col, (fk.ondelete or "").upper()))
    out.sort(key=lambda pair: (pair[0].table.name, pair[0].name))
    return out


async def _plan_delete(s: AsyncSession, table: sa.Table, row_id: int, state: dict,
                       depth: int = 0) -> None:
    """Что реально сотрёт удаление строки: сама строка плюс то, что за ней
    потянет ON DELETE (CASCADE — удаление, SET NULL — обнуление ссылки).

    FK в SQLite включены прагмой (db.py), в Postgres действуют всегда, поэтому
    каскад — не предположение, а поведение движка."""
    key = (table.name, row_id)
    if key in state["seen"]:
        return
    state["seen"].add(key)

    row = await fetch_one(s, table, row_id)
    if row is None:
        if depth == 0:
            raise PreviewError(f"цель действия не найдена: {table.name}.id={row_id}")
        return
    state["changes"].append(Change(table=table.name, row_id=row_id, op="delete", field="*",
                                   label=_label(row), note="строка будет удалена"))

    for col, ondelete in _referencing(table.name):
        child = col.table
        if "id" not in child.c:
            state["warnings"].append(
                f"{child.name}.{col.name} ссылается на {table.name}, но у таблицы нет id — "
                f"её строки в предпросмотр не попали")
            state["truncated"] = True
            continue
        rows = (await s.execute(sa.select(child.c.id).where(col == row_id)
                                .order_by(child.c.id).limit(MAX_ROWS_PER_TABLE + 1))).fetchall()
        ids = [r[0] for r in rows]
        if len(ids) > MAX_ROWS_PER_TABLE:
            ids = ids[:MAX_ROWS_PER_TABLE]
            state["truncated"] = True
            state["warnings"].append(
                f"{child.name}: затронуто больше {MAX_ROWS_PER_TABLE} строк, показаны первые")
        if not ids:
            continue
        if ondelete == "CASCADE":
            if depth + 1 > MAX_DEPTH:
                state["truncated"] = True
                state["warnings"].append(
                    f"{child.name}: каскад глубже {MAX_DEPTH} уровней не раскрыт")
                continue
            for child_id in ids:
                await _plan_delete(s, child, int(child_id), state, depth + 1)
        elif ondelete == "SET NULL":
            for child_id in ids:
                state["changes"].append(Change(
                    table=child.name, row_id=int(child_id), op="update", field=col.name,
                    before=row_id, after=None, note="ссылка обнулится (ON DELETE SET NULL)"))
        else:
            state["warnings"].append(
                f"{child.name}.{col.name} ссылается на удаляемую строку без ON DELETE — "
                f"удаление может быть отвергнуто внешним ключом ({len(ids)} строк)")


async def _delete_preview(svc, table: sa.Table, row_id: int, action: str,
                          what: str) -> Preview:
    state: dict = {"changes": [], "warnings": [], "seen": set(), "truncated": False}
    async with svc.db.session() as s:
        await _plan_delete(s, table, row_id, state)
    return Preview(action=action, summary=f"удаление: {what} id={row_id}",
                   changes=state["changes"], warnings=state["warnings"],
                   truncated=state["truncated"], reversible=False,
                   reversible_note="удалённые строки не восстанавливаются: "
                                   "вернуть их можно только откатом снапшота")


# ---------- зарегистрированные действия ----------

@register("task.stop")
async def _preview_task_stop(svc, task_id: int, params: dict) -> Preview:
    """POST /api/tasks/{id}/stop — Engine.stop: статус задачи, гашение
    непринятых run'ов, отмена активных."""
    async with svc.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id)
        if task is None:
            raise PreviewError(f"цель действия не найдена: tasks.id={task_id}")
        rows = (await s.execute(sa.select(runs_t.c.id, runs_t.c.status)
                                .where(runs_t.c.task_id == task_id)
                                .order_by(runs_t.c.id))).fetchall()

    changes: list[Change] = []
    warnings: list[str] = []
    if task["status"] != "stopped":
        changes.append(Change(table="tasks", row_id=task_id, op="update", field="status",
                              before=task["status"], after="stopped", label=_label(task)))
    else:
        warnings.append("задача уже остановлена: статус не изменится")
    # updated_at движок пишет всегда, даже когда статус тот же — умолчать об этом
    # значило бы обещать «строка не тронута», а строка тронута.
    changes.append(Change(table="tasks", row_id=task_id, op="update", field="updated_at",
                          before=task["updated_at"], after=None, after_known=False,
                          note="проставится момент выполнения действия"))

    for run in rows:
        rid, status = int(run[0]), run[1]
        if status == "queued":
            changes.append(Change(table="task_runs", row_id=rid, op="update", field="status",
                                  before="queued", after="stopped",
                                  note="ещё не начатый run гасится сразу"))
            changes.append(Change(table="task_runs", row_id=rid, op="update",
                                  field="finished_at", before=None, after=None,
                                  after_known=False,
                                  note="проставится момент выполнения действия"))
        elif status in ("leased", "running"):
            warnings.append(
                f"run {rid} выполняется: он будет отменён, но конечный статус зависит от "
                f"того, на каком шаге прервётся исполнение — предсказать его чтением нельзя")

    return Preview(action="task.stop", summary=f"остановка задачи id={task_id}",
                   changes=changes, warnings=warnings, reversible=True,
                   reversible_note="ничего не удаляется: задачу можно запустить снова "
                                   "(run/retry/resume)")


@register("approval.decide")
async def _preview_approval_decide(svc, approval_id: int, params: dict) -> Preview:
    """POST /api/approvals/{id} — Approvals.decide: решение принимается один раз,
    UPDATE ограничен status=pending, поэтому у уже решённого не меняется ничего."""
    if "approve" not in params:
        raise PreviewError("нужен параметр approve: true (разрешить) или false (отклонить)",
                           status=400)
    approve = bool(params["approve"])
    by = str(params.get("by") or "owner")[:120]

    async with svc.db.session() as s:
        row = await fetch_one(s, approvals_t, approval_id)
    if row is None:
        raise PreviewError(f"цель действия не найдена: approvals.id={approval_id}")

    verb = "разрешить" if approve else "отклонить"
    if row["status"] != "pending":
        # Не изменится ровно ничего — и это утверждение, а не «не знаю».
        return Preview(
            action="approval.decide",
            summary=f"{verb} подтверждение id={approval_id}: решение уже принято",
            changes=[], reversible=True,
            reversible_note="нечего отменять: ни одна строка не будет затронута",
            warnings=[f"статус подтверждения — {row['status']}, а UPDATE ограничен pending: "
                      f"повторное решение не переигрывает принятое"])

    status = "approved" if approve else "rejected"
    changes = [
        Change(table="approvals", row_id=approval_id, op="update", field="status",
               before="pending", after=status, label=_label(row)),
        Change(table="approvals", row_id=approval_id, op="update", field="decided_by",
               before=row["decided_by"], after=by),
        Change(table="approvals", row_id=approval_id, op="update", field="decided_at",
               before=row["decided_at"], after=None, after_known=False,
               note="проставится момент решения"),
    ]
    warnings = []
    if approve and row["task_id"]:
        warnings.append(
            f"это решение разблокирует задачу {row['task_id']}: движок продолжит её сам — "
            f"последствия этого продолжения предпросмотром не считаются")
    return Preview(action="approval.decide",
                   summary=f"{verb} подтверждение id={approval_id} (kind={row['kind']})",
                   changes=changes, warnings=warnings, reversible=False,
                   reversible_note="решение принимается один раз: повторный вызов не "
                                   "переигрывает его")


@register("agent.delete")
async def _preview_agent_delete(svc, agent_id: int, params: dict) -> Preview:
    """DELETE /api/agents/{id} — строка агента плюс всё, что за ней тянет схема."""
    return await _delete_preview(svc, agents_t, agent_id, "agent.delete", "агент")


@register("schedule.delete")
async def _preview_schedule_delete(svc, schedule_id: int, params: dict) -> Preview:
    """DELETE /api/schedules/{id} — строка расписания плюс каскады схемы."""
    return await _delete_preview(svc, schedules_t, schedule_id, "schedule.delete", "расписание")


# ---------- ручки ----------

class PreviewIn(BaseModel):
    action: str
    target_id: int | None = None
    params: dict = Field(default_factory=dict)


@router.post("/preview")
async def preview(body: PreviewIn, request: Request):
    """Что произойдёт, если выполнить это действие. Ничего не меняет.

    Ручка читающая, но заведена под флагом: при выключенном флаге поведение
    приложения обязано быть ровно таким, как до модуля."""
    if not enabled():
        return {"enabled": False}
    try:
        result = await build(request.app.state.svc, body.action, body.target_id, body.params)
    except PreviewError as exc:
        raise HTTPException(exc.status, {"message": exc.message, "hint": exc.hint}) from exc
    if result is None:
        # changes=null, а не []: «не знаю» не притворяется «ничего не изменится».
        return {"enabled": True, "available": False, "action": body.action,
                "reason": UNAVAILABLE, "changes": None, "change_count": None,
                "known_actions": sorted(REGISTRY)}
    return {"enabled": True, **result.as_dict()}


@router.get("/preview/actions")
async def preview_actions():
    """Виды действий с предпросмотром. Молчание тут читалось бы как «предпросмотр
    есть для всего», поэтому список отдаётся явно."""
    return {"enabled": enabled(), "actions": sorted(REGISTRY)}


FEATURE = Feature(name="action_preview", router=router)
