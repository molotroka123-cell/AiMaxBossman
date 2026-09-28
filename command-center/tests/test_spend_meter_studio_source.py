"""Audit 2026-09-28: paid Studio generation (Seedance via OpenRouter) bypasses the engine,
so task_runs never saw it and the Spend Meter reported $0 for real cloud video spend."""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import utcnow
from bcc.features import spend_meter


async def _studio_job(env, cost, model='openrouter:bytedance/seedance-2.0-mini'):
    from bcc.studio.tables import jobs
    from bcc.v2.images_tables import image_jobs
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(image_jobs).values(prompt='x', model_alias=model, count=1,
            options={'studio': True}, status='failed', created_at=utcnow(), updated_at=utcnow(),
            started_at=utcnow(), finished_at=utcnow()))
        jid = int(res.inserted_primary_key[0])
        await s.execute(sa.insert(jobs).values(job_id=jid, plane={'model': model}, surface='video', cost_usd=cost))
        await s.commit()


async def test_studio_cloud_charges_are_counted(env, monkeypatch):
    monkeypatch.setenv(spend_meter.FLAG, "1")
    await _studio_job(env, 0.75)                                   # paid, even though the job failed
    await _studio_job(env, 'NOT_CAPTURED:provider_did_not_report')  # unknown is not invented
    await _studio_job(env, 0)
    report = (await env.client.get('/api/spend')).json()
    assert report['spent']['total_usd'] == 0.75 and report['spent']['today_usd'] == 0.75
    assert report['spent']['by_model'] == [{'model': 'openrouter:bytedance/seedance-2.0-mini',
                                             'spent_usd': 0.75, 'entries': 1}]
