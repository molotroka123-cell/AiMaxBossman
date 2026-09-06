"""Native executor text must pass the canonical declared-effect finalizer."""
import json

import pytest
import sqlalchemy as sa

from bcc.db import approvals, settings_kv, tasks, utcnow


@pytest.mark.asyncio
@pytest.mark.parametrize('write_effect', [False, True], ids=['missing-effect', 'observed-effect'])
async def test_native_executor_cannot_bypass_required_effects(env, tmp_path, write_effect):
    target = tmp_path / 'native-proof.txt'
    marker = 'native-effect-independently-observed'
    calls = []

    async def native_executor(task, run, engine):
        calls.append(run['id'])
        if write_effect:
            target.write_text(marker, encoding='utf-8')
        # Identical claims in both cases: only filesystem observation can decide.
        return 'verified deterministic result: native effect completed'

    engine = env.svc.engine
    engine.register_executor('test_native_effect_contract', native_executor)
    async with env.svc.db.session() as session:
        await session.execute(sa.insert(settings_kv).values(
            key='terminal.roots',
            value_enc=env.svc.vault.encrypt(json.dumps([str(tmp_path)]))))
        inserted = await session.execute(sa.insert(tasks).values(
            title='Native finalizer regression', prompt='Deterministic QA',
            kind='test_native_effect_contract', agent_id=None, status='draft',
            max_retries=0, created_at=utcnow(), updated_at=utcnow(),
            meta={'allowed_tools': [], 'required_effects': [{
                'kind': 'file', 'target': str(target),
                'expect': {'exists': True, 'contains': marker}}]}))
        task_id = int(inserted.inserted_primary_key[0])
        await session.commit()

    await engine.enqueue(task_id)
    run_id = await engine.claim()
    assert run_id is not None, 'native executor must pass agentless admission'
    await engine.execute(run_id)
    assert calls == [run_id], 'the real registered native dispatch must execute'
    async with env.svc.db.session() as session:
        task = (await session.execute(sa.select(tasks).where(tasks.c.id == task_id))).mappings().one()
        escalations = (await session.execute(sa.select(approvals).where(
            approvals.c.task_id == task_id,
            approvals.c.kind == 'review_escalation'))).mappings().all()
    events = await env.svc.bus.recent(200)
    finalized = [event.get('data', event) for event in events
                 if event.get('kind') == 'task.finalized']
    if write_effect:
        assert task['status'] == 'completed'
        assert not escalations
        assert len(finalized) == 1
        assert finalized[0]['checks']['verification'] == 'VERIFIED'
        assert finalized[0]['checks']['expectations'] == 1
        assert finalized[0]['override'] is False
    else:
        assert not target.exists()
        assert task['status'] == 'waiting_approval'
        assert any('required effects not verified' in row['preview'] for row in escalations)
        assert not finalized, 'executor success text must never emit task.finalized'
