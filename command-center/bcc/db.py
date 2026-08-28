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

# ---------- V2 (docs/V2_SHARED_CONTRACTS.md §1). Схему меняет только лид. ----------

missions = sa.Table(
    "missions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("title", sa.String(300), nullable=False),
    sa.Column("goal", sa.Text, default=""),
    # единый словарь статусов V2 (§2 контрактов)
    sa.Column("status", sa.String(24), default="draft"),
    sa.Column("duration_minutes", sa.Integer),
    sa.Column("max_workers", sa.Integer, default=2),
    sa.Column("cloud_budget_usd", sa.Float, default=0.0),
    sa.Column("spent_usd", sa.Float, default=0.0),
    sa.Column("plan", sa.JSON),                                 # milestones/tasks плана
    sa.Column("progress", sa.Float, default=0.0),               # 0..1
    sa.Column("kpi_targets", sa.JSON, default=dict),            # {"analyzed": 10, …}
    sa.Column("meta", sa.JSON, default=dict),
    sa.Column("started_at", sa.DateTime),
    sa.Column("finished_at", sa.DateTime),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("updated_at", sa.DateTime, default=utcnow, onupdate=utcnow),
)

kpi_history = sa.Table(
    "kpi_history", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mission_id", sa.Integer, sa.ForeignKey("missions.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("key", sa.String(120), nullable=False),
    sa.Column("value", sa.Float, nullable=False),               # значение ПОСЛЕ применения delta
    sa.Column("delta", sa.Float, default=0.0),
    sa.Column("source_task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="SET NULL")),
    sa.Column("ts", sa.DateTime, default=utcnow, index=True),
)

orchestras = sa.Table(
    "orchestras", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("mode", sa.String(24), default="manager"),        # sequential|parallel|manager|debate|review_loop
    sa.Column("config", sa.JSON, default=dict),                 # max_workers, duration, budget, approval policy
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

orchestra_members = sa.Table(
    "orchestra_members", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("orchestra_id", sa.Integer, sa.ForeignKey("orchestras.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
    sa.Column("role", sa.String(16), default="worker"),         # manager|worker|reviewer
    sa.Column("position", sa.Integer, default=0),
)

skills = sa.Table(
    "skills", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("slug", sa.String(120), nullable=False, unique=True),
    sa.Column("description", sa.Text, default=""),
    sa.Column("current_version_id", sa.Integer),                # FK на skill_versions (без cycle-constraint)
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

skill_versions = sa.Table(
    "skill_versions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("skill_id", sa.Integer, sa.ForeignKey("skills.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("input_schema", sa.JSON, default=dict),
    sa.Column("output_schema", sa.JSON, default=dict),
    sa.Column("required_tools", sa.JSON, default=list),
    sa.Column("process", sa.Text, default=""),                  # рабочий процесс/чек-лист (в prompt)
    sa.Column("permissions", sa.JSON, default=list),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

benchmarks = sa.Table(
    "benchmarks", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("model_id", sa.Integer, sa.ForeignKey("models.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("kind", sa.String(16), default="full"),           # quick | full
    sa.Column("status", sa.String(24), default="queued"),
    sa.Column("results", sa.JSON),                              # ttft, tps, ram, samples, stability
    sa.Column("error", sa.Text),
    sa.Column("started_at", sa.DateTime),
    sa.Column("finished_at", sa.DateTime),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

checkpoints = sa.Table(
    "checkpoints", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("step", sa.Integer, nullable=False),
    sa.Column("messages", sa.JSON, nullable=False),
    sa.Column("note", sa.String(200), default=""),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

session_forks = sa.Table(
    "session_forks", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("source_run_id", sa.Integer, sa.ForeignKey("task_runs.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("checkpoint_id", sa.Integer, sa.ForeignKey("checkpoints.id", ondelete="SET NULL")),
    sa.Column("new_task_id", sa.Integer, sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    sa.Column("changes", sa.JSON, default=dict),                # instruction/model/agent overrides
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

resource_reservations = sa.Table(
    "resource_reservations", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("kind", sa.String(8), default="ram"),             # ram | gpu
    sa.Column("amount_mb", sa.Float, nullable=False),
    sa.Column("holder_kind", sa.String(16), nullable=False),    # model | task | benchmark
    sa.Column("holder_id", sa.Integer, nullable=False),
    sa.Column("status", sa.String(16), default="held"),         # held | released | expired
    sa.Column("detail", sa.Text, default=""),
    sa.Column("created_at", sa.DateTime, default=utcnow),
    sa.Column("released_at", sa.DateTime),
    sa.Column("expires_at", sa.DateTime),                       # crash-страховка: истёк — освободить
)

interventions = sa.Table(
    "interventions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("target_kind", sa.String(16), nullable=False),    # task|run|mission|model
    sa.Column("target_id", sa.Integer, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("action", sa.String(16), nullable=False),         # paused|stopped|switched|throttled|escalated
    sa.Column("detail", sa.JSON, default=dict),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

recovery_attempts = sa.Table(
    "recovery_attempts", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("target_kind", sa.String(16), nullable=False),    # model|worker|browser|task
    sa.Column("target_id", sa.Integer),
    sa.Column("failure", sa.Text, default=""),
    sa.Column("action", sa.String(32), default=""),             # retry|fallback|restart|requeue
    sa.Column("attempt", sa.Integer, default=1),
    sa.Column("status", sa.String(16), default="started"),      # started|completed|escalated
    sa.Column("detail", sa.JSON, default=dict),
    sa.Column("created_at", sa.DateTime, default=utcnow),
)

# Новые колонки существующих таблиц добавляются идемпотентным ALTER в Database.migrate():
# SQLAlchemy create_all не добавляет колонки в существующие таблицы.
V2_NEW_COLUMNS: list[tuple[str, str, str]] = [
    # (таблица, колонка, SQL-тип с default'ом)
    ("tasks", "mission_id", "INTEGER"),
    ("tasks", "orchestra_id", "INTEGER"),
    ("tasks", "skill_version_id", "INTEGER"),
    ("tasks", "kind", "VARCHAR(24) DEFAULT 'generic'"),
    ("tasks", "parent_task_id", "INTEGER"),
    ("tasks", "workspace_path", "VARCHAR(500)"),
    ("tasks", "meta", "JSON"),
    ("task_runs", "route", "JSON"),
    ("task_runs", "reservation_id", "INTEGER"),
    ("agents", "workspace", "VARCHAR(500)"),
]

# Table-объекты выше объявлены ДО этого блока, поэтому колонки добавляем и в metadata —
# select(tasks) должен видеть новые поля.
for _table, _col, _sqltype in V2_NEW_COLUMNS:
    _t = metadata.tables[_table]
    if _col not in _t.c:
        if _sqltype.startswith("JSON"):
            _coltype: sa.types.TypeEngine = sa.JSON()
        elif _sqltype.startswith("INTEGER"):
            _coltype = sa.Integer()
        else:
            _coltype = sa.String(500)
        _default = "generic" if _col == "kind" else None
        _t.append_column(sa.Column(_col, _coltype, default=_default))


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
        await self._migrate()

    async def _migrate(self) -> None:
        """Идемпотентные ALTER для новых V2-колонок: create_all не расширяет
        существующие таблицы. Каждый ALTER — в своей транзакции: уже добавленная
        колонка не должна валить остальные."""
        sqlite = self.url.startswith("sqlite")
        for table, col, sqltype in V2_NEW_COLUMNS:
            if_not = "" if sqlite else "IF NOT EXISTS "
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(sa.text(
                        f"ALTER TABLE {table} ADD COLUMN {if_not}{col} {sqltype}"))
            except sa.exc.OperationalError:
                pass  # SQLite: duplicate column — колонка уже есть

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
    # V2
    "missions", "kpi_history", "orchestras", "orchestra_members", "skills",
    "skill_versions", "benchmarks", "checkpoints", "session_forks",
    "resource_reservations", "interventions", "recovery_attempts",
]
