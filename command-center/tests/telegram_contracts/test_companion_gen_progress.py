"""Pult progress plate: the generation status message is edited in place, throttled, in Russian.

Owner 2026-10-13: «Добавь параметр плашку с ответом по генерации сколько % осталось».
Fakes only (no Telegram, no engine): a fake Studio API reports progress like the real one.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest

from bcc.telegram_companion import gen_progress as gp
from bcc.telegram_companion.adapters import Core, Models, Telegram
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x07' * 300


def detail(**kw):
    base = {'phase': 'sample', 'known': True, 'step': 9, 'steps': 20, 'segment': 1, 'segments': 1,
            's_per_it': 18.0, 'eta_s': 342.0}
    base.update(kw)
    return base


# ------------------------------------------------------------------ wording

def test_line_shows_bar_percent_step_and_minutes_left():
    line = gp.progress_line({'status': 'running', 'progress': 0.42, 'progress_detail': detail()})
    assert line == '▓▓▓▓░░░░░░ 42% · шаг 9/20 · осталось ~6 мин'


def test_unknown_progress_is_said_not_invented():
    # the 0.05 the queue writes at claim time is not a measurement
    assert gp.progress_line({'status': 'running', 'progress': 0.05, 'progress_detail': None}) == '⏳ прогресс неизвестен'
    assert gp.progress_line({'status': 'running', 'progress': 0.05}) == '⏳ прогресс неизвестен'
    assert gp.progress_line({'status': 'running', 'progress': 0.3,
                             'progress_detail': detail(known=False)}) == '⏳ прогресс неизвестен'
    assert 'прогресс неизвестен' in gp.progress_line({'status': 'queued', 'progress': 0})
    assert gp.progress_line(None) == '⏳ прогресс неизвестен'


def test_phases_segments_short_eta_and_never_100_while_running():
    line = gp.progress_line({'status': 'running', 'progress': 0.71,
                             'progress_detail': detail(phase='decode', segment=2, segments=3, eta_s=40)})
    assert line == '▓▓▓▓▓▓▓░░░ 71% · сборка кадров (VAE) · часть 2/3 · осталось ~40 с'
    line = gp.progress_line({'status': 'running', 'progress': 0.97,
                             'progress_detail': detail(phase='verify', eta_s=0)})
    assert line.startswith('▓▓▓▓▓▓▓▓▓░ 97% · проверка файла')
    line = gp.progress_line({'status': 'running', 'progress': 1.0, 'progress_detail': detail(eta_s=None)})
    assert '99%' in line and '100%' not in line and 'осталось' not in line


# ------------------------------------------------------------------ throttling

class FakeTelegram:
    def __init__(self, fail=False):
        self.edits, self.fail = [], fail

    async def edit_message(self, person, message_id, text, parse_mode=None, final=False, keyboard=None):
        if self.fail:
            raise RuntimeError('429')
        self.edits.append((message_id, text, keyboard))
        return True


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_plate_edits_at_most_every_interval_and_skips_unchanged_text():
    async def go():
        tg, clock = FakeTelegram(), Clock()
        keys = [['cancel']]
        plate = gp.GenProgressPlate(tg, OWNER, 42, 'Рисую локально.', keys, every_s=25, clock=clock)
        job = {'status': 'running', 'progress': 0.42, 'progress_detail': detail()}
        assert await plate.tick(job) is False                 # just sent: no edit yet
        clock.t += 24
        assert await plate.tick(job) is False
        clock.t += 2
        assert await plate.tick(job) is True
        assert tg.edits == [(42, 'Рисую локально.\n▓▓▓▓░░░░░░ 42% · шаг 9/20 · осталось ~6 мин', keys)]
        clock.t += 30
        assert await plate.tick(job) is False, 'same text: no edit'
        clock.t += 30
        job = {'status': 'running', 'progress': 0.5, 'progress_detail': detail(step=11, eta_s=250)}
        assert await plate.tick(job) is True and len(tg.edits) == 2
        assert await plate.done(97) is True
        assert tg.edits[-1] == (42, 'Рисую локально.\n▓▓▓▓▓▓▓▓▓▓ 100% · готово за 97 с, файл проверен', None)
    asyncio.run(go())


def test_failed_edit_backs_off_and_never_raises():
    async def go():
        tg, clock = FakeTelegram(fail=True), Clock()
        plate = gp.GenProgressPlate(tg, OWNER, 42, 'h', every_s=25, clock=clock)
        clock.t += 26
        assert await plate.tick({'status': 'running'}) is False
        assert plate.every_s == 50
        clock.t += 30
        assert await plate.tick({'status': 'running'}) is False and plate.every_s == 50
        for _ in range(5):
            clock.t += 1000
            await plate.tick({'status': 'running', 'progress': 0.1, 'progress_detail': detail()})
        assert plate.every_s == gp.MAX_EVERY_S
    asyncio.run(go())


def test_no_message_id_or_no_edit_api_means_no_edits():
    async def go():
        clock = Clock()
        plate = gp.GenProgressPlate(FakeTelegram(), OWNER, None, 'h', clock=clock)
        clock.t += 1000
        assert await plate.tick({'status': 'running'}) is False and await plate.done(5) is False
        plate = gp.GenProgressPlate(object(), OWNER, 5, 'h', clock=clock)
        clock.t += 1000
        assert await plate.tick({'status': 'running'}) is False
        quick = gp.GenProgressPlate(FakeTelegram(), OWNER, 5, 'h', clock=clock)
        assert await quick.done(3) is False, 'nothing was shown: no extra edit for a quick job'
    asyncio.run(go())


# ------------------------------------------------------------------ Companion.generate end to end (fakes)

class FakeStudio:
    def __init__(self):
        self.polls = 0

    def __call__(self, request):
        url, method = request.url, request.method
        if url.path == '/api/studio/models':
            return httpx.Response(200, json={'items': [{'id': 'sdcpp:z-image-turbo', 'available': True}]})
        if url.path == '/api/studio/jobs' and method == 'POST':
            return httpx.Response(200, json={'id': 7, 'status': 'queued'})
        if url.path == '/api/studio/jobs/7':
            self.polls += 1
            if self.polls > 4:
                return httpx.Response(200, json={'id': 7, 'status': 'completed', 'progress': 1,
                                                 'progress_detail': detail(phase='verify')})
            return httpx.Response(200, json={'id': 7, 'status': 'running', 'progress': 0.1 * self.polls,
                                             'progress_detail': detail(step=self.polls * 2, eta_s=60 * (5 - self.polls))})
        if url.path == '/api/studio/runs':
            return httpx.Response(200, json={'items': [{'id': 'run-abc', 'job_id': 7, 'mime': 'image/png',
                                                        'model': 'sdcpp:z-image-turbo',
                                                        'sha256': hashlib.sha256(PNG).hexdigest()}]})
        if url.path == '/api/studio/runs/run-abc/file':
            return httpx.Response(200, content=PNG)
        raise AssertionError('unexpected ' + str(url))


def test_generate_edits_the_status_message_in_place_and_keeps_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, 'EDIT_EVERY_S', 0.0)
    calls = []

    def telegram(request):
        method = request.url.path.rsplit('/', 1)[-1]
        calls.append((method, request.content))
        if method == 'editMessageText':           # Telegram echoes the edited message
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': json.loads(request.content)['message_id']}})
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': 500 + len(calls)}})

    async def go():
        s = Settings((OWNER,), local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='bot-fixture',
                     core_token='core-fixture', image_enabled=True, image_deadline=60)
        store = Store(tmp_path)
        tg = Telegram(s, transport=httpx.MockTransport(telegram))
        core = Core(s, transport=httpx.MockTransport(FakeStudio()))
        models = Models(s, tmp_path, transport=httpx.MockTransport(lambda r: pytest.fail('no LLM call')))
        app = Companion(s, store, tg, core, models)
        app.image_poll_seconds = 0.01
        app.free_memory_gb = lambda: 64.0
        msg = {'from': {'id': OWNER.user_id, 'is_bot': False}, 'chat': {'id': OWNER.chat_id, 'type': 'private'},
               'text': '/img кот', '_callback': True}   # engine path; a typed /img queues via Qwen
        try:
            return await app.handle(OWNER, msg)
        finally:
            await tg.close(); await core.close(); await models.close(); store.close()

    assert asyncio.run(go()) is None
    sends = [i for i, (m, _) in enumerate(calls) if m == 'sendMessage']
    status_id = 501 + sends[0]
    edits = [c for m, c in calls if m == 'editMessageText']
    assert edits, 'the status message was never edited'
    bodies = [json.loads(c) if c.startswith(b'{') else dict(httpx.QueryParams(c.decode())) for c in edits]
    assert all(int(b['message_id']) == status_id for b in bodies), 'edits go to the status message, not new ones'
    live = [b for b in bodies if 'шаг' in b['text']]
    assert live and '% · шаг' in live[0]['text'] and 'осталось ~' in live[0]['text']
    assert all('Отмена' in json.dumps(b.get('reply_markup'), ensure_ascii=False) for b in live), 'Cancel kept'
    assert 'reply_markup' not in bodies[-1], 'the finished plate has no Cancel button'
    assert '100%' in bodies[-1]['text'] and 'файл проверен' in bodies[-1]['text']
    assert all('100%' not in b['text'] for b in live)
    assert sum(1 for m, _ in calls if m == 'sendMessage') == len(sends), 'no progress spam as new messages'
    assert [m for m, _ in calls].count('sendPhoto') == 1
