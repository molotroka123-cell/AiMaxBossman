"""Очередь подтверждений (раздел 8): архитектура для опасных действий.

В MVP автоматических опасных действий нет — у агентов нет инструментов записи,
поэтому очередь никем не наполняется автоматически. Но БД, API и события уже
работают: Phase 2 навешивает на них email/deploy/invoice без переделок.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

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

    async def decide(self, approval_id: int, approve: bool, by: str = "owner", *,
                     prepare_approve: Callable[[AsyncSession, dict], Awaitable[dict]] | None = None
                     ) -> dict | None:
        """Apply one decision, optionally preparing its lease in the SAME transaction.

        Only the winner of pending -> approved may prepare authority. A replay
        returns the existing decision without invoking prepare_approve. Failure
        while preparing rolls back the decision too: no approved row can wake
        a worker after an invalid lease request. The callback must perform only
        transactional database work; notifications happen after commit.
        """
        status = "approved" if approve else "rejected"
        lease = None
        async with self.db.session() as s:
            res = await s.execute(sa.update(approvals_t).where(
                approvals_t.c.id == approval_id,
                approvals_t.c.status == "pending").values(
                status=status, decided_by=by, decided_at=utcnow()))
            if not res.rowcount:
                await s.rollback()
                return await fetch_one(s, approvals_t, approval_id)
            row = await fetch_one(s, approvals_t, approval_id)
            if approve and prepare_approve is not None:
                try:
                    lease = await prepare_approve(s, row)
                except BaseException:
                    await s.rollback()
                    raise
            await s.commit()
        # Persist the lease BEFORE publishing approval.decided: the worker may
        # resume immediately on that notification.
        if lease is not None:
            await self.bus.emit("approval.lease_granted", lease_id=lease["id"],
                                tool=lease["tool"], effect_class=lease["effect_class"],
                                scope_key=lease["scope_key"], task_id=lease["task_id"],
                                agent_id=lease["agent_id"], max_uses=lease["max_uses"],
                                ttl_seconds=int((lease["expires_at"] - lease["created_at"]).total_seconds()),
                                by=by)
        await self.bus.emit("approval.decided", id=approval_id, status=status, by=by)
        return {**row, "lease": lease} if lease is not None else row

    async def revoke(self, approval_id: int, by: str = "owner") -> dict | None:
        """Withdraw an approval BEFORE its effect: approved -> revoked (CAS).

        Execution Truth §8: authorization must still be valid at effect time. An
        approval given while the process was down, or given by mistake, could
        only be undone by racing the worker. A revoked row is not `approved`, so
        every consumer fails closed: the parked tool call resumes as rejected
        (`approval.decided` wakes it), `consume()` refuses it, `finalize_override`
        and the review sweep ignore it. Pending rows are decided, not revoked."""
        async with self.db.session() as s:
            res = await s.execute(sa.update(approvals_t).where(
                approvals_t.c.id == approval_id,
                approvals_t.c.status == "approved").values(
                status="revoked", decided_by=by, decided_at=utcnow()))
            await s.commit()
            row = await fetch_one(s, approvals_t, approval_id)
        if res.rowcount and row is not None:
            await self.bus.emit("approval.revoked", id=approval_id, by=by, approval_kind=row.get("kind"))
            await self.bus.emit("approval.decided", id=approval_id, status="revoked", by=by)
        return row

    async def accept_for_execution(self, approval_id: int) -> bool:
        """Граница «одобрение принято к исполнению»: approved → consumed, CAS по id.

        Это единственная атомарная точка между решением человека и эффектом.
        Отзыв, подтверждённый ДО неё, побеждает: строка уже не approved, CAS даёт
        rowcount == 0, эффект не выполняется. Отзыв ПОСЛЕ неё проигрывает — и не
        обещает отмены уже начатого действия: `revoke()` требует approved, а
        строка уже consumed. Между «прочитали approved» и «исполнили» больше нет
        окна, в котором и отзыв прошёл в базе, и эффект произошёл.
        """
        try:
            aid = int(approval_id)
        except (TypeError, ValueError):
            return False
        async with self.db.session() as s:
            res = await s.execute(sa.update(approvals_t).where(
                approvals_t.c.id == aid,
                approvals_t.c.status == "approved").values(status="consumed"))
            await s.commit()
            ok = bool(res.rowcount)
            row = await fetch_one(s, approvals_t, aid) if ok else None
        if ok:
            await self.bus.emit("approval.consumed", id=aid,
                                approval_kind=(row or {}).get("kind"))
        return ok

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
