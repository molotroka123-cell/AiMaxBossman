"""Pending-approval hygiene (architecture P0 step 3, 12.11.2026).

Synthetic/bench tasks (pc-operator benchmark, «synthetic operator probe», …) can leave ``pending`` approvals that nobody
should ever answer; they clutter the owner's pult list. A tick expires ONLY those: the approval's TASK TITLE (never the
prompt or any chat content) must carry a synthetic marker, and the row must be older than the TTL. Real owner tasks'
approvals are never touched.

Config (env): ``BCC_SYNTH_APPROVAL_TTL_HOURS`` (default 24, 0 = off), ``BCC_SYNTH_APPROVAL_MARKERS`` (comma list,
case-insensitive substrings of the task title; default ``pcbench,synthetic``).
"""
from __future__ import annotations

import datetime as dt
import os

import sqlalchemy as sa

from ..db import approvals as approvals_t, tasks as tasks_t, utcnow
from . import Feature

DEFAULT_MARKERS = ("pcbench", "synthetic")
DEFAULT_TTL_HOURS = 24.0
SYSTEM_ACTOR = "system:synthetic-ttl"


def markers() -> tuple[str, ...]:
    raw = os.environ.get("BCC_SYNTH_APPROVAL_MARKERS")
    if raw is None:
        return DEFAULT_MARKERS
    return tuple(x.strip().lower() for x in raw.split(",") if x.strip())


def ttl_hours() -> float:
    try:
        return float(os.environ.get("BCC_SYNTH_APPROVAL_TTL_HOURS", DEFAULT_TTL_HOURS))
    except ValueError:
        return DEFAULT_TTL_HOURS


def is_synthetic_title(title: str | None) -> bool:
    t = str(title or "").lower()
    return bool(t) and any(m in t for m in markers())


async def expire_synthetic(svc, *, now: dt.datetime | None = None) -> list[int]:
    """pending -> expired for synthetic-task approvals older than the TTL. Returns the expired ids."""
    hours = ttl_hours()
    if hours <= 0 or not markers():
        return []
    cutoff = (now or utcnow()) - dt.timedelta(hours=hours)
    async with svc.db.session() as s:
        rows = (await s.execute(
            sa.select(approvals_t.c.id, tasks_t.c.title)
            .select_from(approvals_t.join(tasks_t, tasks_t.c.id == approvals_t.c.task_id))
            .where(approvals_t.c.status == "pending", approvals_t.c.created_at < cutoff))).fetchall()
    ids = [int(r[0]) for r in rows if is_synthetic_title(r[1])]
    done: list[int] = []
    for aid in ids:
        async with svc.db.session() as s:
            res = await s.execute(sa.update(approvals_t).where(
                approvals_t.c.id == aid, approvals_t.c.status == "pending").values(
                status="expired", decided_by=SYSTEM_ACTOR, decided_at=utcnow()))
            await s.commit()
        if res.rowcount:
            done.append(aid)
            await svc.bus.emit("approval.decided", id=aid, status="expired", by=SYSTEM_ACTOR)
    return done


async def _tick(svc) -> None:
    await expire_synthetic(svc)


FEATURE = Feature(name="approval_hygiene", tick=_tick, tick_seconds=600.0)
