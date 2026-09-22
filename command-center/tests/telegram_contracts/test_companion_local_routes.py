"""Offline contracts for chatting with local MAIN/FAST models from Telegram.

Real HTTPX MockTransport + real SQLite store; no network, no live Telegram,
no live model. Proves the routing contract only, not owner delivery.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bcc.telegram_companion.adapters import Models, Telegram
from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', 10)
STRANGER_ID = 99999
MAIN_URL = 'http://127.0.0.1:8081/v1'
FAST_URL = 'http://127.0.0.1:8082/v1'
# llama-server echoes the *served* model id (the GGUF path without --alias),
# not the requested name; the config must hold that exact id.
MAIN_ID = r'C:\models\main-fixture.gguf'
FAST_ID = r'C:\models\fast-fixture.gguf'


def cfg(**kw):
    base = dict(local_url=MAIN_URL, local_model=MAIN_ID, local_timeout=240,
                fast_url=FAST_URL, fast_model=FAST_ID, fast_timeout=60)
    base.update(kw)
    return Settings((OWNER,), **base)


def msg(uid=OWNER.user_id, text='Привет', **kw):
    body = {'from': {'id': uid, 'is_bot': False}, 'chat': {'id': uid, 'type': 'private'}, **kw}
    if text is not None:
        body['text'] = text
    return body


def completion(model, text='ответ модели'):
    return {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}], 'model': model}


def model_handler(calls, *, main_ok=True, fast_ok=True):
    def handler(request):
        url = str(request.url)
        calls.append(url)
        if url.startswith(MAIN_URL):
            return httpx.Response(200, json=completion(MAIN_ID, 'MAIN ответ')) if main_ok else httpx.Response(503, json={})
        if url.startswith(FAST_URL):
            return httpx.Response(200, json=completion(FAST_ID, 'FAST ответ')) if fast_ok else httpx.Response(503, json={})
        raise AssertionError('unexpected network target ' + url)
    return handler


class NoCore:
    def __init__(self):
        self.calls = []

    async def status(self):
        self.calls.append('status')
        return {'reachable': True}

    async def task(self, task_id, identity):
        self.calls.append(('task', task_id))
        return {'task': {'id': task_id, 'status': self.status_value}, 'result': None}


# --- configuration --------------------------------------------------------

def test_fast_route_requires_url_and_model_and_loopback():
    with pytest.raises(ValueError):
        cfg(fast_model='')
    with pytest.raises(ValueError):
        cfg(fast_url='')
    with pytest.raises(ValueError):
        cfg(fast_url='http://192.168.1.5:8082/v1')
    assert Settings((OWNER,), local_model=MAIN_ID).fast_model == ''


def test_slow_dense_model_timeout_is_allowed_but_bounded():
    assert cfg(local_timeout=240).local_timeout == 240
    for bad in (0, 601, float('nan'), True):
        with pytest.raises(ValueError):
            cfg(local_timeout=bad)
    with pytest.raises(ValueError):
        cfg(fast_timeout=601)


# --- model routing --------------------------------------------------------

def test_plain_message_goes_to_main_only(tmp_path):
    calls = []
    async def run():
        m = Models(cfg(), tmp_path, transport=httpx.MockTransport(model_handler(calls)))
        try:
            assert await m.answer('hi', [], cloud_consent=False) == ('MAIN ответ', 'local')
        finally:
            await m.close()
    asyncio.run(run())
    assert len(calls) == 1 and calls[0].startswith(MAIN_URL)


def test_explicit_fast_never_touches_main_or_cloud(tmp_path):
    calls = []
    async def run():
        m = Models(cfg(), tmp_path, transport=httpx.MockTransport(model_handler(calls, fast_ok=False)))
        try:
            with pytest.raises(CompanionError, match='FAST_MODEL_UNAVAILABLE'):
                await m.answer('hi', [], cloud_consent=True, route='fast')
        finally:
            await m.close()
    asyncio.run(run())
    assert calls and all(c.startswith(FAST_URL) for c in calls)


def test_fast_not_configured_is_a_stable_refusal(tmp_path):
    async def run():
        m = Models(cfg(fast_url='', fast_model=''), tmp_path,
                   transport=httpx.MockTransport(lambda r: pytest.fail('no network expected')))
        try:
            with pytest.raises(CompanionError, match='FAST_MODEL_NOT_CONFIGURED'):
                await m.answer('hi', [], cloud_consent=False, route='fast')
        finally:
            await m.close()
    asyncio.run(run())


def test_main_failure_falls_back_to_local_fast_before_any_cloud(tmp_path):
    calls = []
    async def run():
        m = Models(cfg(), tmp_path, transport=httpx.MockTransport(model_handler(calls, main_ok=False)))
        try:
            assert await m.answer('hi', [], cloud_consent=False) == ('FAST ответ', 'fast')
            # MAIN is in back-off now: the next message goes straight to FAST.
            assert await m.answer('hi', [], cloud_consent=False) == ('FAST ответ', 'fast')
        finally:
            await m.close()
    asyncio.run(run())
    assert [c.startswith(MAIN_URL) for c in calls] == [True, False, False]
    assert not any('openrouter' in c for c in calls)


def test_both_local_down_without_consent_is_refused_not_clouded(tmp_path):
    calls = []
    async def run():
        m = Models(cfg(), tmp_path, transport=httpx.MockTransport(model_handler(calls, main_ok=False, fast_ok=False)))
        try:
            with pytest.raises(CompanionError, match='CLOUD_NOT_AUTHORIZED'):
                await m.answer('hi', [], cloud_consent=False)
        finally:
            await m.close()
    asyncio.run(run())
    assert all(c.startswith((MAIN_URL, FAST_URL)) for c in calls)


def test_served_id_mismatch_is_not_a_reply(tmp_path):
    # Owner typed a friendly name, but llama-server reports the GGUF path.
    calls = []
    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(200, json=completion(MAIN_ID))
    async def run():
        m = Models(cfg(local_model='qwen-main', fast_url='', fast_model=''), tmp_path,
                   transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(CompanionError):
                await m.answer('hi', [], cloud_consent=False)
        finally:
            await m.close()
    asyncio.run(run())


# --- conversation surface -------------------------------------------------

def companion(tmp_path, settings=None, telegram=None, handler=None):
    settings = settings or cfg()
    store = Store(tmp_path)
    models = Models(settings, tmp_path, transport=httpx.MockTransport(handler or model_handler([])))
    core = NoCore()
    return Companion(settings, store, telegram, core, models), store, models, core


def test_fast_command_and_model_listing(tmp_path):
    async def run():
        app, store, models, _ = companion(tmp_path)
        try:
            assert await app.handle(OWNER, msg(text='/fast 2+2?')) == '⚡ Быстрая · fast-fixture\n\nFAST ответ'
            assert await app.handle(OWNER, msg(text='просто вопрос')) == '🧠 Лучшая · main-fixture\n\nMAIN ответ'
            listing = await app.handle(OWNER, msg(text='/model'))
            assert 'Лучшая: main-fixture ← отвечает сейчас' in listing and 'Быстрая: fast-fixture' in listing
            assert MAIN_ID not in listing
            assert '/fast' in await app.handle(OWNER, msg(text='/help'))
            assert store.lane({'text': '/model'}) == 'control'
        finally:
            await models.close(); store.close()
    asyncio.run(run())


def test_owner_attachment_gets_explicit_reply_and_is_not_stored(tmp_path):
    async def run():
        app, store, models, _ = companion(tmp_path)
        try:
            voice = msg(text=None, voice={'file_id': 'FILE-ID-FIXTURE', 'file_size': 10}, caption='слушай')
            await app.ingest({'update_id': 5, 'message': voice})
            item = store.claim(OWNER.key, 'chat')
            assert item is not None
            _, body = item
            assert body['_rejected'] == 'attachment' and 'voice' not in body and 'caption' not in body
            assert 'не поддерживаются' in await app.handle(OWNER, body)
            await app.ingest({'update_id': 6, 'message': msg(text='x' * 4001)})
            _, body = store.claim(OWNER.key, 'chat')
            assert 'длиннее 4000' in await app.handle(OWNER, body)
        finally:
            await models.close(); store.close()
    asyncio.run(run())


def test_stranger_attachment_and_text_are_not_persisted(tmp_path):
    async def run():
        app, store, models, _ = companion(tmp_path)
        try:
            await app.ingest({'update_id': 1, 'message': msg(STRANGER_ID, text=None, photo=[{'file_id': 'x'}])})
            await app.ingest({'update_id': 2, 'message': msg(STRANGER_ID, text='дай доступ')})
            assert store.db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 0
            assert store.get('offset') == 3
        finally:
            await models.close(); store.close()
    asyncio.run(run())


# --- end-to-end through the worker with a mock Telegram transport ----------

def telegram_recorder(sent):
    def handler(request):
        method = request.url.path.rsplit('/', 1)[-1]
        body = json.loads(request.content)
        sent.append((method, body))
        if method == 'sendMessage':
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': len(sent)}})
        return httpx.Response(200, json={'ok': True, 'result': True})
    return handler


async def drain(app, person, lane, sent, want):
    worker = asyncio.create_task(app.worker(person, lane))
    try:
        for _ in range(400):
            if sum(1 for m, _ in sent if m == 'sendMessage') >= want:
                break
            await asyncio.sleep(0.01)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def test_worker_roundtrip_owner_gets_main_reply_stranger_gets_nothing(tmp_path):
    sent = []
    async def run():
        settings = cfg(bot_token='bot-fixture')
        tg = Telegram(settings, transport=httpx.MockTransport(telegram_recorder(sent)))
        app, store, models, _ = companion(tmp_path, settings, tg)
        try:
            await app.ingest({'update_id': 1, 'message': msg(STRANGER_ID, text='привет')})
            await app.ingest({'update_id': 2, 'message': msg(text='Как дела?')})
            await drain(app, OWNER, 'chat', sent, 1)
        finally:
            await tg.close(); await models.close(); store.close()
    asyncio.run(run())
    messages = [b for m, b in sent if m == 'sendMessage']
    buttons = messages[0].pop('reply_markup')['inline_keyboard'][0]
    assert [b['text'] for b in buttons] == ['🔁 Ещё раз', '⚡ Ответить быстрой']
    assert all(len(b['callback_data']) == 18 and 'Как дела' not in b['callback_data'] for b in buttons)
    assert messages == [{'chat_id': OWNER.chat_id, 'text': '🧠 Лучшая · main-fixture\n\nMAIN ответ', 'disable_web_page_preview': True}]
    assert all(b.get('chat_id') == OWNER.chat_id for _, b in sent)
    assert {m for m, _ in sent} <= {'sendMessage', 'sendChatAction'}


def test_pending_message_survives_restart_inflight_is_not_replayed(tmp_path):
    sent = []
    async def run():
        settings = cfg(bot_token='bot-fixture')
        first = Store(tmp_path)
        first.ingest(1, OWNER.key, {**msg(text='в работе'), '_update_id': 1})
        first.ingest(2, OWNER.key, {**msg(text='ждёт очереди'), '_update_id': 2})
        assert first.claim(OWNER.key, 'chat')[0] == 1  # crash while processing #1
        first.close()
        tg = Telegram(settings, transport=httpx.MockTransport(telegram_recorder(sent)))
        app, store, models, _ = companion(tmp_path, settings, tg)
        try:
            store.recover()
            assert store.get('offset') == 3
            await drain(app, OWNER, 'chat', sent, 1)
            phases = dict(store.db.execute('SELECT id, phase FROM inbox').fetchall())
            assert phases == {1: 'interrupted_unknown', 2: 'done'}
        finally:
            await tg.close(); await models.close(); store.close()
    asyncio.run(run())
    assert len([b for m, b in sent if m == 'sendMessage']) == 1


def test_waiting_approval_is_announced_once_and_never_decided_from_telegram(tmp_path):
    class RecordingTelegram:
        def __init__(self):
            self.calls = []
        async def send(self, person, text):
            self.calls.append((person.key, text)); return 1
    async def run():
        app, store, models, core = companion(tmp_path)
        app.telegram = RecordingTelegram()
        core.status_value = 'waiting_approval'
        try:
            nonce = store.propose(OWNER.key, {'prompt': 'delete temp files'})
            store.consume(OWNER.key, nonce)
            store.delegated(OWNER.key, nonce, 77, 'a' * 64)
            await app.notify_tasks(); await app.notify_tasks()
            assert len(app.telegram.calls) == 1 and 'ждёт подтверждения' in app.telegram.calls[0][1]
            core.status_value = 'completed'
            await app.notify_tasks()
            assert len(app.telegram.calls) == 2 and 'completed' in app.telegram.calls[1][1]
            # Only read-only task lookups reached Bossman; no approval API exists here.
            assert all(c[0] == 'task' for c in core.calls)
        finally:
            await models.close(); store.close()
    asyncio.run(run())


# --- settings chosen in the Bossman UI -------------------------------------

def test_default_route_fast_answers_with_fast_only(tmp_path):
    calls = []
    async def run():
        app, store, models, _ = companion(tmp_path, cfg(default_route='fast'), handler=model_handler(calls))
        try:
            assert await app.handle(OWNER, msg(text='вопрос')) == '⚡ Быстрая · fast-fixture\n\nFAST ответ'
        finally:
            await models.close(); store.close()
    asyncio.run(run())
    assert calls and all(c.startswith(FAST_URL) for c in calls)


def test_fast_fallback_can_be_disabled(tmp_path):
    calls = []
    async def run():
        m = Models(cfg(fast_fallback=False), tmp_path,
                   transport=httpx.MockTransport(model_handler(calls, main_ok=False)))
        try:
            with pytest.raises(CompanionError, match='CLOUD_NOT_AUTHORIZED'):
                await m.answer('hi', [], cloud_consent=False)
        finally:
            await m.close()
    asyncio.run(run())
    assert calls and all(c.startswith(MAIN_URL) for c in calls)


def test_route_and_enabled_validation():
    with pytest.raises(ValueError):
        cfg(default_route='fast', fast_url='', fast_model='')
    with pytest.raises(ValueError):
        cfg(default_route='cloud')
    with pytest.raises(ValueError):
        cfg(enabled='yes')


def test_disabled_companion_refuses_to_serve(tmp_path):
    from bcc.telegram_companion.__main__ import serve
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'people': [{'user_id': 11111, 'chat_id': 11111, 'role': 'owner'}],
                                'local_url': MAIN_URL, 'local_model': MAIN_ID, 'enabled': False}), encoding='utf-8')
    with pytest.raises(CompanionError, match='COMPANION_DISABLED'):
        asyncio.run(serve(path))


# --- chat-only scope: /best, /fast, no computer control --------------------

def test_best_and_fast_switch_per_chat_and_label_the_answering_model(tmp_path):
    calls = []
    async def run():
        app, store, models, _ = companion(tmp_path, handler=model_handler(calls))
        try:
            assert 'Быстрая · fast-fixture' in await app.handle(OWNER, msg(text='/fast'))
            assert store.lane({'text': '/fast'}) == 'control' and store.lane({'text': '/fast вопрос'}) == 'chat'
            assert (await app.handle(OWNER, msg(text='вопрос'))).startswith('⚡ Быстрая · fast-fixture\n\n')
            assert 'Быстрая: fast-fixture ← отвечает сейчас' in await app.handle(OWNER, msg(text='/model'))
            assert (await app.handle(OWNER, msg(text='/best один раз'))).startswith('🧠 Лучшая · main-fixture')
            assert (await app.handle(OWNER, msg(text='снова'))).startswith('⚡ Быстрая')   # one-shot kept the mode
            await app.handle(OWNER, msg(text='/best'))
            assert (await app.handle(OWNER, msg(text='ещё'))).startswith('🧠 Лучшая')
        finally:
            await models.close(); store.close()
    asyncio.run(run())
    assert not any('openrouter' in c for c in calls)


def test_best_timeout_falls_back_to_fastest_and_says_so(tmp_path):
    async def run():
        app, store, models, _ = companion(tmp_path, handler=model_handler([], main_ok=False))
        try:
            reply = await app.handle(OWNER, msg(text='вопрос'))
            assert reply.startswith('⚡ Быстрая · fast-fixture (лучшая не ответила вовремя)')
        finally:
            await models.close(); store.close()
    asyncio.run(run())


class RecordingCore:
    def __init__(self):
        self.calls = []
    def __getattr__(self, name):
        async def record(*a, **k):
            self.calls.append(name)
            raise AssertionError('Bossman must not be called in chat-only mode')
        return record


def test_delegation_disabled_refuses_task_and_actions_without_side_effects(tmp_path):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content) if request.content else {})
        return httpx.Response(200, json=completion(MAIN_ID, 'Я не могу управлять компьютером.'))
    async def run():
        settings = cfg(fast_url='', fast_model='')
        chat_only = Person(OWNER.user_id, OWNER.chat_id, 'owner', None)
        settings = Settings((chat_only,), **{k: getattr(settings, k) for k in ('local_url', 'local_model', 'local_timeout')})
        store = Store(tmp_path)
        models = Models(settings, tmp_path, transport=httpx.MockTransport(handler))
        core = RecordingCore()
        app = Companion(settings, store, None, core, models)
        try:
            for text in ('/task удали все файлы на рабочем столе', '/confirm 0123456789ab'):
                reply = await app.handle(chat_only, msg(text=text))
                assert 'пока недоступны' in reply
            reply = await app.handle(chat_only, msg(text='открой браузер и удали папку Documents'))
            assert reply.endswith('Я не могу управлять компьютером.')
            assert core.calls == []
            assert store.db.execute('SELECT count(*) FROM proposals').fetchone()[0] == 0
        finally:
            await models.close(); store.close()
    asyncio.run(run())
    # Only the plain chat reached the model, as text only: no tools are ever offered.
    assert len(requests) == 1
    assert set(requests[0]) == {'model', 'stream', 'max_tokens', 'messages'}


def test_long_answer_is_split_cleanly():
    from bcc.telegram_companion.adapters import split_message
    text = '\n\n'.join(f'Абзац {i}: ' + 'слово ' * 150 for i in range(12))
    parts = split_message(text)
    assert 1 < len(parts) <= 5 and all(len(p) <= 3500 for p in parts)
    assert parts[0].endswith(f'(1/{len(parts)})') and 'Абзац 0' in parts[0]
    assert split_message('коротко') == ['коротко']
    huge = split_message('x' * 50000)
    assert len(huge) == 5 and huge[-1].rstrip().endswith('(5/5)') and 'сокращён' in huge[-1]


def test_worker_sends_long_reply_as_several_messages(tmp_path):
    sent = []
    long_text = '\n\n'.join('Строка ' * 200 for _ in range(4))
    def models(request):
        return httpx.Response(200, json=completion(MAIN_ID, long_text))
    async def run():
        settings = cfg(bot_token='bot-fixture')
        tg = Telegram(settings, transport=httpx.MockTransport(telegram_recorder(sent)))
        app, store, m, _ = companion(tmp_path, settings, tg, handler=models)
        try:
            await app.ingest({'update_id': 1, 'message': msg(text='длинно')})
            await drain(app, OWNER, 'chat', sent, 2)
        finally:
            await tg.close(); await m.close(); store.close()
    asyncio.run(run())
    texts = [b['text'] for mth, b in sent if mth == 'sendMessage']
    assert len(texts) >= 2 and all(len(t) <= 3500 for t in texts)


def test_model_name_labels():
    from bcc.telegram_companion.service import model_name
    assert model_name(r'C:\m\gpt-oss\openai_gpt-oss-120b-MXFP4_MOE-00001-of-00002.gguf') == 'openai_gpt-oss-120b-MXFP4_MOE'
    assert model_name('C:/m/Qwen3.8-27B-UD-Q5_K_M.gguf') == 'Qwen3.8-27B-UD-Q5_K_M'
    assert model_name('main') == 'main'
