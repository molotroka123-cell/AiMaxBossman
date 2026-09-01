"""Postgres (asyncpg): пул, применение схемы, короткие помощники."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg

from . import errors
from .config import ROOT, settings

_pool: asyncpg.Pool | None = None

SCHEMA = Path(ROOT) / "db" / "schema.sql"


def _dsn_hint() -> str:
    """Подсказка без пароля: DSN может нести креды, в текст ошибки они не идут."""
    dsn = settings.database_url or ""
    tail = dsn.rsplit("@", 1)[-1] if "@" in dsn else dsn
    return tail or "<BOSSMAN_DATABASE_URL не задан>"


async def pool() -> asyncpg.Pool:
    """Пул Postgres. Если БД недоступна — честный отказ с подсказкой, а не
    сырой трейс asyncpg на каждый запрос: Core без Postgres не работает
    (это его единственное durable-хранилище), и разработчик должен сразу
    увидеть, что именно поднять."""
    global _pool
    if _pool is None:
        try:
            _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10,
                                              init=_init_conn)
        except (OSError, asyncpg.PostgresError) as exc:
            raise errors.DependencyUnavailable(
                f"Postgres недоступен ({_dsn_hint()}): {type(exc).__name__}. "
                f"Подними БД (docker compose up -d postgres) или укажи рабочий "
                f"BOSSMAN_DATABASE_URL в .env",
                extra={"dependency": "postgres"}) from exc
        try:
            async with _pool.acquire() as conn:
                # encoding задаём явно: на Windows read_text() берёт cp1252 и
                # ломается на кириллице в комментариях схемы.
                await conn.execute(SCHEMA.read_text(encoding="utf-8"))
        except Exception as exc:
            await _pool.close()
            _pool = None
            raise errors.DependencyUnavailable(
                f"Не удалось применить схему БД: {type(exc).__name__}: {exc}",
                extra={"dependency": "postgres", "schema": str(SCHEMA)}) from exc
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
