"""Bossman Vision: every Studio video is looked at, and the owner's feedback teaches the next look."""
import asyncio
import shutil
import subprocess
import pytest
import sqlalchemy as sa
from bcc.studio import review as rv
from bcc.studio import runtime as rt

FFMPEG = shutil.which('ffmpeg')
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason='bundled FFmpeg not on PATH')
BAD_JSON = '```json\n{"verdict": "BAD", "score": 2, "defects": ["colour noise"], "owner_rules_violated": [], "summary": "шум"}\n```'
GOOD_JSON = '{"verdict": "GOOD", "score": 8, "defects": [], "owner_rules_violated": [], "summary": "лиса, плавное движение"}'


class FakeVision:
    def __init__(self, answer=GOOD_JSON, fail=None):
        self.answer, self.fail, self.prompts, self.images = answer, fail, [], []

    async def see(self, prompt, images):
        self.prompts.append(prompt)
        self.images.append(images)
        if self.fail:
            raise self.fail
        return None if self.answer is None else {'text': self.answer, 'model': 'fake-vl', 'endpoint': 'test'}


# ---------------------------------------------------------------- pure functions

def test_frame_times_spread_inside_the_clip():
    for d in (0.4, 1.06, 5, 15, 30.5):
        t = rv.frame_times(d)
        assert len(t) == rv.FRAMES and t == sorted(t)
        assert 0 < t[0] and t[-1] < d
    assert rv.frame_times(0) == [0.0] and rv.frame_times(None) == [0.0]


@pytest.mark.parametrize('text,verdict', [
    (BAD_JSON, 'BAD'),
    (GOOD_JSON, 'GOOD'),
    ('Вот оценка: {"verdict": "bad", "score": 1} надеюсь помог {x}', 'BAD'),
    ('', 'INSUFFICIENT_EVIDENCE'),
    ('Looks great to me!', 'INSUFFICIENT_EVIDENCE'),
    ('{"verdict": "MAYBE", "score": 5}', 'INSUFFICIENT_EVIDENCE'),
    ('{"score": 9}', 'INSUFFICIENT_EVIDENCE'),
    ('{"verdict": "GOOD", "score": ', 'INSUFFICIENT_EVIDENCE'),
])
def test_parse_verdict_never_turns_noise_into_good(text, verdict):
    assert rv.parse_verdict(text)['verdict'] == verdict


def test_parse_verdict_clamps_score_and_keeps_defects():
    r = rv.parse_verdict('{"verdict":"BAD","score":99,"defects":["a","", "b"],"summary":"x"}')
    assert r['score'] == 10 and r['defects'] == ['a', 'b']
    assert rv.parse_verdict('{"verdict":"GOOD","score":"n/a"}')['score'] is None


def test_prompt_carries_owner_rules_and_the_frozen_signal():
    p = rv.build_prompt('fox slides a coin', ['у лисы нет хвоста'], ['мягкий свет'], [0.5, 1.5], motion=0.3)
    assert '[R1] у лисы нет хвоста' in p and 'мягкий свет' in p and 'fox slides a coin' in p
    assert 'almost identical' in p
    assert 'almost identical' not in rv.build_prompt('x', [], [], [1.0], motion=20.0)
    assert 'OWNER' not in rv.build_prompt('x', [], [], [1.0])


def test_motion_signal():
    still = bytes([128]) * 1024
    moving = bytes(range(256)) * 4
    assert rv.motion_signal([still, still, still]) == 0
    assert rv.motion_signal([still, moving]) > rv.FROZEN_DIFF
    assert rv.motion_signal([None, still]) is None


def test_unique_rules_deduplicates_case_and_spacing():
    facts = [{'id': 1, 'statement': 'Лиса  без хвоста'}, {'id': 2, 'statement': 'лиса без хвоста'},
             {'id': 3, 'statement': ' '}, {'id': 4, 'statement': 'мыло'}]
    assert rv.unique_rules(facts) == [{'id': 1, 'text': 'Лиса без хвоста'}, {'id': 4, 'text': 'мыло'}]


# ---------------------------------------------------------------- against the real app

async def make_video(env, name='clip', source='testsrc2=size=160x96:rate=12:duration=1', job=False):
    path = env.svc.settings.data_dir / 'studio' / 'generated' / f'{name}.mp4'
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, '-y', '-v', 'error', '-f', 'lavfi', '-i', source, '-pix_fmt', 'yuv420p',
                    '-c:v', 'libx264', str(path)], check=True)
    plane = {'prompt': 'a red fox waves', 'settings': {}, 'media': []}
    model = {'id': 'sdcpp:test-video', 'provider': 'sdcpp', 'surface': 'video'}
    jid = None
    if job:
        from bcc.v2.images_tables import image_jobs
        async with env.svc.db.session() as s:
            jid = (await s.execute(sa.insert(image_jobs).values(prompt='a red fox waves', status='running',
                                                                  model_alias='sdcpp:test-video'))).inserted_primary_key[0]
            await s.commit()
    rid = await rt.persist(env.svc, jid, plane, model, path)
    return (rid, jid) if job else rid


@pytest.fixture
def quiet(monkeypatch):
    # the background watcher must not call a real vision model on this machine during tests
    monkeypatch.setenv('BCC_VISION_REVIEW', 'off')


@needs_ffmpeg
async def test_review_stores_verdict_and_frames(env, quiet):
    rid = await make_video(env)
    fake = FakeVision(BAD_JSON)
    out = await rv.review_run(env.svc, rid, client=fake)
    assert out['verdict'] == 'BAD' and out['score'] == 2 and out['defects'] == ['colour noise']
    assert len(fake.images[0]) == rv.FRAMES and all(i[:2] == b'\xff\xd8' for i in fake.images[0])
    got = (await env.client.get(f'/api/studio/runs/{rid}/review')).json()
    assert got['review']['verdict'] == 'BAD' and got['review']['vision_model'] == 'fake-vl'
    assert got['review']['motion'] > rv.FROZEN_DIFF


@needs_ffmpeg
@pytest.mark.parametrize('fake', [FakeVision(None), FakeVision(fail=ConnectionError('refused')),
                                  FakeVision('I think it is fine')])
async def test_no_vision_is_insufficient_evidence_not_good(env, quiet, fake):
    rid = await make_video(env)
    assert (await rv.review_run(env.svc, rid, client=fake))['verdict'] == 'INSUFFICIENT_EVIDENCE'


@needs_ffmpeg
async def test_changed_bytes_are_not_reviewed(env, quiet):
    rid = await make_video(env)
    row = await rv._row(env.svc, rv.runs, rv.runs.c.id, rid)
    with open(row['file_path'], 'r+b') as f:
        f.seek(-64, 2)
        f.write(b'\0' * 64)
    fake = FakeVision(GOOD_JSON)
    out = await rv.review_run(env.svc, rid, client=fake)
    assert out['verdict'] == 'INSUFFICIENT_EVIDENCE' and not fake.prompts


@needs_ffmpeg
async def test_frozen_clip_is_flagged_to_the_model(env, quiet):
    rid = await make_video(env, 'still', 'color=c=red:size=160x96:rate=12:duration=1')
    fake = FakeVision(BAD_JSON)
    out = await rv.review_run(env.svc, rid, client=fake)
    assert out['motion'] < rv.FROZEN_DIFF and 'almost identical' in fake.prompts[0]


@needs_ffmpeg
async def test_owner_feedback_becomes_a_rule_for_the_next_review(env, quiet):
    first, second = await make_video(env, 'a'), await make_video(env, 'b')
    await rv.review_run(env.svc, first, client=FakeVision(GOOD_JSON))
    r = await env.client.post(f'/api/studio/runs/{first}/feedback',
                              json={'verdict': 'bad', 'reason': 'лиса дёргается и морда плывёт'})
    assert r.status_code == 200, r.text
    fb = r.json()['feedback']
    assert fb['vision_verdict'] == 'GOOD' and fb['agreed_with_vision'] is False and fb['fact_id']
    facts = (await env.client.get('/api/memory/facts', params={'subject': rv.SUBJECT})).json()['items']
    assert facts[0]['source_kind'] == 'human' and facts[0]['predicate'] == rv.BAD
    assert 'лиса дёргается и морда плывёт' in facts[0]['statement']
    fake = FakeVision(BAD_JSON)
    out = await rv.review_run(env.svc, second, client=fake)
    assert 'лиса дёргается и морда плывёт' in fake.prompts[0]
    assert out['owner_rules_applied'] == [first]
    s = (await env.client.get('/api/studio/review/stats')).json()
    assert s['missed_garbage'] == 1 and s['compared'] == 1 and s['agreed'] == 0
    assert [x['text'] for x in s['rules']['bad']] == ['лиса дёргается и морда плывёт']


@needs_ffmpeg
async def test_feedback_without_reason_learns_no_rule_and_bad_input_is_refused(env, quiet):
    rid = await make_video(env)
    r = await env.client.post(f'/api/studio/runs/{rid}/feedback', json={'verdict': 'good'})
    assert r.status_code == 200 and r.json()['feedback']['fact_id'] is None
    assert r.json()['feedback']['agreed_with_vision'] is None      # nothing to compare with
    assert (await env.client.post(f'/api/studio/runs/{rid}/feedback', json={'verdict': 'meh'})).status_code == 422
    assert (await env.client.post('/api/studio/runs/nope/feedback', json={'verdict': 'bad'})).status_code == 404
    assert (await rv.stats(env.svc))['rules'] == {'bad': [], 'good': []}


async def test_image_runs_are_not_video_reviewed(env, quiet):
    job = (await env.client.post('/api/studio/jobs', json={'model': 'mock:image', 'prompt': 'x'})).json()
    from bcc.features.images import process_one
    assert await process_one(env.svc) == job['id']
    rid = (await env.client.get('/api/studio/runs')).json()['items'][0]['id']
    assert (await env.client.post(f'/api/studio/runs/{rid}/review')).status_code == 422


@needs_ffmpeg
async def test_watcher_reviews_videos_of_completed_jobs_only(env, monkeypatch):
    fake = FakeVision(GOOD_JSON)
    monkeypatch.setattr(rv, 'VisionClient', lambda: fake)
    monkeypatch.setenv('BCC_VISION_REVIEW', 'on')
    watcher = asyncio.create_task(rv.watch(env.svc))
    await asyncio.sleep(0)
    try:
        stopped, stopped_job = await make_video(env, 'stopped', job=True)
        await env.svc.bus.emit('studio.job.failed', job_id=stopped_job, reason='canceled')
        done, jid = await make_video(env, 'done', job=True)
        await env.svc.bus.emit('studio.job.completed', job_id=jid)
        for _ in range(200):
            got = await rv.get(env.svc, done)
            if got['review']:
                break
            await asyncio.sleep(0.05)
        assert got['review']['verdict'] == 'GOOD' and len(fake.prompts) == 1
        assert (await rv.get(env.svc, stopped))['review'] is None     # a stopped job's output is not touched
    finally:
        watcher.cancel()


async def test_watcher_is_not_started_in_the_worker_less_app(env):
    assert not any(t.get_name() == 'bcc-studio-vision-review' for t in getattr(env.svc, '_tasks', []))


# ---------------------------------------------------------------- finding the local vision model

def fake_servers(llama_vision=False, ollama=('bossman-main', ['completion']), loaded=()):
    import httpx, json as _json
    calls = []

    def handler(request):
        url, path = str(request.url), request.url.path
        calls.append((request.method, url))
        if url.startswith('http://llama') and path == '/props':
            return httpx.Response(200, json={'modalities': {'vision': llama_vision}})
        if url.startswith('http://llama') and path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'qwen-vl'}]})
        if url.startswith('http://llama') and path == '/v1/chat/completions':
            return httpx.Response(200, json={'choices': [{'message': {'content': GOOD_JSON}}]})
        if url.startswith('http://ollama') and path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': n} for n, _ in ollama]})
        if url.startswith('http://ollama') and path == '/api/ps':
            return httpx.Response(200, json={'models': [{'name': n} for n in loaded]})
        if url.startswith('http://ollama') and path == '/api/show':
            name = _json.loads(request.content)['model']
            return httpx.Response(200, json={'capabilities': dict(ollama)[name]})
        if url.startswith('http://ollama') and path == '/api/chat':
            body = _json.loads(request.content)
            assert body['think'] is False and body['format'] == 'json' and body['messages'][0]['images']
            return httpx.Response(200, json={'message': {'content': BAD_JSON}})
        return httpx.Response(404, text='no')
    return httpx.MockTransport(handler), calls


async def test_llama_cpp_with_mmproj_is_used_first():
    t, _ = fake_servers(llama_vision=True, ollama=(('vl', ['vision']),))
    c = rv.VisionClient(['http://llama:1', 'http://ollama:2'], transport=t)
    assert await c.find() == ('http://llama:1', 'qwen-vl', 'llama.cpp')
    assert (await c.see('p', [b'frame']))['text'] == GOOD_JSON


async def test_ollama_vision_model_already_in_memory_is_preferred():
    t, _ = fake_servers(ollama=(('big-vl', ['vision']), ('main', ['completion']), ('fast-vl', ['vision'])),
                        loaded=('fast-vl', 'main'))
    c = rv.VisionClient(['http://llama:1', 'http://ollama:2'], transport=t)
    assert await c.find() == ('http://ollama:2', 'fast-vl', 'ollama')
    ans = await c.see('p', [b'frame'])
    assert ans['model'] == 'fast-vl' and rv.parse_verdict(ans['text'])['verdict'] == 'BAD'


async def test_text_only_models_are_not_a_vision_model():
    t, _ = fake_servers(ollama=(('main', ['completion', 'tools']),))
    c = rv.VisionClient(['http://llama:1', 'http://ollama:2', 'http://nothing:3'], transport=t)
    assert await c.find() is None and await c.see('p', [b'frame']) is None


@needs_ffmpeg
async def test_review_reads_the_same_file_the_verification_opened(env, quiet):
    """Review P2 (c): a run whose bytes live in the legacy Images store passed verification but was
    looked up in the Studio store, so it could only ever be INSUFFICIENT_EVIDENCE. Not reachable
    through migrate_legacy today (legacy rows are images); one resolver keeps it that way."""
    path = env.svc.settings.data_dir / 'images' / 'legacy-clip.mp4'
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x96:rate=12:duration=1',
                    '-pix_fmt', 'yuv420p', '-c:v', 'libx264', str(path)], check=True)
    rid = await rt.persist(env.svc, None, {'prompt': 'fox', 'settings': {}, 'media': []},
                           {'id': 'legacy-video', 'provider': 'legacy', 'surface': 'video'}, path, legacy=424242)
    fake = FakeVision(GOOD_JSON)
    out = await rv.review_run(env.svc, rid, client=fake)
    assert out['verdict'] == 'GOOD' and len(fake.images[0]) == rv.FRAMES
