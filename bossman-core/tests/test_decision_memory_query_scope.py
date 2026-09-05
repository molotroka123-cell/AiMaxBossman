"""Execute decision retrieval SQL against controlled rows, without a live PG claim."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from bossman import decision_memory as memory


@pytest.fixture
def decisions(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    connection.create_function("now", 0, lambda: now.isoformat())
    connection.execute("""CREATE TABLE decisions (
        id INTEGER PRIMARY KEY, decision_id TEXT, scope TEXT, subject TEXT,
        decision TEXT, reason TEXT, alternatives_rejected TEXT, evidence TEXT,
        valid_from TEXT, supersedes INTEGER, source_kind TEXT, source_run_id INTEGER,
        source_note TEXT, confidence REAL, created_at TEXT, updated_at TEXT)""")
    for ident, scope, subject, offset, successor in (
        (1, "project-a", "database", -2, 2),
        (2, "project-a", "database", -1, None),
        (3, "project-b", "database", -1, None),
        (4, "project-a", "deployment", -1, None),
        (5, "project-a", "database", 1, None),
    ):
        connection.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, f"decision-{ident}", scope, subject, "controlled", "fixture", None, None,
             (now + timedelta(days=offset)).isoformat(), successor, "owner", None,
             "", 1.0, now.isoformat(), now.isoformat()))

    class Connection:
        async def fetch(self, sql, *params):
            # SQLite understands the same numbered $ parameters used here.
            return [dict(row) for row in connection.execute(sql, params)]

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield Connection()

    async def pool():
        return Pool()

    monkeypatch.setattr(memory, "pool", pool)
    yield
    connection.close()


@pytest.mark.asyncio
async def test_historical_decisions_keep_requested_scope_and_subject(decisions):
    rows = await memory.query_decisions(scope="project-a", subject="database", current_only=False)
    assert {row.id for row in rows} == {1, 2, 5}


@pytest.mark.asyncio
async def test_current_decisions_exclude_future_and_superseded_rows(decisions):
    rows = await memory.query_decisions(scope="project-a", subject="database")
    assert [row.id for row in rows] == [2]


@pytest.mark.asyncio
async def test_historical_subject_filter_works_without_scope(decisions):
    rows = await memory.query_decisions(subject="deployment", current_only=False)
    assert [row.id for row in rows] == [4]
