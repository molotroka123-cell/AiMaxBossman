"""Chat text must not erase a separately configured evidence obligation."""

import pytest
import sqlalchemy as sa

from bcc.db import tasks as tasks_t, task_runs as runs_t
from bcc.features import action_contract, review_gate

from .helpers import make_stack


@pytest.mark.parametrize('review', [
    {'evidence': [{'kind': 'file', 'target': 'notes.txt', 'expect': {'exists': True}}]},
    None,
])
async def test_chat_gate_preserves_preexisting_review_evidence(env, monkeypatch, tmp_path, review):
    stack = await make_stack(env.client)
    task_id = stack['task']['id']
    meta = {'review': review} if review is not None else {}
    async with env.svc.db.session() as session:
        await session.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(meta=meta))
        run_id = (await session.execute(sa.select(runs_t.c.id).where(runs_t.c.task_id == task_id))).scalar_one()
        await session.commit()

    async def is_chat(_svc, _task_id):
        return True

    monkeypatch.setattr(action_contract, '_is_chat_turn', is_chat)
    gate = await action_contract._gate(env.svc)
    task = {'id': task_id, 'agent_id': stack['agent']['id'], 'prompt': 'Создай файл notes.txt'}
    verdict = await gate(task, run_id, 'Ответ текстом')
    async with env.svc.db.session() as session:
        saved = (await session.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == task_id))).scalar_one()
    assert verdict['verdict'] == 'NOT_APPLICABLE'
    assert saved.get('review') == review
    assert 'TERMINAL_FILE_ACTION' in saved['action_not_executed']

    async def roots(_svc, _task):
        return [tmp_path]

    monkeypatch.setattr(review_gate, '_evidence_roots', roots)
    review_result = await (await review_gate._gate(env.svc))(task, run_id, 'Ответ текстом')
    # The missing file remains an independently checked obligation. A plain
    # chat answer without configured evidence retains its old behavior.
    assert review_result['verdict'] == ('FAIL' if review is not None else 'NOT_APPLICABLE')
