"""Audit 2026-09-28 (video/Studio): a PAID cloud generation is neither lost to a poll hiccup,
nor paid twice by Retry, nor left unaccounted, nor charged to the budget when nothing was sent.

Injected HTTP transport only — no provider is contacted, nothing is spent.
"""
import asyncio
import httpx
import pytest
import sqlalchemy as sa
from bcc.features.images import process_one

MODEL = 'openrouter:bytedance/seedance-2.0-mini'   # catalog: 10 s, 9:16, 720p
POLICY = {'enabled': True, 'free_only': False, 'cloud_budget_usd': 10, 'per_job_usd': 2,
          'prices': {MODEL: 1.0}, 'download_hosts': ['cdn.example.test']}


@pytest.fixture
def fast_polls(monkeypatch):
    from bcc.studio import dispatch
    monkeypatch.setattr(dispatch, 'POLL_SECONDS', {'openrouter': 0.01}, raising=False)
    monkeypatch.setattr(dispatch, 'POLL_BACKOFF_MAX_SECONDS', 0.02, raising=False)


async def ordered_clip(tmp_path):
    from bcc.video_studio.media import binary, process
    path = tmp_path / 'ok.mp4'
    await process([binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=180x320:r=5:d=10',
                   '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)])
    return path


class Provider:
    """Scripted OpenRouter: `polls` is the list of answers to GET /videos/<id>, in order."""
    def __init__(self, polls, *, balance=100, clip=None):
        self.polls, self.balance, self.clip = list(polls), balance, clip
        self.posts, self.gets = 0, 0

    def __call__(self, r):
        if r.url.host == 'cdn.example.test':
            return httpx.Response(200, content=self.clip.read_bytes())
        if r.url.path.endswith('/auth/key'):
            return httpx.Response(200, json={'data': {'limit_remaining': self.balance}})
        if r.method == 'POST':
            self.posts += 1
            return httpx.Response(202, json={'id': f'vid-{self.posts}'})
        self.gets += 1
        answer = self.polls.pop(0) if self.polls else ('json', {'status': 'failed', 'error': 'script over'})
        kind, value = answer
        return httpx.Response(value, json={}) if kind == 'http' else httpx.Response(200, json=value)


def install(monkeypatch, fake):
    from bcc.studio.providers import openrouter
    cls = openrouter.OpenRouterProvider
    monkeypatch.setattr(openrouter, 'OpenRouterProvider',
                        lambda key, **kw: cls(key, transport=httpx.MockTransport(fake), **kw))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only-key')


async def new_job(env, policy=POLICY):
    from bcc.studio.governance import save_policy
    await save_policy(env.svc, policy)
    return (await env.client.post('/api/studio/jobs', json={'model': MODEL, 'prompt': 'x'})).json()


async def job(env, jid):
    return (await env.client.get(f'/api/studio/jobs/{jid}')).json()


def test_cloud_poll_interval_is_not_half_a_second():
    from bcc.studio import dispatch
    assert dispatch.POLL_SECONDS['openrouter'] >= 5


async def test_transient_poll_errors_do_not_kill_a_paid_job(env, monkeypatch, tmp_path, fast_polls):
    done = {'status': 'completed', 'usage': {'cost': 0.7}, 'unsigned_urls': ['https://cdn.example.test/o.mp4']}
    fake = Provider([('json', {'status': 'in_progress'}), ('http', 429), ('http', 503), ('http', 429),
                     ('json', {'status': 'in_progress'}), ('json', done)], clip=await ordered_clip(tmp_path))
    install(monkeypatch, fake)
    created = await new_job(env)
    await process_one(env.svc)
    row = await job(env, created['id'])
    assert row['status'] == 'completed', row
    assert fake.posts == 1 and fake.gets == 6
    assert row['studio']['cost_usd'] == 0.7, 'the provider-reported charge is recorded on the job'


async def test_cost_is_recorded_on_the_failure_path_too(env, monkeypatch, fast_polls):
    fake = Provider([('json', {'status': 'failed', 'usage': {'cost': 0.3}})])
    install(monkeypatch, fake)
    created = await new_job(env)
    await process_one(env.svc)
    row = await job(env, created['id'])
    assert row['status'] == 'failed' and row['studio']['cost_usd'] == 0.3, row


async def test_retry_after_a_sent_request_requires_explicit_confirmation(env, monkeypatch, fast_polls):
    fake = Provider([('json', {'status': 'failed', 'error': 'boom'})] * 2)
    install(monkeypatch, fake)
    created = await new_job(env)
    await process_one(env.svc)
    assert (await job(env, created['id']))['studio']['submit_started'] is True
    refused = await env.client.post(f"/api/studio/jobs/{created['id']}/retry")
    assert refused.status_code == 409 and refused.json()['error']['reason'] == 'resubmit_requires_confirmation'
    assert refused.json()['error']['provider_request_id'] == 'vid-1'
    await process_one(env.svc)
    assert fake.posts == 1, 'no second paid request without the owner saying so'
    again = await env.client.post(f"/api/studio/jobs/{created['id']}/retry", json={'confirm_resubmit': True})
    assert again.status_code == 200, again.text
    await process_one(env.svc)
    assert fake.posts == 2


async def test_reservation_is_returned_when_nothing_was_sent(env, monkeypatch):
    from bcc.studio.governance import budget_status
    fake = Provider([], balance=0.5)                      # below the 1.0 upper bound: refused before submit
    install(monkeypatch, fake)
    for _ in range(3):
        created = await new_job(env, {**POLICY, 'cloud_budget_usd': 2})
        await process_one(env.svc)
        row = await job(env, created['id'])
        assert row['studio']['reason'] == 'insufficient_credit', row
    assert fake.posts == 0
    assert (await budget_status(env.svc))['committed_upper_bound_usd'] == 0


async def test_reservation_stays_committed_once_the_request_was_sent(env, monkeypatch, fast_polls):
    from bcc.studio.governance import budget_status
    install(monkeypatch, Provider([('json', {'status': 'failed', 'error': 'boom'})]))
    await new_job(env)
    await process_one(env.svc)
    assert (await budget_status(env.svc))['committed_upper_bound_usd'] == 1.0


async def test_owner_cancel_after_send_marks_provider_outcome_unknown(env, monkeypatch, fast_polls):
    fake = Provider([('json', {'status': 'in_progress'})] * 10_000)
    install(monkeypatch, fake)
    created = await new_job(env)
    worker = asyncio.create_task(process_one(env.svc))
    for _ in range(500):
        if fake.gets:
            break
        await asyncio.sleep(0.01)
    await env.client.post(f"/api/studio/jobs/{created['id']}/cancel")
    await asyncio.wait_for(worker, 5)
    row = await job(env, created['id'])
    assert row['status'] == 'cancelled' and row['studio']['reason'] == 'owner_stop_provider_unknown'
    assert row['studio']['verdict'] == 'OWNER_REQUIRED'
    assert (await env.client.post(f"/api/studio/jobs/{created['id']}/retry")).status_code == 409


async def test_cancel_of_a_queued_cloud_job_is_not_marked_unknown(env, monkeypatch):
    install(monkeypatch, Provider([]))
    created = await new_job(env)
    await env.client.post(f"/api/studio/jobs/{created['id']}/cancel")
    row = await job(env, created['id'])
    assert row['status'] == 'cancelled' and row['studio']['reason'] is None


async def test_restart_with_a_sent_cloud_request_needs_reconcile(env, monkeypatch):
    from bcc.studio.runtime import setup
    from bcc.studio.tables import jobs
    from bcc.v2.images_tables import image_jobs
    install(monkeypatch, Provider([]))
    sent = await new_job(env)
    unsent = (await env.client.post('/api/studio/jobs', json={'model': MODEL, 'prompt': 'y'})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(image_jobs).where(image_jobs.c.id.in_([sent['id'], unsent['id']])).values(status='running'))
        await s.execute(sa.update(jobs).where(jobs.c.job_id == sent['id']).values(submit_started=True, request_id='vid-77'))
        await s.commit()
    await setup(env.svc)
    row = await job(env, sent['id'])
    assert row['status'] == 'failed' and row['studio']['reason'] == 'needs_reconcile'
    assert row['studio']['verdict'] == 'OWNER_REQUIRED' and 'vid-77' in row['error']
    assert (await env.client.post(f"/api/studio/jobs/{sent['id']}/retry", json={'confirm_resubmit': True})).status_code == 409
    assert (await job(env, unsent['id']))['studio']['reason'] == 'interrupted_unknown'


async def test_oversized_reference_is_a_clear_owner_error(monkeypatch):
    from bcc.studio import dispatch
    from bcc.studio.runtime import StudioError
    async def row(svc, table, key, value):
        return {'id': 'r', 'deleted': False, 'sha256': 'a' * 64, 'surface': 'image', 'mime': 'image/png',
                'file_bytes': 16 * 1024 * 1024}
    monkeypatch.setattr(dispatch, 'one', row)
    with pytest.raises(StudioError) as caught:
        await dispatch.inputs_for(None, [{'run_id': 'r', 'role': 'reference', 'sha256': 'a' * 64}])
    assert caught.value.reason == 'egress' and '15 MiB' in str(caught.value)
