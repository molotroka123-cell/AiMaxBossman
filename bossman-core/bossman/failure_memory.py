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
from .db import pool, fetchrow, fetchval, execute


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

FAILURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS failures (
    failure_id      TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    symptom         TEXT NOT NULL,
    error_class     TEXT NOT NULL,
    root_cause      TEXT NOT NULL,
    attempted_fix   TEXT,
    result          TEXT,
    files           TEXT NOT NULL DEFAULT '[]',
    tests           TEXT NOT NULL DEFAULT '[]',
    environment     TEXT NOT NULL DEFAULT '{}',
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_failures_task ON failures(task_id);
CREATE INDEX IF NOT EXISTS idx_failures_resolved ON failures(resolved);
"""


# ──────────────────────────────────────────────────────────────────────
# CRUD operations
# ──────────────────────────────────────────────────────────────────────

async def init_failures_table() -> None:
    """Initialize the failures table (call during startup)."""
    async with (await pool()) as conn:
        await conn.executescript(FAILURE_SCHEMA)
        await conn.commit()


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
    files_json = json.dumps(files or [])
    tests_json = json.dumps(tests or [])
    env_json = json.dumps(environment or {})

    async with (await pool()) as conn:
        row = await conn.fetchrow(
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
            files_json,
            tests_json,
            env_json,
        )
        return FailureRecord(
            failure_id=row["failure_id"],
            task_id=row["task_id"],
            symptom=row["symptom"],
            error_class=row["error_class"],
            root_cause=row["root_cause"],
            attempted_fix=row["attempted_fix"],
            result=row["result"],
            files=json.loads(row["files"]),
            tests=json.loads(row["tests"]),
            environment=json.loads(row["environment"]),
            resolved=row["resolved"],
            created_at=row["created_at"],
        )


async def get_failure(failure_id: str) -> FailureRecord | None:
    """Get a failure by its failure_id."""
    async with (await pool()) as conn:
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
        files=json.loads(row["files"]),
        tests=json.loads(row["tests"]),
        environment=json.loads(row["environment"]),
        resolved=row["resolved"],
        created_at=row["created_at"],
        resolved_at=row.get("resolved_at"),
    )


async def get_unresolved_failures(task_id: str) -> list[FailureRecord]:
    """Get all unresolved failures for a task."""
    async with (await pool()) as conn:
        rows = await conn.execute(
            "SELECT * FROM failures WHERE task_id = $1 AND resolved = FALSE ORDER BY created_at DESC",
            task_id,
        )
        records = []
        async for row in rows:
            records.append(FailureRecord(
                failure_id=row["failure_id"],
                task_id=row["task_id"],
                symptom=row["symptom"],
                error_class=row["error_class"],
                root_cause=row["root_cause"],
                attempted_fix=row["attempted_fix"],
                result=row["result"],
                files=json.loads(row["files"]),
                tests=json.loads(row["tests"]),
                environment=json.loads(row["environment"]),
                resolved=row["resolved"],
                created_at=row["created_at"],
                resolved_at=row.get("resolved_at"),
            ))
        return records


async def resolve_failure(failure_id: str) -> bool:
    """Mark a failure as resolved."""
    now = datetime.now(timezone.utc)
    async with (await pool()) as conn:
        result = await conn.execute(
            "UPDATE failures SET resolved = TRUE, resolved_at = $1 WHERE failure_id = $2",
            now, failure_id,
        )
        return result.status == "UPDATE 1"  # postgres returns "UPDATE 1" if row updated


async def query_failures(
    *,
    task_id: str | None = None,
    resolved: bool | None = None,
    error_class: str | None = None,
    limit: int = 50,
) -> list[FailureRecord]:
    """Query failures with filters."""
    async with (await pool()) as conn:
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

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM failures WHERE {where_clause} ORDER BY created_at DESC LIMIT ${param_idx}"
        params.append(limit)

        rows = await conn.execute(query, *params)
        # Actually need to fetch them
        # Let me use fetch instead
        async with (await pool()) as conn:
            q_where = " WHERE " + " AND ".join(conditions) if conditions else " "
            q_params = params[:-1]  # remove limit
            query = f"SELECT * FROM failures {q_where} ORDER BY created_at DESC LIMIT ${param_idx}"
            q_params.append(limit)
            rows = await conn.fetch(query, *q_params)

    return [
        FailureRecord(
            failure_id=row["failure_id"],
            task_id=row["task_id"],
            symptom=row["symptom"],
            error_class=row["error_class"],
            root_cause=row["root_cause"],
            attempted_fix=row["attempted_fix"],
            result=row["result"],
            files=json.loads(row["files"]) if row["files"] else [],
            tests=json.loads(row["tests"]) if row["tests"] else [],
            environment=json.loads(row["environment"]) if row["environment"] else {},
            resolved=row["resolved"],
            created_at=row["created_at"],
            resolved_at=row.get("resolved_at"),
        )
        for row in rows
    ]