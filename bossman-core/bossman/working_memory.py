"""Durable Working State for LLM Architecture V2 (Bossman).

Structured active-task state, NOT chat history. Storage model: append-only
versioned rows — every material transition (update, append_*, record_*,
complete_step, set_next_action, set_status) writes a NEW row with version+1,
so each version IS a durable checkpoint and `restore(version)` is exact.
Optimistic concurrency: update(task_id, version=expected) conflicts when the
latest stored version differs, raising OptimisticConcurrencyConflict. No
silent overwrites, ever.

Connection contract: functions obtain a connection via `bossman.core.db.pool()`
(call-time lookup so tests can patch it) and use the minimal async interface
`execute(sql, params) -> cursor(fetchone/fetchall)` + `commit()` (aiosqlite
compatible). The asyncpg path shares the same DDL (db/schema.sql) but its
runtime adapter is NOT exercised in Phase 1 — see
docs/audit/AUDIT_LLM_ARCH_V2_FOUNDATION_CODEX.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import bossman.core.db as _core_db


class OptimisticConcurrencyConflict(Exception):
    """Raised when a working-memory update carries a stale expected version."""


class WorkingMemoryNotFound(Exception):
    """Raised when no working-memory row exists for a task_id."""


_JSON_FIELDS = (
    "constraints", "invariants", "decisions", "completed_steps",
    "pending_steps", "open_questions", "recent_failures",
    "observations", "artifacts", "relevant_files",
)
_TEXT_FIELDS = ("objective", "status", "current_step", "next_action_text")
_SCALAR_FIELDS = ("plan_version", "context_version")


@dataclass
class WorkingMemoryRecord:
    id: int | None
    task_id: str
    objective: str
    status: str
    current_step: str | None = None
    plan_version: int = 1
    constraints: list = field(default_factory=list)
    invariants: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    completed_steps: list = field(default_factory=list)
    pending_steps: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    recent_failures: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    relevant_files: list = field(default_factory=list)
    next_action: dict | list | str | None = None
    context_version: int = 1
    version: int = 1
    created_at: str | None = None
    updated_at: str | None = None


_COLUMNS = (
    "id", "task_id", "objective", "status", "current_step", "plan_version",
    "constraints", "invariants", "decisions", "completed_steps",
    "pending_steps", "open_questions", "recent_failures", "observations",
    "artifacts", "relevant_files", "next_action", "context_version",
    "version", "created_at", "updated_at",
)

_SELECT = ("SELECT " + ", ".join(_COLUMNS) +
           " FROM working_memory WHERE task_id = ? ORDER BY version DESC LIMIT 1")
_SELECT_VERSION = ("SELECT " + ", ".join(_COLUMNS) +
                   " FROM working_memory WHERE task_id = ? AND version = ? LIMIT 1")
_SELECT_ALL = ("SELECT " + ", ".join(_COLUMNS) +
               " FROM working_memory WHERE task_id = ? ORDER BY version DESC")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value)
    return json.dumps(value, default=str)


def _load_json(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _record(row: tuple) -> WorkingMemoryRecord:
    d = dict(zip(_COLUMNS, row))
    for f in _JSON_FIELDS:
        d[f] = _load_json(d.get(f)) or []
    d["next_action"] = _load_json(d.get("next_action"))
    return WorkingMemoryRecord(**d)


async def _fetch_latest(conn, task_id: str) -> WorkingMemoryRecord | None:
    cur = await conn.execute(_SELECT, (task_id,))
    row = await cur.fetchone()
    return _record(row) if row else None


async def _write_row(conn, rec: WorkingMemoryRecord, *, created_at: str | None = None) -> WorkingMemoryRecord:
    params = (
        rec.task_id, rec.objective, rec.status, rec.current_step,
        int(rec.plan_version),
        *(_dump(getattr(rec, f)) for f in _JSON_FIELDS),
        _dump(rec.next_action) if rec.next_action is not None else None,
        int(rec.context_version), int(rec.version),
        created_at or _now_iso(), _now_iso(),
    )
    cols = ("task_id", "objective", "status", "current_step", "plan_version",
            *_JSON_FIELDS, "next_action", "context_version", "version",
            "created_at", "updated_at")
    sql = ("INSERT INTO working_memory (" + ", ".join(cols) + ") VALUES (" +
           ", ".join("?" for _ in cols) + ")")
    cur = await conn.execute(sql, params)
    new_id = getattr(cur, "lastrowid", None)
    cur2 = await conn.execute(
        "SELECT " + ", ".join(_COLUMNS) + " FROM working_memory WHERE task_id = ? AND version = ? LIMIT 1",
        (rec.task_id, int(rec.version)))
    row = await cur2.fetchone()
    await conn.commit()
    out = _record(row) if row else rec
    if out.id is None and new_id is not None:
        out.id = new_id
    return out


async def create(*, task_id: str, objective: str, status: str = "active",
                 constraints: list | None = None, invariants: list | None = None,
                 pending_steps: list | None = None) -> WorkingMemoryRecord:
    """Create version-1 working state for a task (PLAN_CREATED checkpoint)."""
    conn = await _core_db.pool()
    existing = await _fetch_latest(conn, task_id)
    if existing is not None:
        return existing
    rec = WorkingMemoryRecord(id=None, task_id=task_id, objective=objective,
                              status=status, version=1,
                              constraints=constraints or [],
                              invariants=invariants or [],
                              pending_steps=pending_steps or [])
    return await _write_row(conn, rec)


async def get(task_id: str) -> WorkingMemoryRecord | None:
    """Latest durable version of the task state."""
    conn = await _core_db.pool()
    return await _fetch_latest(conn, task_id)


async def restore(task_id: str, version: int) -> WorkingMemoryRecord | None:
    """Exact historical version (checkpoint restore, read-only)."""
    conn = await _core_db.pool()
    cur = await conn.execute(_SELECT_VERSION, (task_id, int(version)))
    row = await cur.fetchone()
    await conn.commit()
    return _record(row) if row else None


async def check_conflict(task_id: str, version: int) -> bool:
    """True when a row with (task_id, version) exists — i.e. not stale."""
    conn = await _core_db.pool()
    cur = await conn.execute(_SELECT_VERSION, (task_id, int(version)))
    row = await cur.fetchone()
    await conn.commit()
    return row is not None


async def list_by_task(task_id: str) -> list[WorkingMemoryRecord]:
    """All versions, newest first."""
    conn = await _core_db.pool()
    cur = await conn.execute(_SELECT_ALL, (task_id,))
    rows = await cur.fetchall()
    await conn.commit()
    return [_record(r) for r in rows]


async def update(*, task_id: str, version: int, **fields) -> WorkingMemoryRecord:
    """Optimistic-concurrency update: writes version+1, never overwrites.

    `version` is the caller's expected latest version. Supported fields:
    objective, status, current_step, plan_version, next_action, plus any
    JSON list field by name. Unknown fields raise ValueError.
    """
    allowed = {"objective", "status", "current_step", "plan_version",
               "next_action", "context_version", *_JSON_FIELDS}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unsupported working-memory fields: {sorted(bad)}")
    conn = await _core_db.pool()
    latest = await _fetch_latest(conn, task_id)
    if latest is None:
        raise WorkingMemoryNotFound(task_id)
    if int(latest.version) != int(version):
        raise OptimisticConcurrencyConflict(
            f"task={task_id} expected_version={version} actual_version={latest.version}")
    for name in set(fields) & (set(_JSON_FIELDS) | {"next_action"}):
        setattr(latest, name, fields[name])
    for name in set(fields) - set(_JSON_FIELDS) - {"next_action"}:
        setattr(latest, name, fields[name])
    latest.version = int(version) + 1
    return await _write_row(conn, latest)


async def _bump(task_id: str, mutate) -> WorkingMemoryRecord:
    conn = await _core_db.pool()
    latest = await _fetch_latest(conn, task_id)
    if latest is None:
        raise WorkingMemoryNotFound(task_id)
    mutate(latest)
    latest.version = int(latest.version) + 1
    return await _write_row(conn, latest)


async def append_observation(*, task_id: str, observation: dict) -> WorkingMemoryRecord:
    """OBSERVATION_RECEIVED checkpoint: append one bounded observation dict."""
    return await _bump(task_id, lambda r: r.observations.append(observation))


async def append_failure(*, task_id: str, failure: dict) -> WorkingMemoryRecord:
    """FAILURE_DETECTED checkpoint: append symptom/cause/fix dict."""
    return await _bump(task_id, lambda r: r.recent_failures.append(failure))


async def record_decision(*, task_id: str, decision: dict) -> WorkingMemoryRecord:
    """DECISION_MADE checkpoint: append decision dict (append-only history)."""
    return await _bump(task_id, lambda r: r.decisions.append(decision))


async def complete_step(*, task_id: str, step_id: str) -> WorkingMemoryRecord:
    """Mark a step completed and drop it from pending."""
    def _mut(r: WorkingMemoryRecord) -> None:
        if step_id not in r.completed_steps:
            r.completed_steps.append(step_id)
        if step_id in r.pending_steps:
            r.pending_steps.remove(step_id)
    return await _bump(task_id, _mut)


async def set_next_action(*, task_id: str, next_action: dict | None) -> WorkingMemoryRecord:
    return await _bump(task_id, lambda r: setattr(r, "next_action", next_action))


async def set_status(*, task_id: str, status: str) -> WorkingMemoryRecord:
    return await _bump(task_id, lambda r: setattr(r, "status", status))


async def checkpoint(task_id: str, reason: str = "") -> WorkingMemoryRecord:
    """Durable checkpoint: versioned rows are themselves checkpoints, so this
    returns the latest persisted version. `reason` is advisory only — material
    events already persist via the append_*/update operations above."""
    latest = await get(task_id)
    if latest is None:
        raise WorkingMemoryNotFound(task_id)
    return latest
