"""DecisionStore + FailureStore — 5 каналов памяти (FACTS уже в facts table)."""
from __future__ import annotations

import sqlalchemy as sa

from ..db import Database, decisions as dec_t, failures as fail_t, utcnow


class DecisionStore:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, *, key: str, decision: str, reason: str = "",
                  alternatives_rejected: list[str] | None = None,
                  scope: str = "Bossman V1", created_by: str = "") -> dict:
        async with self.db.session() as s:
            res = await s.execute(sa.insert(dec_t).values(
                key=key, decision=decision, reason=reason,
                alternatives_rejected=alternatives_rejected or [],
                scope=scope, created_by=created_by, created_at=utcnow()))
            did = int(res.inserted_primary_key[0])
            await s.commit()
            row = await s.execute(sa.select(dec_t).where(dec_t.c.id == did))
            return dict(row.first()._mapping)

    async def get(self, key: str) -> dict | None:
        async with self.db.session() as s:
            row = (await s.execute(sa.select(dec_t).where(dec_t.c.key == key))).first()
            return dict(row._mapping) if row else None

    async def list(self, scope: str | None = None) -> list[dict]:
        async with self.db.session() as s:
            q = sa.select(dec_t).order_by(dec_t.c.id)
            if scope:
                q = q.where(dec_t.c.scope == scope)
            rows = (await s.execute(q)).fetchall()
            return [dict(r._mapping) for r in rows]

    async def supersede(self, key: str, new_key: str, **kw) -> dict:
        """Создать новое решение и пометить старое как superseded_by."""
        new = await self.add(key=new_key, **kw)
        async with self.db.session() as s:
            await s.execute(sa.update(dec_t).where(dec_t.c.key == key).values(
                superseded_by=new["id"]))
            await s.commit()
        return new


class FailureStore:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, *, symptom: str, root_cause: str = "",
                  attempted_fix: str = "", result: str = "",
                  files: list[str] | None = None, test: str = "",
                  task_id: int | None = None, run_id: int | None = None) -> dict:
        async with self.db.session() as s:
            res = await s.execute(sa.insert(fail_t).values(
                symptom=symptom, root_cause=root_cause,
                attempted_fix=attempted_fix, result=result,
                files=files or [], test=test,
                task_id=task_id, run_id=run_id, created_at=utcnow()))
            fid = int(res.inserted_primary_key[0])
            await s.commit()
            row = await s.execute(sa.select(fail_t).where(fail_t.c.id == fid))
            return dict(row.first()._mapping)

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """Простой LIKE-поиск по symptom/root_cause — достаточно для POC."""
        pat = f"%{query}%"
        async with self.db.session() as s:
            rows = (await s.execute(
                sa.select(fail_t).where(
                    sa.or_(fail_t.c.symptom.like(pat),
                           fail_t.c.root_cause.like(pat)))
                .order_by(fail_t.c.id.desc()).limit(limit)
            )).fetchall()
            return [dict(r._mapping) for r in rows]

    async def list_recent(self, limit: int = 10) -> list[dict]:
        async with self.db.session() as s:
            rows = (await s.execute(sa.select(fail_t).order_by(fail_t.c.id.desc()).limit(limit))).fetchall()
            return [dict(r._mapping) for r in rows]
