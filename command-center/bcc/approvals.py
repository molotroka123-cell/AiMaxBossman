"""Очередь подтверждений (раздел 8): архитектура для опасных действий.

В MVP автоматических опасных действий нет — у агентов нет инструментов записи,
поэтому очередь никем не наполняется автоматически. Но БД, API и события уже
работают: Phase 2 навешивает на них email/deploy/invoice без переделок.
"""
from __future__ import annotations

import sqlalchemy as sa

from .db import Database, approvals as approvals_t, fetch_one, rows_dicts, utcnow
from .events import EventBus


class Approvals:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus

    async def create(self, kind: str, preview: str = "", *, task_id: int | None = None,
                     run_id: int | None = None) -> dict:
        async with self.db.session() as s:
            res = await s.execute(sa.insert(approvals_t).values(
                kind=kind, preview=preview, task_id=task_id, run_id=run_id,
                status="pending", created_at=utcnow()))
            aid = int(res.inserted_primary_key[0])
            await s.commit()
            row = await fetch_one(s, approvals_t, aid)
        await self.bus.emit("approval.created", id=aid, approval_kind=kind, preview=preview[:500],
                            task_id=task_id, run_id=run_id)
        return row or {}

    async def list(self, status: str | None = "pending", limit: int = 100) -> list[dict]:
        async with self.db.session() as s:
            stmt = sa.select(approvals_t).order_by(approvals_t.c.id.desc()).limit(limit)
            if status:
                stmt = stmt.where(approvals_t.c.status == status)
            res = await s.execute(stmt)
            return rows_dicts(res.fetchall())

    async def decide(self, approval_id: int, approve: bool, by: str = "owner") -> dict | None:
        """Решение принимается один раз: повторный вызов ничего не меняет."""
        status = "approved" if approve else "rejected"
        async with self.db.session() as s:
            res = await s.execute(sa.update(approvals_t).where(
                approvals_t.c.id == approval_id,
                approvals_t.c.status == "pending").values(
                status=status, decided_by=by, decided_at=utcnow()))
            await s.commit()
            if not res.rowcount:
                return await fetch_one(s, approvals_t, approval_id)
            row = await fetch_one(s, approvals_t, approval_id)
        await self.bus.emit("approval.decided", id=approval_id, status=status, by=by)
        return row

    async def consume(self, approval_id, *, kind: str, preview: str) -> bool:
        """F-015: подтверждение — это ЗАПИСЬ в таблице, а не флаг в теле запроса.

        True только если approval с этим id существует, имеет статус approved,
        тот же kind и ТОТ ЖЕ preview (детерминированное описание действия:
        команда+cwd / действие+цель). Успешное использование переводит запись в
        status=consumed — повторно предъявить тот же id нельзя (anti-replay)."""
        try:
            aid = int(approval_id)
        except (TypeError, ValueError):
            return False
        async with self.db.session() as s:
            res = await s.execute(sa.update(approvals_t).where(
                approvals_t.c.id == aid,
                approvals_t.c.status == "approved",
                approvals_t.c.kind == kind,
                approvals_t.c.preview == preview).values(status="consumed"))
            await s.commit()
            ok = bool(res.rowcount)
        if ok:
            await self.bus.emit("approval.consumed", id=aid, approval_kind=kind)
        return ok

