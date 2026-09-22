"""/video + отмена: бот присылает готовую часть с честной подписью, либо говорит, что нечего.

Всё офлайн: Studio — MockTransport, Telegram — MockTransport, движка и сети нет.
Проверяется ровно то, что велел владелец: «стоп обрывает на том, что уже есть», при этом
неполный ролик НИКОГДА не выдаётся за полный.
"""
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
# A minimal but real-shaped MP4 head: is_mp4() checks the ftyp box, the bytes never decode here.
MP4 = b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41' + b'\x00' * 256


def cfg(**kw):
    base = dict(local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='bot-fixture',
                core_token='core-fixture', image_enabled=True, image_deadline=60)
    base.update(kw)
    return Settings((OWNER,), **base)


class FakeStudio:
    """A Studio whose video job is stopped by the owner and leaves `segments_done` segments."""

    def __init__(self, *, segments_done=1, segments_total=3, file_bytes=MP4, sha=None,
                 run_appears_after=0, file_bytes_reported=None):
        self.segments_done, self.segments_total = segments_done, segments_total
        self.file_bytes, self.sha = file_bytes, sha
        self.run_appears_after = run_appears_after      # склейка идёт уже после отмены
        self.file_bytes_reported = file_bytes_reported
        self.created, self.cancelled, self.run_polls = [], False, 0

    def _run(self):
        have = self.segments_done * 81 / 16
        want = (81 + 80 * (self.segments_total - 1)) / 16
        return {'id': 'run-abc', 'job_id': 7, 'mime': 'video/mp4',
                'model': 'sdcpp:wan2.2-ti2v-5b',
                'file_bytes': self.file_bytes_reported if self.file_bytes_reported is not None
                else len(self.file_bytes),
                'sha256': self.sha or hashlib.sha256(self.file_bytes).hexdigest(),
                'provenance': {'partial': True, 'complete': False,
                               'partial_detail': {'segments_done': self.segments_done,
                                                  'segments_total': self.segments_total,
                                                  'duration_s': have, 'duration_s_if_complete': want,
                                                  'reason': 'canceled', 'stopped_by': 'canceled'}}}

    def __call__(self, request):
        url, method = request.url, request.method
        assert request.headers.get('X-BCC-Token') == 'core-fixture'
        if url.path == '/api/studio/models':
            return httpx.Response(200, json={'items': [{'id': 'sdcpp:wan2.2-ti2v-5b', 'available': True},
                                                       {'id': 'sdcpp:z-image-turbo', 'available': True}]})
        if url.path == '/api/studio/jobs' and method == 'POST':
            self.created.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 7, 'status': 'queued'})
        if url.path == '/api/studio/jobs/7/cancel':
            self.cancelled = True
            return httpx.Response(200, json={'id': 7, 'status': 'cancelled'})
        if url.path == '/api/studio/jobs/7':
            return httpx.Response(200, json={'id': 7, 'status': 'cancelled' if self.cancelled else 'running'})
        if url.path == '/api/studio/runs':
            assert url.params['job_id'] == '7' and url.params['surface'] == 'video'
            self.run_polls += 1
            items = [] if (self.segments_done == 0 or self.run_polls <= self.run_appears_after) else [self._run()]
            return httpx.Response(200, json={'items': items})
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

    def videos(self):
        return [c for m, c in self.calls if m == 'sendVideo']

    def texts(self):
        out = []
        for m, c in self.calls:
            if m == 'sendMessage':
                out.append(json.loads(c).get('text', '') if c[:1] == b'{' else c.decode('utf-8', 'replace'))
        return out


def run_cancelled(tmp_path, studio, text='/video 15 волны на закате'):
    tg_rec = TelegramRecorder()

    async def go():
        s = cfg()
        store = Store(tmp_path)
        tg = Telegram(s, transport=httpx.MockTransport(tg_rec))
        core = Core(s, transport=httpx.MockTransport(studio))
        models = Models(s, tmp_path, transport=httpx.MockTransport(lambda r: pytest.fail('no LLM call')))
        app = Companion(s, store, tg, core, models)
        app.image_poll_seconds = 0.01
        app.PARTIAL_WAIT_S = 2.0
        app.free_memory_gb = lambda: 64.0
        app.english_prompt = lambda p: asyncio.sleep(0, result=p)
        msg = {'from': {'id': OWNER.user_id, 'is_bot': False},
               'chat': {'id': OWNER.chat_id, 'type': 'private'}, 'text': text}

        async def press_cancel():
            for _ in range(500):
                if app.image_job and app.image_job['id']:
                    break
                await asyncio.sleep(0.01)
            app.image_job['cancel'] = True

        try:
            task = asyncio.create_task(app.handle(OWNER, msg))
            await press_cancel()
            try:
                return await task
            except CompanionError as exc:
                return 'ERR:' + str(exc)
        finally:
            await tg.close(); await core.close(); await models.close(); store.close()

    return asyncio.run(go()), tg_rec


def test_cancel_sends_the_finished_part_with_an_honest_caption(tmp_path):
    studio = FakeStudio(segments_done=1, segments_total=3)
    reply, tg = run_cancelled(tmp_path, studio)
    assert reply == 'ERR:IMAGE_GEN_CANCELLED' and studio.cancelled
    [video] = tg.videos()
    assert MP4 in video
    caption = video.decode('utf-8', 'replace')
    assert 'НЕПОЛНЫЙ' in caption, caption
    assert 'Готово 1 из 3' in caption
    assert '5.0625 с вместо 15.0625 с' in caption or '5.0625' in caption and '15.0625' in caption
    assert 'не повторён' in caption and 'не дорисован' in caption
    # ни одного слова, которым неполный ролик мог бы сойти за законченную съёмку
    for forbidden in ('Готово!', 'проверено: sha256 совпал', 'Ещё вариант'):
        assert forbidden not in caption, (forbidden, caption)


def test_cancel_with_zero_segments_says_there_is_nothing_to_save(tmp_path):
    studio = FakeStudio(segments_done=0, segments_total=3)
    reply, tg = run_cancelled(tmp_path, studio)
    assert reply == 'ERR:IMAGE_GEN_CANCELLED' and studio.cancelled
    assert tg.videos() == [], 'пустой или выдуманный ролик отправлен'
    assert any('Сохранять нечего' in t for t in tg.texts()), tg.texts()


def test_the_partial_waits_for_the_join_that_happens_after_the_stop(tmp_path):
    """Склейка сегментов идёт уже после отмены: бот обязан её дождаться, а не сдаться сразу."""
    studio = FakeStudio(segments_done=2, segments_total=3, run_appears_after=3)
    reply, tg = run_cancelled(tmp_path, studio)
    assert reply == 'ERR:IMAGE_GEN_CANCELLED'
    [video] = tg.videos()
    assert 'Готово 2 из 3' in video.decode('utf-8', 'replace')


def test_unverified_partial_bytes_are_never_sent(tmp_path):
    """Тот же байтовый шлагбаум, что и у полного результата: sha не сошёлся — не отправляем."""
    studio = FakeStudio(segments_done=1, sha='0' * 64)
    reply, tg = run_cancelled(tmp_path, studio)
    assert reply == 'ERR:IMAGE_GEN_CANCELLED' and tg.videos() == []

    studio = FakeStudio(segments_done=1, file_bytes=b'<svg>not a video</svg>')
    reply, tg = run_cancelled(tmp_path / 'b', studio)
    assert reply == 'ERR:IMAGE_GEN_CANCELLED' and tg.videos() == []


def test_a_partial_too_large_for_telegram_is_named_not_sent(tmp_path):
    studio = FakeStudio(segments_done=2, segments_total=6, file_bytes_reported=200 * 1024 * 1024)
    reply, tg = run_cancelled(tmp_path, studio)
    assert reply == 'ERR:IMAGE_GEN_CANCELLED' and tg.videos() == []
    assert any('больше 48 МБ' in t and '2 из 6' in t for t in tg.texts()), tg.texts()


def test_a_cancelled_image_job_never_triggers_the_video_partial_path(tmp_path):
    """Отрицательный контроль: частичная выдача — только для цепочки сегментов видео."""
    studio = FakeStudio(segments_done=1)
    reply, tg = run_cancelled(tmp_path, studio, text='/img кот-астронавт')
    assert reply == 'ERR:IMAGE_GEN_CANCELLED'
    assert tg.videos() == []
    assert not any('НЕПОЛНЫЙ' in t for t in tg.texts())
