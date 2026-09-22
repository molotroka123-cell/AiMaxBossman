"""/img: local generation through a FAKE Bossman Studio API (no engine, no network)."""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest

from bcc.telegram_companion.adapters import Core, Models, Telegram
from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
GUEST = Person(22222, 22222, 'guest', None)
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x07' * 300


def cfg(**kw):
    base = dict(local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='bot-fixture',
                core_token='core-fixture', image_enabled=True, image_deadline=60)
    base.update(kw)
    return Settings((OWNER, GUEST), **base)


class FakeStudio:
    def __init__(self, *, available=True, finish='completed', polls=1, file_bytes=PNG, sha=None):
        self.available, self.finish, self.polls = available, finish, polls
        self.file_bytes, self.sha = file_bytes, sha
        self.requests, self.created, self.cancelled = [], [], False

    def __call__(self, request):
        url, method = request.url, request.method
        self.requests.append((method, url.path))
        assert request.headers.get('X-BCC-Token') == 'core-fixture'
        if url.path == '/api/studio/models':
            return httpx.Response(200, json={'items': [{'id': 'sdcpp:z-image-turbo', 'available': self.available}]})
        if url.path == '/api/studio/jobs' and method == 'POST':
            self.created.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 7, 'status': 'queued'})
        if url.path == '/api/studio/jobs/7/cancel':
            self.cancelled = True
            return httpx.Response(200, json={'id': 7, 'status': 'cancelled'})
        if url.path == '/api/studio/jobs/7':
            self.polls -= 1
            status = 'running' if self.polls > 0 else ('cancelled' if self.cancelled else self.finish)
            return httpx.Response(200, json={'id': 7, 'status': status})
        if url.path == '/api/studio/runs':
            assert url.params['job_id'] == '7'
            return httpx.Response(200, json={'items': [{'id': 'run-abc', 'job_id': 7, 'mime': 'image/png',
                'model': 'sdcpp:z-image-turbo', 'sha256': self.sha or hashlib.sha256(self.file_bytes).hexdigest()}]})
        if url.path == '/api/studio/runs/run-abc/file':
            return httpx.Response(200, content=self.file_bytes)
        raise AssertionError('unexpected ' + str(url))


class TelegramRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, request):
        method = request.url.path.rsplit('/', 1)[-1]
        self.calls.append((method, request.content))
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': len(self.calls)}})

    def photos(self):
        return [c for m, c in self.calls if m == 'sendPhoto']


def run(tmp_path, studio, text='/img кот-астронавт', person=OWNER, settings=None, during=None, free_gb=64.0,
        llm_busy=False):
    tg_rec = TelegramRecorder()
    async def go():
        s = settings or cfg()
        store = Store(tmp_path)
        tg = Telegram(s, transport=httpx.MockTransport(tg_rec))
        core = Core(s, transport=httpx.MockTransport(studio))
        models = Models(s, tmp_path, transport=httpx.MockTransport(lambda r: pytest.fail('no LLM call')))
        app = Companion(s, store, tg, core, models)
        app.image_poll_seconds = 0.01
        app.free_memory_gb = lambda: free_gb
        msg = {'from': {'id': person.user_id, 'is_bot': False}, 'chat': {'id': person.chat_id, 'type': 'private'},
               'text': text}
        try:
            if llm_busy:
                await models.lock.acquire()
            task = asyncio.create_task(app.handle(person, msg))
            if during:
                await during(app, person)
            try:
                return await task, store.history(person.key)
            except CompanionError as exc:
                return 'ERR:' + str(exc), store.history(person.key)
        finally:
            await tg.close(); await core.close(); await models.close(); store.close()
    reply, history = asyncio.run(go())
    return reply, history, tg_rec


def test_generated_image_is_sent_only_after_byte_verification(tmp_path):
    studio = FakeStudio(polls=3)
    reply, history, tg = run(tmp_path, studio)
    assert reply is None
    [photo] = tg.photos()
    assert PNG in photo and b'sdcpp:z-image-turbo' in photo and b'seed' in photo
    job = studio.created[0]
    assert job['model'] == 'sdcpp:z-image-turbo' and job['prompt'] == 'кот-астронавт'
    assert job['settings']['width'] == 1024 and job['settings']['steps'] == 8 and isinstance(job['settings']['seed'], int)
    assert history[0]['content'] == '[картинка] кот-астронавт'


def test_hash_mismatch_or_non_image_is_never_sent(tmp_path):
    reply, _, tg = run(tmp_path / 'a', FakeStudio(sha='0' * 64))
    assert reply == 'ERR:IMAGE_BYTES_UNVERIFIED' and tg.photos() == []
    fake = b'<svg>not a png</svg>'
    reply, _, tg = run(tmp_path / 'b', FakeStudio(file_bytes=fake))
    assert reply == 'ERR:IMAGE_BYTES_UNVERIFIED' and tg.photos() == []


def test_engine_not_configured_is_said_plainly(tmp_path):
    studio = FakeStudio(available=False)
    reply, _, tg = run(tmp_path, studio)
    assert reply == 'ERR:IMAGE_ENGINE_NOT_CONFIGURED' and studio.created == [] and tg.photos() == []


def test_failed_job_reports_failure(tmp_path):
    reply, _, tg = run(tmp_path, FakeStudio(finish='failed'))
    assert reply == 'ERR:IMAGE_GEN_FAILED' and tg.photos() == []


def test_cancel_stops_the_studio_job(tmp_path):
    studio = FakeStudio(polls=10_000)
    async def cancel(app, person):
        for _ in range(200):
            if app.image_job and app.image_job['id']:
                break
            await asyncio.sleep(0.01)
        msg = {'from': {'id': person.user_id, 'is_bot': False}, 'chat': {'id': person.chat_id, 'type': 'private'},
               'text': '/cancel'}
        assert 'Отменяю' in await app.handle(person, msg)
    reply, _, tg = run(tmp_path, studio, during=cancel)
    assert reply == 'ERR:IMAGE_GEN_CANCELLED' and studio.cancelled and tg.photos() == []


def test_one_at_a_time_and_ram_and_llm_guards(tmp_path):
    async def second(app, person):
        for _ in range(200):
            if app.image_job and app.image_job['id']:
                break
            await asyncio.sleep(0.01)
        msg = {'from': {'id': person.user_id, 'is_bot': False}, 'chat': {'id': person.chat_id, 'type': 'private'},
               'text': '/img ещё'}
        with pytest.raises(CompanionError, match='IMAGE_GEN_BUSY'):
            await app.handle(person, msg)
    reply, _, _ = run(tmp_path / 'a', FakeStudio(polls=5), during=second)
    assert reply is None
    studio = FakeStudio()
    assert run(tmp_path / 'b', studio, free_gb=3.0)[0] == 'ERR:IMAGE_GEN_LOW_MEMORY' and studio.requests == []
    studio = FakeStudio()
    assert run(tmp_path / 'c', studio, llm_busy=True)[0] == 'ERR:IMAGE_GEN_LLM_BUSY' and studio.requests == []


def test_guests_and_disabled_setting(tmp_path):
    studio = FakeStudio()
    assert run(tmp_path / 'a', studio, person=GUEST)[0] == 'ERR:IMAGE_GEN_GUESTS_DISABLED'
    assert run(tmp_path / 'b', studio, settings=cfg(image_enabled=False))[0] == 'ERR:IMAGE_GEN_DISABLED'
    assert studio.requests == []
    reply, _, _ = run(tmp_path / 'c', FakeStudio(), person=GUEST, settings=cfg(image_guests=True))
    assert reply is None


def test_image_settings_validation():
    for bad in ({'image_model': 'openrouter:dall-e'}, {'image_size': 999}, {'image_steps': 50},
                {'image_deadline': 5}, {'image_enabled': 'yes'}):
        with pytest.raises(ValueError):
            cfg(**bad)
