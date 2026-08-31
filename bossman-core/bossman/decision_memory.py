"""Decision Memory with supersession.

Structured decisions with scope, rationale, and the ability to supersede
older decisions while preserving history.

Suggested schema (from spec):
{
  "decision_id": "...",
  "scope": "...",
  "subject": "...",
  "decision": "...",
  "reason": "...",
  "alternatives_rejected": [],
  "evidence": [],
  "valid_from": "...",
  "supersedes": null
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
class DecisionRecord:
    """A structured decision record."""

    id: int
    decision_id: str
    scope: str
    subject: str
    decision: str
    reason: str
    alternatives_rejected: list[Any]
    evidence: list[Any]
    valid_from: datetime
    supersedes: int | None
    source_kind: str = "agent"
    source_run_id: int | None = None
    source_note: str = ""
    confidence: float = 1.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ──────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────

DECISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id              BIGSERIAL PRIMARY KEY,
    decision_id     TEXT NOT NULL UNIQUE,
    scope           TEXT NOT NULL,
    subject         TEXT NOT NULL,
    decision        TEXT NOT NULL,
    reason          TEXT,
    alternatives_rejected TEXT NOT NULL DEFAULT '[]',
    evidence        TEXT NOT NULL DEFAULT '[]',
    valid_from      TIMESTAMP NOT NULL,
    supersedes      BIGINT,
    source_kind     TEXT NOT NULL DEFAULT 'agent',
    source_run_id   INTEGER,
    source_note     TEXT,
    confidence      REAL NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decisions_scope ON decisions(scope, subject);
CREATE INDEX IF NOT EXISTS idx_decisions_id ON decisions(decision_id);
"""


# ──────────────────────────────────────────────────────────────────────
# CRUD operations
# ──────────────────────────────────────────────────────────────────────

async def init_decisions_table() -> None:
    """Initialize the decisions table (call during startup).

    asyncpg has no executescript/commit; simple-query execute() runs the
    multi-statement DDL. Canonical schema lives in db/schema.sql (JSONB/BIGSERIAL);
    this IF-NOT-EXISTS DDL is a Postgres-valid fallback only.
    """
    await execute(DECISION_SCHEMA)


async def create_decision(
    decision_id: str,
    scope: str,
    subject: str,
    decision: str,
    reason: str,
    *,
    alternatives_rejected: list[Any] | None = None,
    evidence: list[Any] | None = None,
    valid_from: datetime | None = None,
    source_kind: str = "agent",
    source_run_id: int | None = None,
    source_note: str = "",
    confidence: float = 1.0,
) -> DecisionRecord:
    """Create a new decision.

    A new decision may SUPERSEDE an old one. Historical decisions are
    never deleted - only marked as superseded.
    """

    alternatives = alternatives_rejected or []
    evidence = evidence or []
    valid = valid_from or datetime.now(timezone.utc)

    async with (await pool()) as conn:
        row = await conn.fetchrow(
            """INSERT INTO decisions
               (decision_id, scope, subject, decision, reason,
                alternatives_rejected, evidence, valid_from,
                source_kind, source_run_id, source_note, confidence,
                created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now(), now())
               RETURNING id, decision_id, scope, subject, decision, reason,
                         alternatives_rejected, evidence, valid_from,
                         supersedes, source_kind, source_run_id,
                         source_note, confidence, created_at, updated_at""",
            decision_id,
            scope,
            subject,
            decision,
            reason,
            json.dumps(alternatives),
            json.dumps(evidence),
            valid,
            source_kind,
            source_run_id,
            source_note,
            confidence,
        )
        return DecisionRecord(
            id=row["id"],
            decision_id=row["decision_id"],
            scope=row["scope"],
            subject=row["subject"],
            decision=row["decision"],
            reason=row["reason"],
            alternatives_rejected=json.loads(row["alternatives_rejected"]),
            evidence=json.loads(row["evidence"]),
            valid_from=row["valid_from"],
            supersedes=row["supersedes"],
            source_kind=row["source_kind"],
            source_run_id=row["source_run_id"],
            source_note=row["source_note"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


async def get_decision(decision_id: str) -> DecisionRecord | None:
    """Get a decision by its decision_id."""
    async with (await pool()) as conn:
        row = await fetchrow(
            "SELECT * FROM decisions WHERE decision_id = $1",
            decision_id,
        )

    if row is None:
        return None

    return DecisionRecord(
        id=row["id"],
        decision_id=row["decision_id"],
        scope=row["scope"],
        subject=row["subject"],
        decision=row["decision"],
        reason=row["reason"],
        alternatives_rejected=json.loads(row["alternatives_rejected"]),
        evidence=json.loads(row["evidence"]),
        valid_from=row["valid_from"],
        supersedes=row["supersedes"],
        source_kind=row["source_kind"],
        source_run_id=row["source_run_id"],
        source_note=row["source_note"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def supersede_decision(
    old_decision_id: str,
    new_decision_id: str,
    *,
    valid_from: datetime | None = None,
) -> dict[str, Any]:
    """Supersede an old decision with a new one.

    The old decision remains in the database but is marked as superseded.
    Only the most recent decision valid for a given (scope, subject) pair
    is considered currently valid.
    """

    valid = valid_from or datetime.now(timezone.utc)

    # First check the old decision exists (fail before any write).
    old = await get_decision(old_decision_id)
    if old is None:
        raise errors.NotFound(f"Decision {old_decision_id} not found")

    # Insert the new decision (superseding record).
    new = await create_decision(
        decision_id=new_decision_id,
        scope=old.scope,
        subject=old.subject,
        decision=old.decision,
        reason=old.reason,
        alternatives_rejected=old.alternatives_rejected,
        evidence=old.evidence,
        valid_from=valid,
        source_kind=old.source_kind,
        source_run_id=old.source_run_id,
        source_note=old.source_note,
        confidence=old.confidence,
    )

    # Mark old as superseded by new (asyncpg: module execute(), no commit()).
    await execute(
        "UPDATE decisions SET supersedes = $1, updated_at = now() WHERE id = $2",
        new.id, old.id,
    )

    return {
        "old_decision_id": old_decision_id,
        "new_decision_id": new_decision_id,
        "supersedes": new.id,
    }


async def query_decisions(
    *,
    scope: str | None = None,
    subject: str | None = None,
    current_only: bool = True,
    limit: int = 50,
) -> list[DecisionRecord]:
    """Query decisions.

    If current_only=True, only return decisions that are not superseded
    (i.e., currently valid decisions).
    """
    async with (await pool()) as conn:
        if current_only:
            query = """SELECT * FROM decisions
                       WHERE supersedes IS NULL
                       AND (scope = $1 OR $1 IS NULL)
                       AND (subject = $2 OR $2 IS NULL)
                       ORDER BY valid_from DESC
                       LIMIT $3"""
            rows = await conn.fetch(query, scope, subject, limit)
        else:
            query = """SELECT * FROM decisions
                       ORDER BY valid_from DESC
                       LIMIT $1"""
            rows = await conn.fetch(query, limit)

    return [
        DecisionRecord(
            id=row["id"],
            decision_id=row["decision_id"] if "decision_id" in row else f"dec-{row['id']}",
            scope=row["scope"],
            subject=row["subject"],
            decision=row["decision"],
            reason=row["reason"],
            alternatives_rejected=json.loads(row["alternatives_rejected"])
            if row["alternatives_rejected"]
            else [],
            evidence=json.loads(row["evidence"]) if row["evidence"] else [],
            valid_from=row["valid_from"],
            supersedes=row["supersedes"],
            source_kind=row["source_kind"],
            source_run_id=row["source_run_id"],
            source_note=row["source_note"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


# ──────────────────────────────────────────────────────────────────────
# Convenience
# ──────────────────────────────────────────────────────────────────────

async def record_decision_unsafe(
    decision_id: str,
    scope: str,
    subject: str,
    decision: str,
    reason: str,
    *,
    alternatives_rejected: list[Any] | None = None,
    evidence: list[Any] | None = None,
) -> DecisionRecord:
    """Record a decision without supersession logic (use with care).

    This bypasses the supersession mechanism and simply adds a new decision.
    Use only when you want to preserve historical decisions without
    replacing old ones.
    """
    alternatives = alternatives_rejected or []
    evidence = evidence or []

    async with (await pool()) as conn:
        row = await conn.fetchrow(
            """INSERT INTO decisions
               (decision_id, scope, subject, decision, reason,
                alternatives_rejected, evidence, valid_from,
                source_kind, source_run_id, source_note, confidence,
                created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, now(), $8, $9, $10, $11, now(), now())
               RETURNING id, decision_id, scope, subject, decision, reason,
                         alternatives_rejected, evidence, valid_from,
                         supersedes, source_kind, source_run_id,
                         source_note, confidence, created_at, updated_at""",
            decision_id,
            scope,
            subject,
            decision,
            reason,
            json.dumps(alternatives),
            json.dumps(evidence),
            "agent",
            None,
            "",
            1.0,
        )
        return DecisionRecord(
            id=row["id"],
            decision_id=row["decision_id"],
            scope=row["scope"],
            subject=row["subject"],
            decision=row["decision"],
            reason=row["reason"],
            alternatives_rejected=json.loads(row["alternatives_rejected"]),
            evidence=json.loads(row["evidence"]),
            valid_from=row["valid_from"],
            supersedes=row["supersedes"],
            source_kind=row["source_kind"],
            source_run_id=row["source_run_id"],
            source_note=row["source_note"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )