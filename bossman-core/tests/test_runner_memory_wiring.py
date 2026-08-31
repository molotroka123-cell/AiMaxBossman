"""Каноничная память в реальном production hot path (bossman/runner.py).

До этого прохода WorkingMemory/decision_memory/failure_memory были доказаны
на живом Postgres (test_pg_memory_gate.py), но ни один production entrypoint
их не вызывал — producer без потребителя. runner.py теперь пишет состояние
задачи в WorkingMemory на старте/финише, решение об облачной эскалации в
decision_memory, и провал задачи в failure_memory. Здесь — доказательство
теми же вызовами и той же формой аргументов, что использует сам runner.py,
а не абстрактный API memory-слоя.

Без `BOSSMAN_TEST_PG_DSN` — честный SKIP_HOST.
"""
from __future__ import annotations

import os

import pytest

DSN = os.getenv("BOSSMAN_TEST_PG_DSN")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="SKIP_HOST: no BOSSMAN_TEST_PG_DSN (real PostgreSQL) available"),
]


@pytest.fixture()
async def pg(monkeypatch):
    monkeypatch.setenv("BOSSMAN_DATABASE_URL", DSN)
    from bossman import db
    from bossman.config import settings
    monkeypatch.setattr(settings, "database_url", DSN, raising=False)
    await db.close()
    yield db
    await db.close()


async def test_runner_helper_writes_task_state_exactly_as_run_task_does(pg):
    """Тот же вызов, что и `await _record_memory(_WM.create_task_state(...))`
    в начале run_task, плюс финальный update_task_state на завершении."""
    from bossman.runner import _WM, _record_memory
    tid = "pytest-runner-wire-1"
    await pg.execute("DELETE FROM working_memory WHERE task_id=$1", tid)

    await _record_memory(_WM.create_task_state(tid, "тестовая задача"[:4000]), "create_task_state")
    state = await _WM.get_task_state(tid)
    assert state is not None and state["objective"] == "тестовая задача"

    await _record_memory(_WM.update_task_state(
        tid, {"status": "completed", "current_step": "итог"[:2000]}), "update_task_state")
    state = await _WM.get_task_state(tid)
    assert state["status"] == "completed" and state["current_step"] == "итог"


async def test_runner_helper_records_cloud_escalation_decision(pg):
    """Тот же вызов, что и в ветке NeedsCloudApproval внутри run_task."""
    import bossman.decision_memory as dm
    from bossman.runner import _record_memory
    tid = "pytest-runner-wire-2"
    decision_id = f"cloud-escalation-{tid}"
    await pg.execute("DELETE FROM decisions WHERE decision_id=$1", decision_id)

    await _record_memory(dm.create_decision(
        decision_id, "cost_control", f"task {tid}: cloud call to gpt-x",
        "approved", "owner approved cloud escalation",
        source_kind="approval", source_run_id=1), "create_decision")

    got = await dm.get_decision(decision_id)
    assert got is not None and got.decision == "approved" and got.scope == "cost_control"


async def test_runner_helper_records_task_failure(pg):
    """Тот же вызов, что и в хвосте run_task при status != 'done'."""
    import bossman.failure_memory as fm
    from bossman.runner import _record_memory
    tid = "pytest-runner-wire-3"
    await pg.execute("DELETE FROM failures WHERE task_id=$1", tid)

    await _record_memory(fm.record_failure(
        tid, "остановлено: превышен max_steps агента"[:2000], "task_failed",
        "остановлено: превышен max_steps агента"[:2000], "", "failed",
        environment={"agent": "coder", "steps": 40}), "record_failure")

    unresolved = await fm.get_unresolved_failures(tid)
    assert len(unresolved) == 1 and unresolved[0].error_class == "task_failed"


async def test_memory_write_failure_is_logged_not_raised(pg, caplog):
    """`_record_memory` — инструментация, а не часть контракта задачи: сбой
    памяти логируется и проглатывается, задача не должна падать из-за него."""
    from bossman.runner import _record_memory

    async def boom():
        raise RuntimeError("simulated memory outage")

    await _record_memory(boom(), "create_task_state")  # не должно бросить
    assert any("memory write skipped" in r.message for r in caplog.records)
