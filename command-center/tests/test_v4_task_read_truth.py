"""Current task results and bounded, read-only task-list queries."""
import pytest
import sqlalchemy as sa

from bcc.db import tasks, task_runs


@pytest.mark.parametrize("status", ["queued", "running", "failed", "waiting_approval", "blocked"])
async def test_old_success_is_history_not_current_result(env, status):
    async with env.svc.db.session() as session:
        result = await session.execute(sa.insert(tasks).values(title="rerun", prompt="read", status=status))
        tid = result.inserted_primary_key[0]
        await session.execute(sa.insert(task_runs).values(task_id=tid, status="completed", result="old artifact"))
        await session.execute(sa.insert(task_runs).values(task_id=tid, status=status, error="current failure"))
        await session.commit()
    response = await env.client.get(f"/api/tasks/{tid}")
    assert response.status_code == 200
    data = response.json()
    assert data["result"] is None
    assert data["runs"][0]["result"] == "old artifact"
    async with env.svc.db.session() as session:
        await session.execute(sa.update(tasks).where(tasks.c.id == tid).values(status="completed"))
        await session.execute(sa.insert(task_runs).values(task_id=tid, status="completed", result="new artifact"))
        await session.commit()
    assert (await env.client.get(f"/api/tasks/{tid}")).json()["result"] == "new artifact"


async def test_task_list_fetches_latest_runs_in_two_reads(env):
    ids = []
    async with env.svc.db.session() as session:
        for index in range(20):
            result = await session.execute(sa.insert(tasks).values(title=str(index), prompt="read", status="draft"))
            tid = result.inserted_primary_key[0]
            ids.append(tid)
            if index:
                await session.execute(sa.insert(task_runs).values(task_id=tid, status="completed", result="old"))
                await session.execute(sa.insert(task_runs).values(task_id=tid, status="queued"))
        await session.commit()
    statements = []

    def observed(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = env.svc.db.engine.sync_engine
    sa.event.listen(engine, "before_cursor_execute", observed)
    try:
        response = await env.client.get("/api/tasks?limit=20")
    finally:
        sa.event.remove(engine, "before_cursor_execute", observed)
    assert response.status_code == 200
    rows = response.json()
    assert [r["id"] for r in rows] == list(reversed(ids))
    assert rows[-1]["last_run"] is None
    assert all(r["last_run"]["status"] == "queued" for r in rows[:-1])
    task_reads = [s for s in statements if "FROM tasks" in s or "FROM task_runs" in s]
    assert len(task_reads) == 2
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
