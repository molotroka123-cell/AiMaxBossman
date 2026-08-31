"""Working Memory — типизированный VIEW поверх ЕДИНОЙ каноничной персистентности.

Канон (одна авторитетность памяти):
* durable-хранилище — Postgres через `bossman.db` (пул сам применяет
  `db/schema.sql` и регистрирует jsonb-кодек);
* DDL здесь НЕТ: схема таблиц `working_memory` / `working_memory_versions`
  принадлежит `db/schema.sql`;
* ключ состояния — `task_id` (одна активная строка на задачу, UNIQUE(task_id)),
  как и у сиблингов decision/failure memory. Проектный скоуп выводится из
  `tasks.project_id`, дублировать его здесь не нужно;
* версии — append-only снапшоты в `working_memory_versions` (checkpoint/restore);
* конкурентность — оптимистическая по колонке `version` (SELECT ... FOR UPDATE).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .db import fetch, fetchrow, pool

# JSONB-колонки: значения передаём НАТИВНЫМИ объектами — кодек пула кодирует их
# ровно один раз (ручной json.dumps здесь дал бы двойное кодирование).
_JSON_FIELDS = (
    "constraints", "invariants", "decisions", "completed_steps", "pending_steps",
    "open_questions", "recent_failures", "observations", "artifacts",
    "relevant_files", "next_action",
)

_STATE_COLUMNS = (
    "objective", "status", "current_step", "plan_version", "context_version",
) + _JSON_FIELDS


class OptimisticConcurrencyConflict(Exception):
    """Версия строки изменилась между чтением и записью — запись отклонена."""


# Обратно-совместимый алиас (исторические импорты).
ConcurrencyError = OptimisticConcurrencyConflict


class WorkingMemory:
    """Типизированный view активного состояния задачи над каноничным Postgres."""

    async def create_task_state(
        self,
        task_id: str,
        objective: str,
        constraints: Optional[List[Any]] = None,
        invariants: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Создать состояние задачи (идемпотентно) и снять снапшот версии 1."""
        async with (await pool()).acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO working_memory (task_id, objective, constraints, invariants)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (task_id) DO NOTHING""",
                    task_id, objective, constraints or [], invariants or [])
                row = await conn.fetchrow(
                    "SELECT * FROM working_memory WHERE task_id = $1", task_id)
                await self._snapshot(conn, dict(row))
                return dict(row)

    async def get_task_state(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = await fetchrow("SELECT * FROM working_memory WHERE task_id = $1", task_id)
        return dict(row) if row else None

    async def update_task_state(
        self,
        task_id: str,
        updates: Dict[str, Any],
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Обновить состояние с оптимистической конкурентностью.

        `expected_version` не совпал с текущим → OptimisticConcurrencyConflict
        ДО какой-либо записи. Версия инкрементируется, снимается снапшот.
        """
        allowed = {k: v for k, v in (updates or {}).items() if k in _STATE_COLUMNS}
        if not allowed:
            raise ValueError("no updatable columns in updates")

        async with (await pool()).acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchrow(
                    "SELECT version FROM working_memory WHERE task_id = $1 FOR UPDATE", task_id)
                if not current:
                    raise ValueError(f"Task {task_id} not found")
                if expected_version is not None and current["version"] != expected_version:
                    raise OptimisticConcurrencyConflict(
                        f"Version mismatch: expected {expected_version}, got {current['version']}")

                sets, params = [], []
                for i, (key, value) in enumerate(allowed.items(), start=1):
                    sets.append(f"{key} = ${i}")
                    params.append(value)
                idx = len(params) + 1
                sets += ["version = version + 1", "updated_at = now()"]
                row = await conn.fetchrow(
                    f"UPDATE working_memory SET {', '.join(sets)} WHERE task_id = ${idx} RETURNING *",
                    *params, task_id)
                await self._snapshot(conn, dict(row))
                return dict(row)

    @staticmethod
    async def _snapshot(conn, state: Dict[str, Any]) -> None:
        """Append-only снапшот текущей версии (идемпотентен по (wm_id, version))."""
        await conn.execute(
            """INSERT INTO working_memory_versions (working_memory_id, task_id, version, snapshot)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (working_memory_id, version) DO NOTHING""",
            state["id"], state["task_id"], state["version"],
            {k: _jsonable(v) for k, v in state.items()})

    async def list_versions(self, task_id: str) -> List[Dict[str, Any]]:
        return await fetch(
            """SELECT version, created_at FROM working_memory_versions
               WHERE task_id = $1 ORDER BY version DESC""", task_id)

    async def restore_version(self, task_id: str, version: int) -> Dict[str, Any]:
        """Восстановить состояние из снапшота версии (создаёт новую версию)."""
        snap = await fetchrow(
            """SELECT snapshot FROM working_memory_versions
               WHERE task_id = $1 AND version = $2""", task_id, version)
        if not snap:
            raise ValueError(f"Version {version} not found for task {task_id}")
        state = snap["snapshot"]
        restore = {k: state.get(k) for k in _STATE_COLUMNS if k in state}
        return await self.update_task_state(task_id, restore)

    async def checkpoint(self, task_id: str) -> Dict[str, Any]:
        state = await self.get_task_state(task_id)
        if not state:
            raise ValueError(f"Task {task_id} not found")
        return {"task_id": task_id, "version": state["version"],
                "timestamp": datetime.now(timezone.utc).isoformat(), "state": state}

    async def restore_checkpoint(self, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        return await self.restore_version(checkpoint["task_id"], checkpoint["version"])


def _jsonable(v: Any) -> Any:
    """datetime → ISO-строка: снапшот должен быть валидным JSON."""
    return v.isoformat() if isinstance(v, datetime) else v
