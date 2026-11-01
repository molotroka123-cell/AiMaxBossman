"""Synthetic regression for the failed-action response shown in both task views."""

import pytest
import sqlalchemy as sa

from bcc.db import task_runs, tasks
from bcc.features import chat_threads
from .helpers import make_stack


@pytest.mark.parametrize("answer", ["Я приступаю", None])
async def test_failed_action_summary_is_consistent_in_task_and_chat_views(env, answer):
    stack = await make_stack(env.client, prompt="Учебная задача: создай файл")
    task_id = stack["task"]["id"]
    async with env.svc.db.session() as session:
        await session.execute(sa.update(tasks).where(tasks.c.id == task_id).values(status="failed"))
        await session.execute(sa.update(task_runs).where(task_runs.c.task_id == task_id).values(
            status="failed", result=answer, error="action_contract/no_verified_action"))
        await session.commit()

    response = await env.client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    task_view = response.json()
    chat_view = (await chat_threads._task_views(env.svc, [task_id]))[task_id]
    assert task_view["task"]["status"] == chat_view["status"] == "failed"
    assert task_view["error"] == chat_view["error"] == "action_contract/no_verified_action"
    assert task_view["result"] is None and chat_view["result"] is None
    assert task_view["failure_summary"] == chat_view["failure_summary"]
    summary = task_view["failure_summary"]
    assert "Действие не подтверждено" in summary
    if answer:
        assert answer in summary
