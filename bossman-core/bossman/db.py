"""Postgres (asyncpg): пул, применение схемы, короткие помощники."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg

from .config import ROOT, settings

_pool: asyncpg.Pool | None = None

SCHEMA = Path(ROOT) / "db" / "schema.sql"


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10,
                                          init=_init_conn)
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA.read_text())
    return _pool


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def fetch(sql: str, *args: Any) -> list[dict]:
    p = await pool()
    rows = await p.fetch(sql, *args)
    return [dict(r) for r in rows]


async def fetchrow(sql: str, *args: Any) -> dict | None:
    p = await pool()
    row = await p.fetchrow(sql, *args)
    return dict(row) if row else None


async def fetchval(sql: str, *args: Any) -> Any:
    p = await pool()
    return await p.fetchval(sql, *args)


async def execute(sql: str, *args: Any) -> str:
    p = await pool()
    return await p.execute(sql, *args)
