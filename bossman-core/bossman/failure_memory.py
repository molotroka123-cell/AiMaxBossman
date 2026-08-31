"""Failure Memory with structured failure records.

Structured failure records that can be retrieved before retrying,
providing evidence for reasoning without automatically executing fixes.

Suggested schema (from spec):
{
  "failure_id": "...",
  "task_id": "...",
  "symptom": "...",
  "error_class": "...",
  "root_cause": "...",
  "attempted_fix": "...",
  "result": "...",
  "files": [],
  "tests": [],
  "environment": {},
  "resolved": false,
  "created_at": "..."
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import errors
from .db import execute, fetch, fetchrow, fetchval, pool


# ──────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────

@dataclass
class FailureRecord:
    """A structured failure record."""

    failure_id: str
    task_id: str
    symptom: str
    error_class: str
    root_cause: str
    attempted_fix: str
    result: str
    files: list[Any] = field(default_factory=list)
    tests: list[Any] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    resolved: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


# ──────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────

# Схема таблицы `failures` принадлежит ЕДИНСТВЕННОМУ авторитету —
# bossman-core/db/schema.sql (применяется в db.pool()). Здесь DDL намеренно НЕТ:
# два определения одной таблицы = два источника правды.


# ──────────────────────────────────────────────────────────────────────
# CRUD operations
# ──────────────────────────────────────────────────────────────────────

async def init_failures_table() -> None:
    """No-op совместимости: схему применяет db.pool() из db/schema.sql.

    Возвращает управление, если каноничная таблица на месте; иначе — честная ошибка
    (а не тихое создание второй, расходящейся схемы).
    """
    ok = await fetchval("SELECT to_regclass('public.failures') IS NOT NULL")
    if not ok:
        raise errors.DependencyUnavailable(
            "таблица failures отсутствует: схему создаёт db/schema.sql через db.pool()",
            extra={"dependency": "postgres", "table": "failures"})


async def record_failure(
    task_id: str,
    symptom: str,
    error_class: str,
    root_cause: str,
    attempted_fix: str,
    result: str,
    *,
    files: list[Any] | None = None,
    tests: list[Any] | None = None,
    environment: dict[str, Any] | None = None,
) -> FailureRecord:
    """Record a new failure failure."""

    fid = f"fail-{datetime.now().timestamp()}"
    files_val = files or []
    tests_val = tests or []
    env_val = environment or {}

    row = await fetchrow(
        """INSERT INTO failures
           (failure_id, task_id, symptom, error_class, root_cause,
            attempted_fix, result, files, tests, environment,
            created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, now())
           RETURNING failure_id, task_id, symptom, error_class, root_cause,
                     attempted_fix, result, files, tests, environment,
                     resolved, created_at""",
        fid,
        task_id,
        symptom,
        error_class,
        root_cause,
        attempted_fix,
        result,
        files_val,
        tests_val,
        env_val,
    )
    return FailureRecord(
            failure_id=row["failure_id"],
            task_id=row["task_id"],
            symptom=row["symptom"],
            error_class=row["error_class"],
            root_cause=row["root_cause"],
            attempted_fix=row["attempted_fix"],
            result=row["result"],
            files=row["files"],
            tests=row["tests"],
            environment=row["environment"],
            resolved=row["resolved"],
            created_at=row["created_at"],
        )


async def get_failure(failure_id: str) -> FailureRecord | None:
    """Get a failure by its failure_id."""
    row = await fetchrow(
        "SELECT * FROM failures WHERE failure_id = $1",
        failure_id,
    )

    if row is None:
        return None

    return FailureRecord(
        failure_id=row["failure_id"],
        task_id=row["task_id"],
        symptom=row["symptom"],
        error_class=row["error_class"],
        root_cause=row["root_cause"],
        attempted_fix=row["attempted_fix"],
        result=row["result"],
        files=row["files"],
        tests=row["tests"],
        environment=row["environment"],
        resolved=row["resolved"],
        created_at=row["created_at"],
        resolved_at=row.get("resolved_at"),
    )


async def get_unresolved_failures(task_id: str) -> list[FailureRecord]:
    """Get all unresolved failures for a task."""
    # asyncpg execute() returns a status string, not an async iterator — must fetch().
    rows = await fetch(
        "SELECT * FROM failures WHERE task_id = $1 AND resolved = FALSE ORDER BY created_at DESC",
        task_id,
    )
    return [
        FailureRecord(
            failure_id=row["failure_id"],
            task_id=row["task_id"],
            symptom=row["symptom"],
            error_class=row["error_class"],
            root_cause=row["root_cause"],
            attempted_fix=row["attempted_fix"],
            result=row["result"],
            files=row["files"],
            tests=row["tests"],
            environment=row["environment"],
            resolved=row["resolved"],
            created_at=row["created_at"],
            resolved_at=row.get("resolved_at"),
        )
        for row in rows
    ]


async def resolve_failure(failure_id: str) -> bool:
    """Mark a failure as resolved."""
    now = datetime.now(timezone.utc)
    # asyncpg execute() returns the command tag string, e.g. "UPDATE 1"; there is
    # no .status attribute. One row updated ⇒ exactly "UPDATE 1".
    status = await execute(
        "UPDATE failures SET resolved = TRUE, resolved_at = $1 WHERE failure_id = $2",
        now, failure_id,
    )
    return status == "UPDATE 1"


async def query_failures(
    *,
    task_id: str | None = None,
    resolved: bool | None = None,
    error_class: str | None = None,
    limit: int = 50,
) -> list[FailureRecord]:
    """Query failures with filters."""
    conditions: list[str] = []
    params: list[Any] = []
    param_idx = 1

    if task_id is not None:
        conditions.append(f"task_id = ${param_idx}")
        params.append(task_id)
        param_idx += 1

    if resolved is not None:
        conditions.append(f"resolved = ${param_idx}")
        params.append(resolved)
        param_idx += 1

    if error_class is not None:
        conditions.append(f"error_class = ${param_idx}")
        params.append(error_class)
        param_idx += 1

    where_clause = " AND ".join(conditions) if conditions else "TRUE"
    query = f"SELECT * FROM failures WHERE {where_clause} ORDER BY created_at DESC LIMIT ${param_idx}"
    params.append(limit)
    rows = await fetch(query, *params)

    return [
        FailureRecord(
            failure_id=row["failure_id"],
            task_id=row["task_id"],
            symptom=row["symptom"],
            error_class=row["error_class"],
            root_cause=row["root_cause"],
            attempted_fix=row["attempted_fix"],
            result=row["result"],
            files=row["files"] or [],
            tests=row["tests"] or [],
            environment=row["environment"] or {},
            resolved=row["resolved"],
            created_at=row["created_at"],
            resolved_at=row.get("resolved_at"),
        )
        for row in rows
    ]