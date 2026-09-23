"""Шина событий: подписчики WS + история в таблице events (лента активности).

Живой поток получает всё; в БД кладутся только «содержательные» события —
метрики и построчный лог run'а идут своими таблицами (system_metrics, run_events),
дублировать их в ленте активности бессмысленно.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import sqlalchemy as sa

from .db import Database, events as events_t, rows_dicts, run_events as run_events_t, utcnow
from .plugin_security import redact
from .trace import get_trace_id

#: Bossman 1.2 (terminal): what ONE model step and ONE tool call actually
#: produced — the provider's reasoning (only when it sent any), the step's text,
#: its usage, and a tool call's arguments/result preview. Observation only.
#: They are NOT pushed to the web UI's /api/events feed nor listed in
#: /api/activity (those panels do not render them); they reach the per-task
#: history (/api/tasks/{id}/events) and stream (/api/events/stream) that the
#: terminal client and Claude Code read. Redaction applies to all of them:
#: every emit goes through `redact(..., scrub_text=True)` below.
STREAM_ONLY = frozenset({
    "run.reasoning_delta", "run.assistant_delta", "run.assistant_message",
    "run.usage", "run.tool_use", "run.tool_result",
})
#: The model's reasoning is shown live and never stored: no durable copy of a
#: chain of thought, so a replayed task history has none (honestly absent).
NOT_PERSISTED_STREAM = frozenset({"run.reasoning_delta"})

# эти виды не пишем в историю: у них есть свои таблицы и своя частота
TRANSIENT = {"system.metrics", "run.log", *NOT_PERSISTED_STREAM}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TaskStreamFilter:
    """Which bus events belong to ONE task (or run) — for /api/events/stream.

    Many events name only the run (`run.log`, `checkpoint.created`) or only the
    approval (`approval.decided`). The filter learns the task's run ids and
    approval ids from events that carry both, so everything about this task
    reaches its stream and nothing about another task does. Pure: no I/O."""

    def __init__(self, task_id: int | None = None, run_id: int | None = None):
        self.task_id = task_id
        self.run_ids: set[int] = {run_id} if run_id is not None else set()
        self.approval_ids: set[int] = set()

    def accept(self, msg: dict) -> bool:
        kind = str(msg.get("kind") or "")
        if kind in ("system.metrics", "hello"):
            return False
        tid = _as_int(msg.get("task_id"))
        rid = _as_int(msg.get("run_id"))
        mine = False
        if self.task_id is not None and tid is not None:
            # task_id названа явно: она решает, даже если run_id совпал бы случайно
            mine = tid == self.task_id
        elif rid is not None:
            mine = rid in self.run_ids
        elif kind.startswith("approval.") and _as_int(msg.get("id")) is not None:
            mine = _as_int(msg.get("id")) in self.approval_ids
        if not mine:
            return False
        if rid is not None:
            self.run_ids.add(rid)
        if kind == "approval.created" and _as_int(msg.get("id")) is not None:
            self.approval_ids.add(_as_int(msg.get("id")))
        return True


class EventBus:
    def __init__(self, db: Database | None = None):
        self.db = db
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def is_subscribed(self, q: asyncio.Queue) -> bool:
        """False once `publish` dropped a lagging queue: its reader must say so
        (and reconnect) instead of waiting forever on a queue nobody fills."""
        return q in self._subscribers

    async def emit(self, kind: str, /, **data: Any) -> dict:
        # kind — только позиционный: в data встречаются свои поля с именем kind
        """Разослать событие подписчикам и (если оно содержательное) записать в историю."""
        # Секреты не попадают ни в таблицу events, ни в WS-ленту — ДО персиста и
        # ДО broadcast, чтобы оба пути видели одно и то же. Чистки по ИМЕНАМ
        # ключей для этого мало: ключ владельца приезжает внутри ЗНАЧЕНИЯ
        # (`preview` команды на подтверждение, `message` строки лога), где имя
        # поля безобидно. BL-099: он доходил и до WS-ленты, и до таблицы events.
        data = redact(data, scrub_text=True)
        # Время события считается ОДИН раз. Раньше utcnow() вызывался дважды —
        # отдельно для рассылки и отдельно для записи в историю, — и одно и то
        # же событие приходило с разным временем в живой ленте и в /activity.
        # Расхождение видно владельцу, а сверить два пути между собой (чтобы не
        # считать событие дважды) при разном времени вообще невозможно.
        now = utcnow()
        # TRUTH-003 §14: trace_id из контекста исполнения, если вызывающий не передал свой
        if "trace_id" not in data:
            tid = get_trace_id()
            if tid:
                data["trace_id"] = tid
        # kind/ts всегда наши: поле данных с тем же именем не должно подменять вид события
        msg = {**data, "kind": kind, "ts": now.isoformat()}
        if self.db is not None and kind not in TRANSIENT:
            try:
                async with self.db.session() as s:
                    res = await s.execute(sa.insert(events_t).values(kind=kind, ts=now, data=data))
                    await s.commit()
                # 1.2: the durable id is the event's sequence number. A client
                # that lost its stream resumes from it (replay without gaps or
                # duplicates); `seq` is only ever the id of a stored row.
                with contextlib.suppress(Exception):
                    msg["seq"] = int(res.inserted_primary_key[0])
            except asyncio.CancelledError:
                # Fenced-out worker diagnostics happen after authority has already
                # moved to another worker. On Windows/aiosqlite the cancelled old
                # worker can surface CancelledError while persisting this diagnostic.
                # That observation must stay best-effort and must never escape as a
                # failed zombie execution. For every other event cancellation keeps
                # its normal control-flow semantics.
                if kind != "run.fenced_out":
                    raise
            except Exception:  # история не должна ронять основную работу
                pass
        self.publish(msg)
        return msg

    def publish(self, msg: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # отставший клиент отключается и после переподключения перечитает состояние
                self._subscribers.discard(q)

    async def by_trace(self, trace_id: str, limit: int = 500) -> list[dict]:
        """Цепочка событий одного действия (по trace_id в payload).

        BL-108. Отбор делает БАЗА, и идёт он от СВЕЖИХ записей к старым. Раньше
        выбирались 5000 самых СТАРЫХ строк журнала и фильтровались в памяти: на
        установке, где журнал перевалил за 5000 записей (ретеншн разрешает
        200 000, `control_plane.RETENTION_MAX_ROWS`), цепочка действия, которое
        владелец ТОЛЬКО ЧТО видел на экране, в окно не попадала. Ручка
        `/api/observability/trace/{trace_id}` отвечала пустым списком, который
        не отличить от «такого действия не было», — то есть совет «посмотри в
        журнал» переставал работать ровно на той установке, где журнал и нужен.
        Измерено: 5204 строки, свежая цепочка из двух событий — найдено 0,
        старая цепочка из первых строк — найдена.

        Окно не «расширено», а убрано: единственный предел теперь `limit` — это
        размер страницы ответа, а не глубина поиска.
        """
        if self.db is None:
            return []
        limit = max(1, int(limit))
        async with self.db.session() as s:
            res = await s.execute(sa.select(events_t)
                                  .where(events_t.c.data["trace_id"].as_string() == trace_id)
                                  .order_by(events_t.c.id.desc()).limit(limit))
            rows = rows_dicts(res.fetchall())
        return list(reversed(rows))

    async def prune(self, *, max_age_days: int = 14, max_rows: int = 200_000) -> dict[str, int]:
        """TRUTH-003 §14: ограниченное хранение — по возрасту и по числу строк (events и run_events).
        Возвращает, сколько строк удалено. Ничего не удаляет, если БД нет."""
        if self.db is None:
            return {"events": 0, "run_events": 0}
        from datetime import timedelta
        cutoff = utcnow() - timedelta(days=max(1, int(max_age_days)))
        removed = {"events": 0, "run_events": 0}
        async with self.db.session() as s:
            for name, tbl, ts_col in (("events", events_t, events_t.c.ts), ("run_events", run_events_t, run_events_t.c.ts)):
                res = await s.execute(sa.delete(tbl).where(ts_col < cutoff))
                removed[name] += int(res.rowcount or 0)
                total = int((await s.execute(sa.select(sa.func.count()).select_from(tbl))).scalar() or 0)
                if total > max_rows:
                    keep_from = (await s.execute(sa.select(tbl.c.id).order_by(tbl.c.id.desc()).offset(max_rows).limit(1))).scalar()
                    if keep_from is not None:
                        res = await s.execute(sa.delete(tbl).where(tbl.c.id <= keep_from))
                        removed[name] += int(res.rowcount or 0)
            await s.commit()
        return removed

    async def recent(self, limit: int = 50) -> list[dict]:
        if self.db is None:
            return []
        async with self.db.session() as s:
            # Лента активности — для людей: построчный вывод модели и
            # инструментов (STREAM_ONLY) живёт в истории задачи, а не здесь.
            res = await s.execute(
                sa.select(events_t).where(events_t.c.kind.notin_(sorted(STREAM_ONLY)))
                .order_by(events_t.c.id.desc()).limit(limit))
            return rows_dicts(res.fetchall())

    async def task_history(self, task_id: int, *, after: int = 0,
                           limit: int = 500) -> list[dict]:
        """Durable, ordered events of ONE task after a cursor (1.2 replay).

        The cursor is the `events.id` sequence (`seq` on live messages), so a
        client that lost its stream asks for "after N" and gets every stored
        event it missed exactly once. An event belongs to the task when it
        names the task, one of the task's runs, or one of its approvals.
        Transient kinds (metrics, run.log lines, reasoning) are not here by
        construction; run.log has its own table (/api/runs/{id}/events)."""
        if self.db is None:
            return []
        from .db import approvals as approvals_t, task_runs as runs_t
        limit = max(1, min(int(limit), 2000))
        async with self.db.session() as s:
            run_ids = [int(r[0]) for r in (await s.execute(
                sa.select(runs_t.c.id).where(runs_t.c.task_id == task_id))).fetchall()]
            approval_ids = [int(r[0]) for r in (await s.execute(
                sa.select(approvals_t.c.id).where(approvals_t.c.task_id == task_id))).fetchall()]
            # Сравнение ТЕКСТОМ: у coding.task.* task_id — hex-строка, и
            # CAST(... AS INTEGER) уронил бы запрос на Postgres.
            def text(key: str):
                return sa.cast(events_t.c.data[key].as_string(), sa.String)
            belongs = [text("task_id") == str(int(task_id))]
            if run_ids:
                belongs.append(text("run_id").in_([str(i) for i in run_ids]))
            if approval_ids:
                belongs.append(sa.and_(events_t.c.kind.like("approval.%"),
                                       text("id").in_([str(i) for i in approval_ids])))
            res = await s.execute(sa.select(events_t).where(
                events_t.c.id > int(after), sa.or_(*belongs))
                .order_by(events_t.c.id).limit(limit))
            rows = rows_dicts(res.fetchall())
        out = []
        for row in rows:
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            ts = row.get("ts")
            out.append({**data, "kind": row.get("kind"), "seq": int(row["id"]),
                        "ts": ts.isoformat() if hasattr(ts, "isoformat") else ts})
        return out
