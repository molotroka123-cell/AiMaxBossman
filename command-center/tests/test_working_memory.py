"""Tests for Working Memory V2 - durable structured memory with versioning.

Phase 1: CRUD operations, version conflict detection, checkpoint/restore.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import shutil
from datetime import datetime, timezone
from typing import Any

import pytest

# Add bossman-core to path so we can import from bossman.* packages
sys.path.insert(0, "C:\\AiMaxBossman-claude-bossman-control-v03-43igbk\\bossman-core")
sys.path.insert(0, "C:\\AiMaxBossman-claude-bossman-control-v03-43igbk\\command-center")

import aiosqlite


# Conflict error is owned by the implementation module (minimal fix of
# unpassable inline definition committed at 0516bb0 — see audit doc).
from bossman.working_memory import OptimisticConcurrencyConflict


@pytest.fixture
def tmp_db():
    """Provide a temporary SQLite database with working_memory table."""
    tmp_dir = tempfile.mkdtemp()
    db_path = f"{tmp_dir}/test_working_memory.db"

    # Create the working_memory table directly
    schema = """
    CREATE TABLE IF NOT EXISTS working_memory (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id         TEXT NOT NULL,
        objective       TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'active',
        current_step    TEXT,
        plan_version    INT NOT NULL DEFAULT 1,
        constraints     TEXT NOT NULL DEFAULT '[]',
        invariants      TEXT NOT NULL DEFAULT '[]',
        decisions       TEXT NOT NULL DEFAULT '[]',
        completed_steps TEXT NOT NULL DEFAULT '[]',
        pending_steps   TEXT NOT NULL DEFAULT '[]',
        open_questions  TEXT NOT NULL DEFAULT '[]',
        recent_failures TEXT NOT NULL DEFAULT '[]',
        observations    TEXT NOT NULL DEFAULT '[]',
        artifacts       TEXT NOT NULL DEFAULT '[]',
        relevant_files  TEXT NOT NULL DEFAULT '[]',
        next_action     TEXT,
        context_version INT NOT NULL DEFAULT 1,
        version         INT NOT NULL DEFAULT 1,
        created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_working_memory_task ON working_memory(task_id);
    CREATE INDEX IF NOT EXISTS idx_working_memory_version ON working_memory(task_id, version DESC);
    """

    async def setup():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(schema)
            await db.commit()

    asyncio.run(setup())
    yield db_path

    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_working_memory_crud(tmp_db):
    """Test basic create, get, update operations."""
    from bossman.working_memory import (
        create, get, update, append_observation, append_failure,
        record_decision, complete_step, set_next_action, checkpoint, restore,
        check_conflict, list_by_task,
    )

    async with aiosqlite.connect(tmp_db) as db:
        # Override the pool() to use our test database
        import bossman.core.db as bdb
        original_pool = bdb.pool

        async def test_pool():
            return db

        bdb.pool = test_pool

        try:
            # Test create
            record = await create(
                task_id="test-task-001",
                objective="Test objective",
                status="active",
            )

            assert record.task_id == "test-task-001"
            assert record.objective == "Test objective"
            assert record.status == "active"
            assert record.version == 1
            assert record.id is not None

            # Test get
            retrieved = await get("test-task-001")
            assert retrieved is not None
            assert retrieved.id == record.id
            assert retrieved.version == 1

            # Test get latest (should be same)
            retrieved_latest = await get("test-task-001")
            assert retrieved_latest.version == 1

            # Test update
            updated = await update(
                task_id="test-task-001",
                version=1,
                status="paused",
                current_step="step-1",
            )
            assert updated is not None
            assert updated.status == "paused"
            assert updated.current_step == "step-1"
            assert updated.version == 2  # version should increment

            # Test version conflict - use inline error check
            conflict_raised = False
            try:
                await update(
                    task_id="test-task-001",
                    version=1,  # old version - should conflict
                    status="should not happen",
                )
            except OptimisticConcurrencyConflict:
                conflict_raised = True
            assert conflict_raised, "Should have raised OptimisticConcurrencyConflict"

            # Test append_observation
            updated = await append_observation(
                task_id="test-task-001",
                observation={"action": "inspect", "result": "found thing"},
            )
            assert updated is not None
            assert len(updated.observations) == 1
            assert updated.observations[0]["action"] == "inspect"

            # Test append_failure
            updated = await append_failure(
                task_id="test-task-001",
                failure={"error": "value error", "symptom": "test symptom"},
            )
            assert updated is not None
            assert len(updated.recent_failures) == 1
            assert updated.recent_failures[0]["error"] == "value error"

            # Test record_decision
            updated = await record_decision(
                task_id="test-task-001",
                decision={"decision": "use local model", "reason": "cost optimization"},
            )
            assert updated is not None
            assert len(updated.decisions) == 1
            assert updated.decisions[0]["decision"] == "use local model"

            # Test complete_step
            updated = await complete_step(
                task_id="test-task-001",
                step_id="step-1",
            )
            assert updated is not None
            assert "step-1" in updated.completed_steps

            # Test set_next_action
            updated = await set_next_action(
                task_id="test-task-001",
                next_action={"action": "analyze", "priority": "high"},
            )
            assert updated is not None
            assert updated.next_action["action"] == "analyze"

            # Test checkpoint (should return current state)
            ckpt = await checkpoint("test-task-001")
            assert ckpt is not None
            assert ckpt.version == updated.version

            # Test restore to specific version
            restored = await restore("test-task-001", version=1)
            assert restored is not None
            assert restored.version == 1

            # Test check_conflict
            conflict_ok = await check_conflict("test-task-001", version=2)
            assert conflict_ok is True  # version 2 exists

            conflict_fail = await check_conflict("test-task-001", version=999)
            assert conflict_fail is False  # version 999 doesn't exist

            # Test list_by_task
            all_versions = await list_by_task("test-task-001")
            assert len(all_versions) >= 1
            # Should have versions 1 and 2
            versions = [v.version for v in all_versions]
            assert 2 in versions

        finally:
            bdb.pool = original_pool


@pytest.mark.asyncio
async def test_working_memory_version_conflict(tmp_db):
    """Test that version conflicts are detected properly."""
    from bossman.working_memory import create, update

    async with aiosqlite.connect(tmp_db) as db:
        import bossman.core.db as bdb
        original_pool = bdb.pool

        async def test_pool():
            return db

        bdb.pool = test_pool

        try:
            # Create a record
            record = await create(task_id="conflict-task", objective="test")
            assert record.version == 1

            # Update it (version becomes 2)
            updated = await update(
                task_id="conflict-task",
                version=1,
                status="updated",
            )
            assert updated.version == 2

            # Try to update with old version - should conflict
            conflict_raised = False
            try:
                await update(
                    task_id="conflict-task",
                    version=1,  # stale version
                    status="should fail",
                )
            except OptimisticConcurrencyConflict:
                conflict_raised = True
            assert conflict_raised, "Should have raised OptimisticConcurrencyConflict"

            # Update with new version should work
            updated2 = await update(
                task_id="conflict-task",
                version=2,
                status="second update",
            )
            assert updated2.version == 3

        finally:
            bdb.pool = original_pool


@pytest.mark.asyncio
async def test_working_memory_bulk_operations(tmp_db):
    """Test list_by_task and other bulk operations."""
    from bossman.working_memory import (
        create, update, append_observation, complete_step, list_by_task,
    )

    async with aiosqlite.connect(tmp_db) as db:
        import bossman.core.db as bdb
        original_pool = bdb.pool

        async def test_pool():
            return db

        bdb.pool = test_pool

        try:
            # Create and update multiple times
            await create(task_id="bulk-task", objective="bulk test")
            await update(task_id="bulk-task", version=1, status="in progress")
            await append_observation(task_id="bulk-task", observation={"step": 1})
            await complete_step(task_id="bulk-task", step_id="step-1")

            # List all versions
            all_versions = await list_by_task("bulk-task")
            assert len(all_versions) >= 1

            # Check versions are in descending order
            versions = [v.version for v in all_versions]
            assert versions[0] == max(versions)  # first should be latest

        finally:
            bdb.pool = original_pool