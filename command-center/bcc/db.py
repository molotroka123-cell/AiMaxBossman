"""Схема БД (раздел 3 архитектуры) и async-движок SQLAlchemy 2.

Состояние живёт только в БД: задача переживает reboot, worker поднимает её с checkpoint.
Времена храним наивным UTC — так сравнения одинаково работают и в SQLite, и в Postgres.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

metadata = sa.MetaData()


def utcnow() -> datetime:
    """Наивный UTC — единый формат времени во всей системе."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


providers = sa.Table(
    "providers", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("kind", sa.String(32), nullable=False),          # openai_compat | anthropic
    sa.Column("base_url", sa.String(500), nullable=False, default=""),
    sa.Column("api_key_enc", sa.Text),                          # Fernet, наружу — только маска
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

models = sa.Table(
    "models", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("provider_id", sa.Integer, sa.ForeignKey("providers.id", ondelete="CASCADE"),
              nullable=False),
    sa.Column("name", sa.String(200), nullable=False),          # имя модели у провайдера
    sa.Column("alias", sa.String(120), nullable=False, unique=True),
    sa.Column("kind", sa.String(16), default="local"),          # local | cloud
    sa.Column("context_window", sa.Integer, default=8192),
    sa.Column("caps", sa.JSON, default=dict),                   # vision, tools, reasoning, coding
    sa.Column("price_in", sa.Float, default=0.0),               # USD за 1M входных токенов
    sa.Column("price_out", sa.Float, default=0.0),
    sa.Column("status", sa.String(16), default="unknown"),      # unknown|online|offline|error
    sa.Column("status_detail", sa.Text, default=""),
    sa.Column("last_check", sa.DateTime),
    sa.Column("bench", sa.JSON),                                # prompt_tps, gen_tps, latency_ms, tested_at
)

agents = sa.Table(
    "agents", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("role", sa.String(200), default=""),
    sa.Column("system_prompt", sa.Text, default=""),
    sa.Column("model_id", sa.Integer, sa.ForeignKey("models.id", ondelete="SET NULL")),
    sa.Column("fallback_model_id", sa.Integer, sa.ForeignKey("models.id", ondelete="SET NULL")),
    sa.Column("tools", sa.JSON, default=list),                  # в MVP пусто: опасных инструментов нет
    sa.Column("max_steps", sa.Integer, default=4),
    sa.Column("max_tokens", sa.Integer, default=2048),
    sa.Column("budget_usd", sa.Float, default=0.0),
    sa.Column("permissions", sa.JSON, default=dict),
    sa.Column("enabled", sa.Boolean, default=True),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

tasks = sa.Table(
    "tasks", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("title", sa.String(300), default=""),
    sa.Column("prompt", sa.Text, nullable=False),
    sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="SET NULL")),
    # draft|queued|running|paused|waiting_approval|completed|failed|stopped
    sa.Column("status", sa.String(24), default="draft"),
    sa.Column("priority", sa.Integer, default=5),               # меньше — важнее
    sa.Column("max_retries", sa.Integer, default=2),
    sa.Column("schedule_id", sa.Integer, sa.ForeignKey("schedules.id", ondelete="SET NULL")),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow, onupdate=utcnow),
)

task_runs = sa.Table(
    "task_runs", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    sa.Column("attempt", sa.Integer, default=0),
    # queued|leased|running|completed|failed|stopped
    sa.Column("status", sa.String(16), default="queued"),
    # для leased/running — срок аренды; для queued — «не раньше» (пауза перед retry)
    sa.Column("worker_lease_until", sa.DateTime),
    sa.Column("checkpoint", sa.JSON),                           # {messages, step, note}
    sa.Column("result", sa.Text),
    sa.Column("error", sa.Text),
    sa.Column("model_alias", sa.String(120)),
    sa.Column("tokens_in", sa.Integer, default=0),
    sa.Column("tokens_out", sa.Integer, default=0),
    sa.Column("cost_usd", sa.Float, default=0.0),
    sa.Column("started_at", sa.DateTime),
    sa.Column("finished_at", sa.DateTime),
)

schedules = sa.Table(
    "schedules", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),           # once | interval | daily
    sa.Column("at_time", sa.DateTime),                          # для once
    sa.Column("interval_minutes", sa.Integer),
    sa.Column("daily_time", sa.String(8)),                      # "HH:MM"
    sa.Column("next_run_at", sa.DateTime),
    sa.Column("enabled", sa.Boolean, default=True),
    sa.Column("task_template", sa.JSON, default=dict),          # title, prompt, agent_id, priority, max_retries
    sa.Column("last_fired_at", sa.DateTime),
)

run_events = sa.Table(
    "run_events", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("ts", sa.DateTime, default=utcnow),
    sa.Column("level", sa.String(8), default="info"),           # info | warn | error
    sa.Column("kind", sa.String(48), default="log"),
    sa.Column("message", sa.Text, default=""),
    sa.Column("data", sa.JSON),
)

approvals = sa.Table(
    "approvals", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="CASCADE")),
    sa.Column("run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="CASCADE")),
    sa.Column("kind", sa.String(48), nullable=False),
    sa.Column("preview", sa.Text, default=""),
    sa.Column("status", sa.String(16), default="pending"),      # pending|approved|rejected
    sa.Column("decided_by", sa.String(120)),
    sa.Column("decided_at", sa.DateTime),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

system_metrics = sa.Table(
    "system_metrics", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("ts", sa.DateTime, default=utcnow, index=True),
    sa.Column("cpu_pct", sa.Float),
    sa.Column("ram_used_mb", sa.Float),
    sa.Column("ram_total_mb", sa.Float),
    sa.Column("disk_used_gb", sa.Float),
    sa.Column("disk_total_gb", sa.Float),
    sa.Column("gpu", sa.JSON),
)

events = sa.Table(
    "events", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("ts", sa.DateTime, default=utcnow, index=True),
    sa.Column("kind", sa.String(48), nullable=False),
    sa.Column("data", sa.JSON),
)

settings_kv = sa.Table(
    "settings", metadata,
    sa.Column("key", sa.String(120), primary_key=True),
    sa.Column("value_enc", sa.Text),                            # значение шифруется тем же Fernet
)


class Database:
    """Тонкая обёртка над async-движком: сессии, create_all, аккуратное закрытие."""

    def __init__(self, url: str):
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, future=True, **_engine_kwargs(url))
        self._sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)
        if url.startswith("sqlite"):
            sa.event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)

    def session(self) -> AsyncSession:
        return self._sessionmaker()

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def ping(self) -> bool:
        async with self.session() as s:
            await s.execute(sa.text("SELECT 1"))
        return True

    async def close(self) -> None:
        await self.engine.dispose()


def _engine_kwargs(url: str) -> dict[str, Any]:
    if url.startswith("sqlite"):
        # busy_timeout снимает гонку worker'а и API за одну SQLite-базу
        return {"connect_args": {"timeout": 30}}
    return {"pool_pre_ping": True}


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def row_dict(row: Any) -> dict | None:
    """Строка результата → обычный dict (для JSON-ответов)."""
    return dict(row._mapping) if row is not None else None


def rows_dicts(rows: Any) -> list[dict]:
    return [dict(r._mapping) for r in rows]


async def fetch_one(session: AsyncSession, table: sa.Table, row_id: int) -> dict | None:
    res = await session.execute(sa.select(table).where(table.c.id == row_id))
    return row_dict(res.first())


__all__ = [
    "Database", "Engine", "metadata", "utcnow", "row_dict", "rows_dicts", "fetch_one",
    "providers", "models", "agents", "tasks", "task_runs", "schedules", "run_events",
    "approvals", "system_metrics", "events", "settings_kv",
]
