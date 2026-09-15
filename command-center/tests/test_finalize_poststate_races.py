"""A fresh observation must finalize only the task/run that requested it."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import tasks, task_runs, tool_calls
from bcc.engine import FencedOut

from .helpers import make_stack
from .test_finalize_gate import _allow_root, _set_meta


@pytest.mark.parametrize("change", ["obligation", "prompt", "new_run", "pause", "stop", "new_tool_call"])
async def test_normal_finalize_cannot_commit_a_superseded_observation(env, tmp_path, monkeypatch, change):
    import bcc.finalize as finalizer
    stack = await make_stack(env.client, prompt="plain task")
    task_id = stack["task"]["id"]
    run_id = await env.svc.engine.claim()
    target = tmp_path / "actual.txt"
    target.write_text("independently observed bytes")
    await _allow_root(env, tmp_path)
    await _set_meta(env, task_id, {"required_effects": [{
        "kind": "file", "target": str(target), "expect": {"contains": "independently observed bytes"}}]})
    original = finalizer.verify_all

    async def mutate_after_actual_observation(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert result[0] == "VERIFIED", result
        async with env.svc.db.session() as session:
            if change == "obligation":
                await session.execute(sa.update(tasks).where(tasks.c.id == task_id).values(meta={
                    "required_effects": [{"kind": "file", "target": str(tmp_path / "never-created"),
                                          "expect": {"exists": True}}]}))
            elif change == "new_run":
                await session.execute(sa.insert(task_runs).values(
                    task_id=task_id, status="queued", attempt=2))
            elif change == "prompt":
                await session.execute(sa.update(tasks).where(tasks.c.id == task_id).values(
                    prompt="a different owner objective"))
            elif change == "new_tool_call":
                await session.execute(sa.insert(tool_calls).values(
                    task_id=task_id, run_id=run_id, tool="terminal.run",
                    call_id="late-mutation", status="pending_approval"))
            else:
                await session.execute(sa.update(tasks).where(tasks.c.id == task_id).values(
                    status="paused" if change == "pause" else "stopped"))
            await session.commit()
        return result

    monkeypatch.setattr(finalizer, "verify_all", mutate_after_actual_observation)
    with pytest.raises(FencedOut):
        await finalizer.finalize_task(env.svc.engine, run_id, task_id, answer="done", usage={})
    state = (await env.client.get(f"/api/tasks/{task_id}")).json()
    assert state["task"]["status"] != "completed", state
    assert not any(run["status"] == "completed" for run in state["runs"])
    assert not any(event["kind"] in ("task.completed", "task.finalized")
                   for event in await env.svc.bus.recent(100))
