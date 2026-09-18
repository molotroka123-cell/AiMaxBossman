"""Studio's real authenticated API + shared worker. No external provider needed."""
import asyncio
import json
from pathlib import Path
import pytest
import sqlalchemy as sa
from bcc.features.images import process_one

async def create(env, **values):
    r = await env.client.post('/api/studio/jobs',json={'model':'mock:image','prompt':'Studio Prague',**values})
    assert r.status_code == 200, r.text
    return r.json()

async def finish(env, **values):
    job=await create(env,**values)
    assert await process_one(env.svc) == job['id']
    result=(await env.client.get(f"/api/studio/jobs/{job['id']}")).json()
    assert result['status']=='completed',result
    return (await env.client.get('/api/studio/runs')).json()['items'][0]

async def test_api_auth_and_model_truth(env):
    r=await env.client.get('/api/studio/models',headers={'X-BCC-Token':'wrong'})
    assert r.status_code in (401,403)
    models=(await env.client.get('/api/studio/models')).json()['items']
    assert any(m['id']=='mock:image' for m in models)
    assert not any(m['verified'] for m in models)
    assert all(not m['available'] for m in models if m['provider'] in ('openrouter','higgsfield'))

async def test_result_bytes_reuse_trash_restore_and_favorite(env):
    run=await finish(env,settings={'width':256,'height':256,'seed':123})
    assert len(run['provenance']['output']['sha256'])==64
    assert run['provenance']['mock'] is True
    assert (await env.client.get(run['file_url'])).status_code==200
    reuse=(await env.client.post(f"/api/studio/runs/{run['id']}/reuse")).json()
    assert reuse['plane']==run['provenance']['plane']
    assert (await env.client.patch(f"/api/studio/runs/{run['id']}",json={'favorite':True})).status_code==200
    assert (await env.client.get('/api/studio/runs?favorite=true')).json()['total']==1
    await env.client.delete(f"/api/studio/runs/{run['id']}")
    assert (await env.client.get('/api/studio/runs')).json()['total']==0
    assert (await env.client.get('/api/studio/runs?deleted=true')).json()['total']==1
    await env.client.post(f"/api/studio/runs/{run['id']}/restore")
    assert (await env.client.get('/api/studio/runs')).json()['total']==1

async def test_no_60_record_eviction(env):
    for n in range(61): await finish(env,prompt=f'frame {n}')
    data=(await env.client.get('/api/studio/runs?limit=100')).json()
    assert data['total']==61 and len(data['items'])==61
    assert {r['provenance']['plane']['prompt'] for r in data['items']}=={f'frame {n}' for n in range(61)}

async def test_provenance_db_immutable_but_organization_mutable(env):
    run=await finish(env)
    from bcc.studio.tables import runs
    with pytest.raises(sa.exc.IntegrityError):
        async with env.svc.db.session() as s:
            await s.execute(sa.update(runs).where(runs.c.id==run['id']).values(provenance={}))
            await s.commit()
    assert (await env.client.patch(f"/api/studio/runs/{run['id']}",json={'provenance':{}})).status_code==422
    assert (await env.client.patch(f"/api/studio/runs/{run['id']}",json={'favorite':True})).status_code==200

async def test_tampered_bytes_never_served(env):
    run=await finish(env)
    Path(run['provenance']['output']['path']).write_bytes(b'broken')
    assert (await env.client.get(run['file_url'])).status_code==409

@pytest.mark.parametrize('payload',[{'settings':{'stranger':1}},{'settings':{'width':True}},{'model':'invented:model'},{'count':9},{'prompt':''}])
async def test_invalid_planes_rejected_before_queue(env,payload):
    r=await env.client.post('/api/studio/jobs',json={'model':'mock:image','prompt':'x',**payload})
    assert r.status_code==422
    assert (await env.client.get('/api/studio/jobs')).json()['total']==0

async def test_legacy_assets_migrate_once_without_fabricated_cost(env):
    await env.client.post('/api/images/jobs',json={'prompt':'old mock'})
    await process_one(env.svc)
    first=(await env.client.get('/api/studio/runs')).json()
    second=(await env.client.get('/api/studio/runs')).json()
    assert first['total']==second['total']==1
    assert first['items'][0]['provenance']['cost_usd']=='NOT_CAPTURED:pre-v8'

async def test_cancel_pending_job_has_no_result(env):
    job=await create(env)
    await env.client.post(f"/api/studio/jobs/{job['id']}/cancel")
    assert await process_one(env.svc) is None
    assert (await env.client.get('/api/studio/runs')).json()['total']==0

async def test_shared_worker_atomic_claim(env):
    await create(env)
    await asyncio.gather(process_one(env.svc),process_one(env.svc))
    assert (await env.client.get('/api/studio/runs')).json()['total']==1

async def test_restart_persists_gallery_and_never_resubmits_ambiguous_job(env):
    await finish(env)
    job=await create(env)
    from bcc.v2.images_tables import image_jobs
    async with env.svc.db.session() as s:
        await s.execute(sa.update(image_jobs).where(image_jobs.c.id==job['id']).values(status='running'))
        await s.commit()
    from bcc.studio.runtime import setup
    await setup(env.svc)
    data=(await env.client.get(f"/api/studio/jobs/{job['id']}")).json()
    assert data['status']=='failed' and data['studio']['reason']=='interrupted_unknown'
    assert (await env.client.get('/api/studio/runs')).json()['total']==1
