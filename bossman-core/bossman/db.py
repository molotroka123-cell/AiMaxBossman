"""Postgres (asyncpg): пул, применение схемы, короткие помощники."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import asyncpg

from . import errors
from .config import ROOT, settings

_pool: asyncpg.Pool | None = None
# Loop, на котором создан _pool. Соединения asyncpg (protocol/transport/Future)
# намертво привязаны к своему event loop: пул, созданный на loop A и
# использованный на loop B, даёт «Task got Future attached to a different loop»
# (BUG-004: pytest-asyncio function-scoped loops; в проде — любой сценарий
# «asyncio.run() во вспомогательном потоке/CLI, затем основной loop сервера»).
_pool_loop: asyncio.AbstractEventLoop | None = None

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
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool is not None and _pool_loop is not loop:
        # Чужой loop: пул нельзя ни использовать, ни `await close()` — его
        # Future'ы живут на старом (часто уже закрытом) loop. Только terminate()
        # (синхронный abort транспортов) и пересоздание на текущем loop.
        _discard_stale_pool()
    if _pool is None:
        try:
            _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10,
                                              init=_init_conn)
            _pool_loop = loop
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
            _pool_loop = None
            raise errors.DependencyUnavailable(
                f"Не удалось применить схему БД: {type(exc).__name__}: {exc}",
                extra={"dependency": "postgres", "schema": str(SCHEMA)}) from exc
    return _pool


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def bound_loop() -> asyncio.AbstractEventLoop | None:
    """Loop, которому принадлежит текущий пул (None — пула нет). Для тестов
    и диагностики: продакшн-код к нему не обращается."""
    return _pool_loop if _pool is not None else None


def _discard_stale_pool() -> None:
    """Сбросить пул, созданный на другом loop. Ожидать его закрытия нельзя
    (это и уронило предыдущую попытку c1c44df: `await close()` на чужом loop),
    поэтому — best-effort terminate() и забываем объект. Соединения, которые
    terminate() не смог оборвать (loop уже закрыт), закроет GC транспорта."""
    global _pool, _pool_loop
    stale, _pool, _pool_loop = _pool, None, None
    if stale is None:
        return
    try:
        stale.terminate()
    except Exception:  # noqa: BLE001 — старый loop может быть закрыт
        pass


async def close() -> None:
    global _pool, _pool_loop
    if _pool is None:
        return
    if _pool_loop is not asyncio.get_running_loop():
        _discard_stale_pool()          # graceful close на чужом loop невозможен
        return
    stale, _pool, _pool_loop = _pool, None, None
    await stale.close()


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
