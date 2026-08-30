"""
Bossman V2 Working Memory - PostgreSQL/asyncpg implementation
Canonical production DB contract for active task state management.
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import asyncpg

logger = logging.getLogger(__name__)


class WorkingMemory:
    """
    Production-grade Working Memory using PostgreSQL/asyncpg.
    Implements optimistic concurrency, append-only versions, and exact restore.
    """
    
    def __init__(self, db_pool: asyncpg.Pool, project_id: int):
        self.db = db_pool
        self.project_id = project_id
        self._version_cache: Dict[str, int] = {}
    
    async def create_task_state(
        self,
        task_id: str,
        objective: str,
        constraints: List[Dict] = None,
        invariants: List[Dict] = None
    ) -> Dict[str, Any]:
        """Create a new task state with version 1."""
        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO working_memory 
                (project_id, task_id, objective, constraints, invariants, version)
                VALUES ($1, $2, $3, $4, $5, 1)
                ON CONFLICT (project_id, task_id) DO NOTHING
                """,
                self.project_id, task_id, objective,
                json.dumps(constraints or []), json.dumps(invariants or [])
            )
            
            # Create initial version snapshot
            state = await self.get_task_state(task_id)
            await self._create_version_snapshot(conn, task_id, state)
            
            return state
    
    async def get_task_state(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get current task state."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM working_memory
                WHERE project_id = $1 AND task_id = $2
                """,
                self.project_id, task_id
            )
            
            if not row:
                return None
            
            return dict(row)
    
    async def update_task_state(
        self,
        task_id: str,
        updates: Dict[str, Any],
        expected_version: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Update task state with optimistic concurrency control.
        Returns updated state or raises ConcurrencyError.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Get current version
                current = await conn.fetchrow(
                    """
                    SELECT version FROM working_memory
                    WHERE project_id = $1 AND task_id = $2
                    FOR UPDATE
                    """,
                    self.project_id, task_id
                )
                
                if not current:
                    raise ValueError(f"Task {task_id} not found")
                
                current_version = current['version']
                
                # Check optimistic concurrency
                if expected_version is not None and current_version != expected_version:
                    raise ConcurrencyError(
                        f"Version mismatch: expected {expected_version}, got {current_version}"
                    )
                
                # Build update query
                set_clauses = []
                params = []
                param_idx = 1
                
                for key, value in updates.items():
                    if key in ['constraints', 'invariants', 'decisions', 'completed_steps',
                              'pending_steps', 'open_questions', 'recent_failures',
                              'observations', 'artifacts', 'relevant_files']:
                        set_clauses.append(f"{key} = ${param_idx}")
                        params.append(json.dumps(value))
                    else:
                        set_clauses.append(f"{key} = ${param_idx}")
                        params.append(value)
                    param_idx += 1
                
                # Always increment version
                set_clauses.append(f"version = version + 1")
                set_clauses.append(f"updated_at = NOW()")
                
                query = f"""
                    UPDATE working_memory
                    SET {', '.join(set_clauses)}
                    WHERE project_id = ${param_idx} AND task_id = ${param_idx + 1}
                    RETURNING *
                """
                params.extend([self.project_id, task_id])
                
                row = await conn.fetchrow(query, *params)
                
                # Create version snapshot
                await self._create_version_snapshot(conn, task_id, dict(row))
                
                return dict(row)
    
    async def _create_version_snapshot(
        self,
        conn: asyncpg.Connection,
        task_id: str,
        state: Dict[str, Any]
    ) -> None:
        """Create append-only version snapshot."""
        await conn.execute(
            """
            INSERT INTO working_memory_versions (working_memory_id, version, snapshot)
            SELECT id, version, $1
            FROM working_memory
            WHERE project_id = $2 AND task_id = $3
            """,
            json.dumps(state), self.project_id, task_id
        )
    
    async def restore_version(self, task_id: str, version: int) -> Dict[str, Any]:
        """Restore task state to specific version."""
        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Get version snapshot
                snapshot = await conn.fetchrow(
                    """
                    SELECT snapshot FROM working_memory_versions
                    WHERE working_memory_id = (
                        SELECT id FROM working_memory
                        WHERE project_id = $1 AND task_id = $2
                    ) AND version = $3
                    """,
                    self.project_id, task_id, version
                )
                
                if not snapshot:
                    raise ValueError(f"Version {version} not found for task {task_id}")
                
                # Restore state
                state = json.loads(snapshot['snapshot'])
                
                # Update current state
                await conn.execute(
                    """
                    UPDATE working_memory
                    SET 
                        objective = $1,
                        status = $2,
                        current_step = $3,
                        plan_version = $4,
                        constraints = $5,
                        invariants = $6,
                        decisions = $7,
                        completed_steps = $8,
                        pending_steps = $9,
                        open_questions = $10,
                        recent_failures = $11,
                        observations = $12,
                        artifacts = $13,
                        relevant_files = $14,
                        next_action = $15,
                        context_version = $16,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE project_id = $17 AND task_id = $18
                    """,
                    state.get('objective'),
                    state.get('status'),
                    state.get('current_step'),
                    state.get('plan_version'),
                    json.dumps(state.get('constraints', [])),
                    json.dumps(state.get('invariants', [])),
                    json.dumps(state.get('decisions', [])),
                    json.dumps(state.get('completed_steps', [])),
                    json.dumps(state.get('pending_steps', [])),
                    json.dumps(state.get('open_questions', [])),
                    json.dumps(state.get('recent_failures', [])),
                    json.dumps(state.get('observations', [])),
                    json.dumps(state.get('artifacts', [])),
                    json.dumps(state.get('relevant_files', [])),
                    state.get('next_action'),
                    state.get('context_version'),
                    self.project_id,
                    task_id
                )
                
                return await self.get_task_state(task_id)
    
    async def list_versions(self, task_id: str) -> List[Dict[str, Any]]:
        """List all versions for a task."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT version, created_at FROM working_memory_versions
                WHERE working_memory_id = (
                    SELECT id FROM working_memory
                    WHERE project_id = $1 AND task_id = $2
                )
                ORDER BY version DESC
                """,
                self.project_id, task_id
            )
            
            return [dict(row) for row in rows]
    
    async def checkpoint(self, task_id: str) -> Dict[str, Any]:
        """Create a checkpoint of current state."""
        state = await self.get_task_state(task_id)
        if not state:
            raise ValueError(f"Task {task_id} not found")
        
        return {
            'task_id': task_id,
            'version': state['version'],
            'timestamp': datetime.utcnow().isoformat(),
            'state': state
        }
    
    async def restore_checkpoint(self, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        """Restore from checkpoint."""
        return await self.restore_version(
            checkpoint['task_id'],
            checkpoint['version']
        )


class ConcurrencyError(Exception):
    """Raised when optimistic concurrency check fails."""
    pass
