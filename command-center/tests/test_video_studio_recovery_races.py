import pytest
import sqlalchemy as sa
from .helpers import make_stack
from .test_fence_fl01 import _takeover, _run_row
from bcc.db import tasks as tasks_t
pytestmark = pytest.mark.asyncio

@pytest.mark.parametrize("requeue",[False,True])
async def test_old_gate_fail_cannot_requeue_new_owner(env,requeue):
    stack=await make_stack(env.client)
    engine=env.svc.engine
    rid=await engine.claim()
    async with env.svc.db.session() as s:
        task=dict((await s.execute(sa.select(tasks_t).where(tasks_t.c.id==stack['task']['id']))).mappings().one())
    async def gate(task,run,answer):
        await _takeover(env,engine,rid)
        return {'verdict':'FAIL','requeue':requeue,'status':'failed'}
    engine.hooks['gate_completion']=[]
    engine.add_hook('gate_completion',gate)
    await engine._complete_run(rid,task,'old result',[],1,0,0,0,'')
    row=await _run_row(env.svc.db,rid)
    print('QA gate state',row['fence'],row['status'])
    assert row['status']=='leased', 'stale gate overwrote new owner lease'

async def test_retry_enqueue_after_committed_job(env,monkeypatch):
    video=env.svc.video_studio
    created=await video.store.create('qa-project','QA','qa-create')
    project=created['project']
    payload={'project_id':project['id'],'expected_revision':0,'operation_id':'qa-export','options':{}}
    enqueue=env.svc.engine.enqueue
    async def crash(*args,**kwargs):raise RuntimeError('simulated crash before enqueue')
    monkeypatch.setattr(env.svc.engine,'enqueue',crash)
    with pytest.raises(RuntimeError):await video.export(payload)
    monkeypatch.setattr(env.svc.engine,'enqueue',enqueue)
    replay=await video.export(payload)
    print('QA durable retry state',replay['status'])
    assert replay['status']=='queued', 'committed draft never enqueued by idempotent replay'


async def test_renewed_reservation_not_expired_by_stale_tick(env,monkeypatch):
    from contextlib import asynccontextmanager
    from datetime import timedelta
    from bcc.features.resources import _tick,_reserve
    from bcc.db import resource_reservations as res_t,utcnow
    rid=await _reserve(env.svc,'video_job',999,512)
    async with env.svc.db.session() as session:
        await session.execute(sa.update(res_t).where(res_t.c.id==rid).values(expires_at=utcnow()-timedelta(seconds=1)))
        await session.commit()
    original=env.svc.db.session
    calls=0
    @asynccontextmanager
    async def interleaved():
        nonlocal calls
        calls+=1
        this=calls
        async with original() as session:yield session
        if this==1:
            async with original() as session:
                await session.execute(sa.update(res_t).where(res_t.c.id==rid).values(expires_at=utcnow()+timedelta(minutes=15)))
                await session.commit()
    monkeypatch.setattr(env.svc.db,'session',interleaved)
    await _tick(env.svc)
    async with original() as session:
        row=(await session.execute(sa.select(res_t).where(res_t.c.id==rid))).mappings().one()
    print('QA renewed reservation status',row['status'])
    assert row['status']=='held','expired after heartbeat renewed the lease'

async def test_concurrent_replay_repairs_only_one_durable_draft(env,monkeypatch):
    import asyncio
    from bcc.db import task_runs as runs_t
    video=env.svc.video_studio
    await video.store.create('repair-project','QA','repair-create')
    payload={'project_id':'repair-project','expected_revision':0,'operation_id':'repair-export','options':{}}
    original=env.svc.engine.enqueue
    async def crash(*args,**kwargs):raise RuntimeError('before enqueue')
    monkeypatch.setattr(env.svc.engine,'enqueue',crash)
    with pytest.raises(RuntimeError):await video.export(payload)
    monkeypatch.setattr(env.svc.engine,'enqueue',original)
    values=await asyncio.gather(*(video.export(payload) for _ in range(4)))
    assert len({v['job_id'] for v in values})==1
    async with env.svc.db.session() as session:
        count=(await session.execute(sa.select(sa.func.count()).select_from(runs_t)
            .where(runs_t.c.task_id==values[0]['task_id']))).scalar_one()
    assert count==1

@pytest.mark.parametrize('max_retries',[0,2])
async def test_recovery_rechecks_expiry_after_new_heartbeat(env,monkeypatch,max_retries):
    from contextlib import asynccontextmanager
    from datetime import timedelta
    from bcc.db import task_runs as runs_t,utcnow
    stack=await make_stack(env.client,max_retries=max_retries)
    rid=await env.svc.engine.claim()
    async with env.svc.db.session() as session:
        await session.execute(sa.update(runs_t).where(runs_t.c.id==rid).values(worker_lease_until=utcnow()-timedelta(seconds=2)))
        await session.commit()
    original=env.svc.db.session
    renew=False
    @asynccontextmanager
    async def interleaved():
        nonlocal renew
        async with original() as session:
            execute=session.execute
            async def observe(statement,*args,**kwargs):
                nonlocal renew
                result=await execute(statement,*args,**kwargs)
                if statement.is_select and 'max_retries' in str(statement) and 'worker_lease_until' in str(statement):
                    renew=True
                return result
            session.execute=observe
            yield session
        if renew:
            renew=False
            async with original() as session:
                await session.execute(sa.update(runs_t).where(runs_t.c.id==rid).values(worker_lease_until=utcnow()+timedelta(minutes=15)))
                await session.commit()
    monkeypatch.setattr(env.svc.db,'session',interleaved)
    assert await env.svc.engine.recover()==0
    async with original() as session:
        row=(await session.execute(sa.select(runs_t).where(runs_t.c.id==rid))).mappings().one()
    assert row['status']=='leased'
