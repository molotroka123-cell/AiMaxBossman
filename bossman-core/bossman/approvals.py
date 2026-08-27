"""Очередь подтверждений: необратимое — только с подтверждением (принцип 5).

Действие с пометкой confirm не выполняется без нажатия; отклонённое — не выполняется
никогда. Раннер задачи ждёт решения; решение приходит из UI или Telegram."""
from __future__ import annotations

import asyncio

from . import db, events, telegram


async def create(kind: str, preview: str, *, task_id: int | None = None,
                 run_id: int | None = None, tool: str | None = None,
                 payload: dict | None = None) -> int:
    row = await db.fetchrow(
        """INSERT INTO approvals (task_id, run_id, kind, tool, payload, preview)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        task_id, run_id, kind, tool, payload, preview)
    approval_id = row["id"]
    events.emit("approval.created", id=approval_id, kind=kind, tool=tool, preview=preview[:500])
    await telegram.ask_approval(approval_id, preview)
    return approval_id


async def decide(approval_id: int, approve: bool, decided_by: str) -> dict | None:
    row = await db.fetchrow(
        """UPDATE approvals SET status=$2, decided_by=$3, decided_at=now()
           WHERE id=$1 AND status='pending' RETURNING *""",
        approval_id, "approved" if approve else "rejected", decided_by)
    if row:
        events.emit("approval.decided", id=approval_id, status=row["status"], by=decided_by)
    return row


async def wait(approval_id: int, timeout_s: int = 24 * 3600) -> dict:
    """Ждать решения. Пока висит — задача в waiting_approval, ничего не выполняется."""
    for _ in range(timeout_s // 2):
        row = await db.fetchrow("SELECT * FROM approvals WHERE id=$1", approval_id)
        if row and row["status"] != "pending":
            return row
        await asyncio.sleep(2)
    await db.execute("UPDATE approvals SET status='expired' WHERE id=$1 AND status='pending'",
                     approval_id)
    return {"id": approval_id, "status": "expired"}
