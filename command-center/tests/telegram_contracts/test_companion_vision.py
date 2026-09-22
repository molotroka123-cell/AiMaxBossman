"""Photo recognition from Telegram: offline contracts.

One httpx.MockTransport plays both Telegram (getFile + file download) and the
local OpenAI-compatible servers (/props, /v1/chat/completions). No network.
"""
from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from bcc.telegram_companion.adapters import IMAGE_MAX_BYTES, Models, Telegram, image_mime
from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.service import IMAGE_PROMPT, Companion, failure_text
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
STRANGER = 99999
MAIN_URL, FAST_URL = 'http://127.0.0.1:8083/v1', 'http://127.0.0.1:8082/v1'
MAIN_ID, FAST_ID = r'C:\m\gpt-oss-120b.gguf', r'C:\m\Qwen3.6-35B-A3B.gguf'
JPEG = b'\xff\xd8\xff\xe0' + b'\x00JFIF' + b'\x11' * 200
PNG = b'\x89PNG\r\n\x1a\n' + b'\x22' * 200


def cfg(**kw):
    base = dict(local_url=MAIN_URL, local_model=MAIN_ID, fast_url=FAST_URL, fast_model=FAST_ID,
                bot_token='bot-fixture')
    base.update(kw)
    return Settings((OWNER,), **base)


class World:
    """Fake Telegram + fake llama.cpp servers; records every request."""

    def __init__(self, *, main_vision=False, fast_vision=True, file_bytes=JPEG, file_size=None,
                 content_length=None):
        self.requests = []
        self.main_vision, self.fast_vision = main_vision, fast_vision
        self.file_bytes, self.file_size, self.content_length = file_bytes, file_size, content_length

    def __call__(self, request):
        url = str(request.url)
        self.requests.append((request.method, url, request.content))
        if url.endswith('/getFile'):
            size = len(self.file_bytes) if self.file_size is None else self.file_size
            return httpx.Response(200, json={'ok': True, 'result': {'file_id': 'F', 'file_path': 'photos/file_1.jpg',
                                                                    'file_size': size}})
        if '/file/bot' in url:
            headers = {'content-length': str(self.content_length)} if self.content_length else {}
            return httpx.Response(200, content=self.file_bytes, headers=headers)
        if url.endswith('/props'):
            vision = self.main_vision if url.startswith('http://127.0.0.1:8083') else self.fast_vision
            return httpx.Response(200, json={'modalities': {'vision': vision, 'audio': False}})
        if url.endswith('/chat/completions'):
            model = MAIN_ID if url.startswith(MAIN_URL) else FAST_ID
            return httpx.Response(200, json={'model': model, 'choices': [
                {'message': {'content': 'На фото кот.'}, 'finish_reason': 'stop'}]})
        raise AssertionError('unexpected request ' + url)

    def urls(self, part):
        return [u for _, u, _ in self.requests if part in u]

    def chat_payloads(self):
        return [json.loads(c) for _, u, c in self.requests if u.endswith('/chat/completions')]


def photo_msg(uid=OWNER.user_id, caption=None, sizes=None):
    m = {'from': {'id': uid, 'is_bot': False}, 'chat': {'id': uid, 'type': 'private'},
         'photo': sizes or [{'file_id': 'small', 'width': 90, 'height': 90, 'file_size': 1000},
                            {'file_id': 'large', 'width': 1280, 'height': 960, 'file_size': 200000}]}
    if caption is not None:
        m['caption'] = caption
    return m


def run_photo(tmp_path, world, message, settings=None):
    """Ingest one update, claim it and answer it like the worker does."""
    async def go():
        s = settings or cfg()
        transport = httpx.MockTransport(world)
        store = Store(tmp_path)
        tg, models = Telegram(s, transport=transport), Models(s, tmp_path, transport=transport)
        app = Companion(s, store, tg, None, models)
        try:
            await app.ingest({'update_id': 1, 'message': message})
            item = store.claim(OWNER.key, 'chat')
            if item is None:
                return None, store.history(OWNER.key), store
            _, body = item
            try:
                reply = await app.handle(OWNER, body)
            except CompanionError as exc:
                reply = 'ERR:' + str(exc)
            return reply, store.history(OWNER.key), store
        finally:
            await tg.close(); await models.close(); store.close()
    return asyncio.run(go())


def test_photo_with_caption_goes_to_vision_route_as_data_uri(tmp_path):
    world = World()
    reply, history, _ = run_photo(tmp_path, world, photo_msg(caption='Что за животное?'))
    assert reply.startswith('👁 ⚡ Быстрая · Qwen3.6-35B-A3B (лучшая модель не видит изображения)')
    assert reply.endswith('На фото кот.')
    [payload] = world.chat_payloads()
    content = payload['messages'][-1]['content']
    assert content[0] == {'type': 'text', 'text': 'Что за животное?'}
    assert content[1]['type'] == 'image_url'
    assert content[1]['image_url']['url'] == 'data:image/jpeg;base64,' + base64.b64encode(JPEG).decode()
    assert 'tools' not in payload
    # the largest photo size was requested
    get_file = [json.loads(c) for _, u, c in world.requests if u.endswith('/getFile')]
    assert get_file == [{'file_id': 'large'}]


def test_photo_without_caption_uses_default_russian_prompt(tmp_path):
    world = World()
    reply, _, _ = run_photo(tmp_path, world, photo_msg())
    assert world.chat_payloads()[0]['messages'][-1]['content'][0]['text'] == IMAGE_PROMPT
    assert 'На фото кот.' in reply


def test_history_keeps_a_note_not_the_image(tmp_path):
    world = World()
    _, history, _ = run_photo(tmp_path, world, photo_msg(caption='подпись'))
    dump = json.dumps(history, ensure_ascii=False)
    assert history[0] == {'role': 'user', 'content': '[фото] подпись'}
    assert base64.b64encode(JPEG).decode()[:40] not in dump and 'base64' not in dump


def test_strangers_photo_is_ignored_and_never_downloaded(tmp_path):
    world = World()
    reply, _, _ = run_photo(tmp_path, world, photo_msg(uid=STRANGER, caption='привет'))
    assert reply is None and world.requests == []


def test_oversize_rejected_before_download(tmp_path):
    # Telegram metadata says too large: rejected at ingest, zero requests.
    world = World()
    big = [{'file_id': 'huge', 'width': 5000, 'height': 5000, 'file_size': IMAGE_MAX_BYTES + 1}]
    reply, _, _ = run_photo(tmp_path, world, photo_msg(sizes=big))
    assert '10 МБ' in reply and world.requests == []
    # getFile reports oversize: no file download request.
    world = World(file_size=IMAGE_MAX_BYTES + 5)
    reply, _, _ = run_photo(tmp_path / 'b', world, photo_msg())
    assert reply == 'ERR:IMAGE_TOO_LARGE' and world.urls('/file/bot') == []
    # Download header announces oversize: refused without reading the body.
    world = World(file_size=1000, content_length=IMAGE_MAX_BYTES + 5)
    reply, _, _ = run_photo(tmp_path / 'c', world, photo_msg())
    assert reply == 'ERR:IMAGE_TOO_LARGE' and world.chat_payloads() == []


def test_stream_cap_enforced_without_size_hints(tmp_path):
    world = World(file_bytes=JPEG + b'\x00' * (IMAGE_MAX_BYTES + 10), file_size=1000)
    reply, _, _ = run_photo(tmp_path, world, photo_msg())
    assert reply == 'ERR:IMAGE_TOO_LARGE' and world.chat_payloads() == []


def test_non_image_bytes_rejected_and_model_not_called(tmp_path):
    world = World(file_bytes=b'MZ\x90\x00 this is an exe')
    reply, _, _ = run_photo(tmp_path, world, photo_msg())
    assert reply == 'ERR:IMAGE_NOT_RECOGNISED' and world.chat_payloads() == []


def test_image_documents_png_accepted_other_types_refused(tmp_path):
    world = World(file_bytes=PNG)
    doc = {'from': {'id': OWNER.user_id, 'is_bot': False}, 'chat': {'id': OWNER.user_id, 'type': 'private'},
           'document': {'file_id': 'doc', 'mime_type': 'image/png', 'file_size': 300}}
    reply, _, _ = run_photo(tmp_path, world, doc)
    assert 'На фото кот.' in reply
    assert world.chat_payloads()[0]['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    for mime, expected in (('image/gif', 'JPEG, PNG или WebP'), ('application/pdf', 'не поддерживаются')):
        world = World()
        doc['document'] = {'file_id': 'doc', 'mime_type': mime, 'file_size': 300}
        reply, _, _ = run_photo(tmp_path / mime.replace('/', '_'), world, doc)
        assert expected in reply and world.requests == []


def test_vision_route_selection(tmp_path):
    # best (main) has vision -> main answers, no "can't see" note
    world = World(main_vision=True, fast_vision=True)
    reply, _, _ = run_photo(tmp_path / 'a', world, photo_msg())
    assert reply.startswith('👁 🧠 Лучшая · gpt-oss-120b\n')
    # nobody advertises vision -> clear refusal plus the "animate" offer; image never downloaded
    world = World(main_vision=False, fast_vision=False)
    reply, _, _ = run_photo(tmp_path / 'b', world, photo_msg())
    assert reply.startswith(failure_text('NO_VISION_MODEL')) and world.urls('getFile') == []
    assert [label for row in reply.keyboard for label, _ in row] == ['🎞 Оживить 5 с', '🎞 Оживить 10 с']
    # explicit owner setting wins without probing
    world = World(main_vision=False, fast_vision=False)
    reply, _, _ = run_photo(tmp_path / 'c', world, photo_msg(), settings=cfg(vision_route='fast'))
    assert 'Быстрая' in reply and world.urls('/props') == []


def test_vision_setting_validation():
    with pytest.raises(ValueError):
        cfg(vision_route='cloud')
    with pytest.raises(ValueError):
        cfg(vision_route='fast', fast_url='', fast_model='')


def test_magic_bytes():
    assert image_mime(JPEG) == 'image/jpeg' and image_mime(PNG) == 'image/png'
    assert image_mime(b'RIFF\x00\x00\x00\x00WEBPVP8 ') == 'image/webp'
    assert image_mime(b'GIF89a') is None and image_mime(b'') is None
