"""Inline keyboards + callback_query: owner-bound opaque tokens, no stale replay."""
from __future__ import annotations

import asyncio
import json

import httpx

from bcc.telegram_companion.adapters import Models, Telegram, markup
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.service import Companion, Reply
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
GUEST = Person(22222, 22222, 'guest', None)
STRANGER = 99999
MAIN_URL, FAST_URL = 'http://127.0.0.1:8083/v1', 'http://127.0.0.1:8082/v1'


def cfg():
    return Settings((OWNER, GUEST), local_url=MAIN_URL, local_model='best-model', fast_url=FAST_URL,
                    fast_model='fast-model', bot_token='bot-fixture')


def world(calls):
    def handler(request):
        url = str(request.url)
        if 'api.telegram.org' in url:
            calls.append((url.rsplit('/', 1)[-1], json.loads(request.content)))
            return httpx.Response(200, json={'ok': True, 'result': True})
        model = 'best-model' if url.startswith(MAIN_URL) else 'fast-model'
        if url.endswith('/props'):
            return httpx.Response(200, json={'modalities': {'vision': model == 'fast-model'}})
        return httpx.Response(200, json={'model': model, 'choices': [{'message': {'content': 'ответ ' + model},
                                                                      'finish_reason': 'stop'}]})
    return httpx.MockTransport(handler)


def msg(uid, text):
    return {'from': {'id': uid, 'is_bot': False}, 'chat': {'id': uid, 'type': 'private'}, 'text': text}


def press(uid, data, update_id, chat_type='private', chat_id=None):
    return {'update_id': update_id, 'callback_query': {
        'id': f'cb{update_id}', 'data': data, 'from': {'id': uid, 'is_bot': False},
        'message': {'message_id': 5, 'chat': {'id': chat_id or uid, 'type': chat_type}}}}


def with_app(tmp_path, body):
    calls = []
    async def go():
        s = cfg()
        transport = world(calls)
        store = Store(tmp_path)
        tg, models = Telegram(s, transport=transport), Models(s, tmp_path, transport=transport)
        app = Companion(s, store, tg, None, models)
        try:
            return await body(app, store)
        finally:
            await tg.close(); await models.close(); store.close()
    return asyncio.run(go()), calls


def data_of(keyboard, label):
    return next(d for row in keyboard for l, d in row if l == label)


def test_start_and_menu_show_main_menu(tmp_path):
    async def body(app, store):
        reply = await app.handle(OWNER, msg(OWNER.user_id, '/start'))
        assert isinstance(reply, Reply) and '/best' in reply
        labels = [l for row in reply.keyboard for l, _ in row]
        assert labels == ['🧠 Лучшая', '⚡ Самая быстрая', '👁 Модель для фото', '🎨 Сгенерировать картинку',
                          '❓ Какая модель?', 'ℹ️ Помощь', '🧹 Очистить историю']
        assert all(len(d) == 18 and d.startswith('b:') for row in reply.keyboard for _, d in row)
        menu = await app.handle(OWNER, msg(OWNER.user_id, '/menu'))
        assert len([1 for row in menu.keyboard for _ in row]) == 7
        assert store.lane({'text': '/menu'}) == 'control'
    with_app(tmp_path, body)


def test_each_menu_button_routes_to_its_command(tmp_path):
    expected = {'🧠 Лучшая': '/best', '⚡ Самая быстрая': '/fast', '👁 Модель для фото': '/photo',
                '🎨 Сгенерировать картинку': '/img', '❓ Какая модель?': '/model', 'ℹ️ Помощь': '/help',
                '🧹 Очистить историю': '/forget'}
    async def body(app, store):
        keyboard = (await app.handle(OWNER, msg(OWNER.user_id, '/menu'))).keyboard
        routed = {}
        for n, (label, command) in enumerate(expected.items(), start=10):
            await app.ingest(press(OWNER.user_id, data_of(keyboard, label), n))
            lane = store.lane({'text': command})
            _, queued = store.claim(OWNER.key, lane)
            routed[label] = queued['text']
            assert queued['_callback'] is True
            reply = await app.handle(OWNER, queued)   # the command really works from the button
            assert reply
        return routed
    routed, calls = with_app(tmp_path, body)
    assert routed == expected
    acks = [p for m, p in calls if m == 'answerCallbackQuery']
    assert len(acks) == 7 and all('text' not in p for p in acks)


def test_answer_buttons_again_and_other_model(tmp_path):
    async def body(app, store):
        reply = await app.handle(OWNER, msg(OWNER.user_id, 'Как дела?'))
        assert [l for l, _ in reply.keyboard[0]] == ['🔁 Ещё раз', '⚡ Ответить быстрой']
        await app.ingest(press(OWNER.user_id, data_of(reply.keyboard, '⚡ Ответить быстрой'), 20))
        _, queued = store.claim(OWNER.key, 'chat')
        assert queued['text'] == '/fast Как дела?'
        fast_reply = await app.handle(OWNER, queued)
        assert fast_reply.startswith('⚡ Быстрая · fast-model')
        assert [l for l, _ in fast_reply.keyboard[0]] == ['🔁 Ещё раз', '🧠 Ответить лучшей']
        await app.ingest(press(OWNER.user_id, data_of(reply.keyboard, '🔁 Ещё раз'), 21))
        _, queued = store.claim(OWNER.key, 'chat')
        assert queued['text'] == '/best Как дела?'
    with_app(tmp_path, body)


def test_strangers_and_group_callbacks_are_ignored_silently(tmp_path):
    async def body(app, store):
        keyboard = (await app.handle(OWNER, msg(OWNER.user_id, '/menu'))).keyboard
        token = data_of(keyboard, '🧹 Очистить историю')
        await app.ingest(press(STRANGER, token, 30))
        await app.ingest(press(OWNER.user_id, token, 31, chat_type='group', chat_id=-100500))
        assert store.db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 0
        assert store.get('offset') == 32
    _, calls = with_app(tmp_path, body)
    assert calls == []          # not even answerCallbackQuery for them


def test_unknown_foreign_and_stale_tokens_are_rejected(tmp_path):
    async def body(app, store):
        owner_kb = (await app.handle(OWNER, msg(OWNER.user_id, '/menu'))).keyboard
        for n, data in enumerate(['b:0123456789abcdef', 'x' * 18, '/forget', 'b:' + 'z' * 70, None], start=40):
            await app.ingest(press(OWNER.user_id, data, n))
        # guest presses the owner's button: bound to another person -> stale
        await app.ingest(press(GUEST.user_id, data_of(owner_kb, '🧹 Очистить историю'), 50))
        assert store.db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 0
        # "restart": a fresh companion has no tokens, so old buttons are stale
        fresh = Companion(app.settings, store, app.telegram, None, app.models)
        await fresh.ingest(press(OWNER.user_id, data_of(owner_kb, '🧠 Лучшая'), 51))
        assert store.db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 0
    _, calls = with_app(tmp_path, body)
    stale = [p for m, p in calls if m == 'answerCallbackQuery']
    assert len(stale) == 7 and all('устарела' in p['text'] for p in stale)


def test_markup_bounds_callback_data():
    m = markup([[('ok', 'b:0123456789abcdef'), ('too long', 'b:' + 'x' * 80)]])
    assert m == {'inline_keyboard': [[{'text': 'ok', 'callback_data': 'b:0123456789abcdef'}]]}
