"""Telegram side of Bossman Vision: 👍/👎 under every video, the reason after 👎 becomes a rule,
and the vision verdict follows the video. FAKE Studio and Telegram, no network."""
from __future__ import annotations

import asyncio
import json

import httpx

from bcc.telegram_companion.adapters import Core, Models, Telegram
from bcc.telegram_companion.config import CompanionError
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

from test_companion_image_gen import GUEST, OWNER, TelegramRecorder, cfg
from test_companion_media_models import MP4, MultiStudio


class ReviewStudio(MultiStudio):
    def __init__(self, review=None, **kw):
        super().__init__(file_bytes=MP4, **kw)
        self.review, self.feedback = review, []

    def __call__(self, request):
        path = request.url.path
        if path == '/api/studio/runs/run-abc/review':
            return httpx.Response(200, json={'run_id': 'run-abc', 'review': self.review, 'feedback': None})
        if path == '/api/studio/runs/run-abc/feedback':
            body = json.loads(request.content)
            self.feedback.append(body)
            return httpx.Response(200, json={'run_id': 'run-abc', 'review': self.review,
                                             'feedback': {**body, 'agreed_with_vision': None}})
        return super().__call__(request)


def talk(tmp_path, studio, texts, person=OWNER, prepare=None):
    rec = TelegramRecorder()

    async def go():
        s = cfg()
        store = Store(tmp_path)
        tg = Telegram(s, transport=httpx.MockTransport(rec))
        core = Core(s, transport=httpx.MockTransport(studio))
        models = Models(s, tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        app = Companion(s, store, tg, core, models)
        app.image_poll_seconds = 0.01
        app.free_memory_gb = lambda: 64.0
        if prepare:
            prepare(app, store)
        replies = []
        try:
            for text in texts:
                msg = {'from': {'id': person.user_id, 'is_bot': False},
                       'chat': {'id': person.chat_id, 'type': 'private'}, 'text': text}
                try:
                    replies.append(await app.handle(person, msg))
                except CompanionError as exc:
                    replies.append('ERR:' + str(exc))
            await asyncio.gather(*app.vision_tasks)
            return replies, app
        finally:
            await tg.close(); await core.close(); await models.close(); store.close()
    replies, app = asyncio.run(go())
    return replies, rec, app


def sent_texts(rec):
    return [json.loads(c)['text'] for m, c in rec.calls if m == 'sendMessage']


def video_buttons(rec):
    [body] = [c for m, c in rec.calls if m == 'sendVideo']
    crlf = bytes([13, 10])
    part = body.split(b'name="reply_markup"', 1)[1].split(crlf * 2, 1)[1].split(crlf, 1)[0]
    return [b['text'] for row in json.loads(part)['inline_keyboard'] for b in row]


def test_video_has_rating_buttons_and_the_vision_verdict_follows(tmp_path):
    studio = ReviewStudio(review={'verdict': 'BAD', 'score': 2, 'summary': 'морда плывёт',
                                  'defects': ['морда плывёт', 'мерцание'], 'owner_rules_applied': ['r1']})
    replies, rec, app = talk(tmp_path, studio, ['/video 5 лиса машет лапой'])
    assert replies == [None]
    assert video_buttons(rec) == ['🔁 Ещё вариант', '👍 Годно', '👎 Брак']
    commands = {cmd for _, cmd, _ in app.buttons.values()}
    assert {'/rate run-abc good', '/rate run-abc bad'} <= commands
    [verdict] = [t for t in sent_texts(rec) if 'Bossman Vision' in t]
    assert '❌ брак 2/10' in verdict and 'мерцание' in verdict and 'Учтено твоих правил: 1' in verdict


def test_missing_review_is_said_not_passed_as_good(tmp_path, monkeypatch):
    studio = ReviewStudio(review=None)

    def short_wait(app, store):
        original = app.vision_followup
        app.vision_followup = lambda person, rid: original(person, rid, wait_s=0.05)
    replies, rec, _ = talk(tmp_path, studio, ['/video 5 лиса'], prepare=short_wait)
    [verdict] = [t for t in sent_texts(rec) if 'Bossman Vision' in t]
    assert 'не успел' in verdict and 'годно' not in verdict
    studio2 = ReviewStudio(review={'verdict': 'INSUFFICIENT_EVIDENCE', 'reason': 'no local vision model'})
    _, rec2, _ = talk(tmp_path / 'b', studio2, ['/video 5 лиса'])
    [verdict2] = [t for t in sent_texts(rec2) if 'Bossman Vision' in t]
    assert 'не смог проверить' in verdict2 and 'no local vision model' in verdict2


def test_thumbs_down_then_reason_becomes_a_rule(tmp_path):
    studio = ReviewStudio()
    replies, _, _ = talk(tmp_path, studio, ['/rate run-abc bad', 'у неё пропал хвост и морда плывёт'])
    assert 'Что именно не так' in replies[0]
    assert 'Запомнил правило' in replies[1] and 'у неё пропал хвост' in replies[1]
    assert studio.feedback == [{'verdict': 'bad', 'reason': ''},
                               {'verdict': 'bad', 'reason': 'у неё пропал хвост и морда плывёт'}]


def test_a_command_while_waiting_for_a_reason_is_still_a_command(tmp_path):
    studio = ReviewStudio()
    replies, _, _ = talk(tmp_path, studio, ['/rate run-abc bad', '/rate run-abc good красивый свет'])
    assert studio.feedback[-1] == {'verdict': 'good', 'reason': 'красивый свет'}
    assert 'хорошо' in replies[1]


def test_expired_wait_does_not_swallow_the_next_message(tmp_path):
    studio = ReviewStudio()

    def stale(app, store):
        store.put('rate_wait:' + OWNER.key, {'run': 'run-abc', 'verdict': 'bad', 'until': 0})
    talk(tmp_path, studio, ['просто привет'], prepare=stale)
    assert studio.feedback == []          # the old 👎 does not eat a normal message


def test_guest_cannot_teach_the_owners_taste(tmp_path):
    studio = ReviewStudio()
    replies, _, _ = talk(tmp_path, studio, ['/rate run-abc bad мне не нравится'], person=GUEST)
    assert 'только от владельца' in replies[0] and studio.feedback == []


def test_rate_usage_and_bad_run_id(tmp_path):
    studio = ReviewStudio()
    replies, _, _ = talk(tmp_path, studio, ['/rate run-abc maybe', '/rate ../../x bad'])
    assert '/rate <run> good|bad' in replies[0]
    assert replies[1] == 'ERR:STUDIO_RUN_UNKNOWN' and studio.feedback == []


def test_a_guest_never_sees_local_paths_or_endpoints_from_a_failed_review(tmp_path):
    """Review P2 (a): the reviewer's failure reason carries local paths and URLs; a guest gets none of it."""
    reason = 'frames unavailable: C:/Users/asd/Bossman Test 0923/data/studio/x.mp4 via http://127.0.0.1:11435'
    studio = ReviewStudio(review={'verdict': 'INSUFFICIENT_EVIDENCE', 'reason': reason})
    rec = TelegramRecorder()

    async def go():
        s = cfg(image_guests=True)
        store = Store(tmp_path)
        tg = Telegram(s, transport=httpx.MockTransport(rec))
        core = Core(s, transport=httpx.MockTransport(studio))
        models = Models(s, tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        app = Companion(s, store, tg, core, models)
        app.image_poll_seconds = 0.01
        app.free_memory_gb = lambda: 64.0
        msg = {'from': {'id': GUEST.user_id, 'is_bot': False}, 'chat': {'id': GUEST.chat_id, 'type': 'private'},
               'text': '/video 5 лиса'}
        try:
            await app.handle(GUEST, msg)
            await asyncio.gather(*app.vision_tasks)
        finally:
            await tg.close(); await core.close(); await models.close(); store.close()
    asyncio.run(go())
    [verdict] = [t for t in sent_texts(rec) if 'Bossman Vision' in t]
    assert 'не смог проверить' in verdict
    for leak in ('C:/Users', 'Bossman Test', '127.0.0.1', '11435', 'http'):
        assert leak not in verdict, verdict
    assert video_buttons(rec) == ['🔁 Ещё вариант']      # and a guest gets no rating buttons
