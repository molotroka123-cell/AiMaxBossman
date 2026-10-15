"""Pult generation: local Qwen refines the prompt and the EXISTING generate()/animate() runs it AT ONCE.

Owner 13.10: «я дал команду и промт бот не должен спрашивать он должен исполнять» and «ты можешь что бы
qwen со мной общался и запустил генерацию что я хочу». Fake Ollama + fake Bossman Studio + fake Telegram
(httpx.MockTransport): no network, no GPU, no real Telegram message.
"""
from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest
from PIL import Image

from bcc.jev import config as jev_config
from bcc.jev.client import JevClient
from bcc.telegram_companion import gen_refine, jev_bridge
from bcc.telegram_companion.adapters import Core, Models, Telegram
from bcc.telegram_companion.config import CompanionError, Settings
from bcc.telegram_companion.service import VIDEO_MODEL, Companion, Reply
from bcc.telegram_companion.store import Store

from test_companion_image_gen import GUEST, OWNER, PNG, TelegramRecorder, cfg
from test_companion_media_models import MP4, MultiStudio
from test_companion_video_lengths import AnimStudio

VIDEO_PROMPT = ("A red fox runs through a snowy pine forest at sunrise, powder snow bursting under its paws, "
                "low tracking shot, golden backlight, cinematic film grain")
IMAGE_PROMPT = ("Cozy coffee shop on a rainy evening, warm window light, wide shot, wet street reflections, "
                "35mm photo, shallow depth of field")
PHOTO_PROMPT = ("She slowly smiles and waves her right hand, hair moving gently in the breeze, slow push-in, "
                "soft warm evening light, cinematic")
EDIT_PROMPT = "Replace the background with a warm orange sunset sky over the sea, keep everything else unchanged"
AFTER = ['✏️ Переделать', '✖️ Отмена']


class FakeOllama:
    def __init__(self, answers=(), models=gen_refine.REFINE_MODELS):
        self.answers, self.models = list(answers), list(models)
        self.chats, self.unloads, self.hosts = [], [], []

    def __call__(self, request):
        self.hosts.append(request.url.host)
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': m} for m in self.models]})
        if request.url.path == '/api/chat':
            body = json.loads(request.content)
            self.chats.append(body)
            content = self.answers.pop(0) if self.answers else 'PROMPT: ' + VIDEO_PROMPT
            return httpx.Response(200, json={'model': body['model'], 'message': {'role': 'assistant',
                                                                                 'content': content}})
        if request.url.path == '/api/generate':
            self.unloads.append(json.loads(request.content))
            return httpx.Response(200, json={'done': True})
        raise AssertionError('unexpected ' + str(request.url))

    def system(self, i):
        return self.chats[i]['messages'][0]['content']

    def user(self, i):
        return self.chats[i]['messages'][1]['content']


def msg(person, text, callback=False):
    m = {'from': {'id': person.user_id, 'is_bot': False}, 'chat': {'id': person.chat_id, 'type': 'private'},
         'text': text}
    if callback:
        m['_callback'] = True
    return m


def jpeg(w=1280, h=960):
    out = io.BytesIO()
    Image.new('RGB', (w, h), (30, 120, 200)).save(out, format='JPEG')
    return out.getvalue()


class PhotoTelegram(TelegramRecorder):
    def __init__(self, photo=None):
        super().__init__()
        self.photo = photo or jpeg()

    def __call__(self, request):
        url = str(request.url)
        if url.endswith('/getFile'):
            self.calls.append(('getFile', request.content))
            return httpx.Response(200, json={'ok': True, 'result': {'file_path': 'photos/p.jpg',
                                                                    'file_size': len(self.photo)}})
        if '/file/bot' in url:
            self.calls.append(('download', b''))
            return httpx.Response(200, content=self.photo)
        return super().__call__(request)


class EditStudio(AnimStudio):
    """AnimStudio + the Qwen-Image-Edit model; results are images."""

    def __call__(self, request):
        if request.url.path == '/api/studio/models':
            self.requests.append((request.method, request.url.path))
            return httpx.Response(200, json={'items': [{'id': m, 'available': True} for m in
                                  ('sdcpp:z-image-turbo', 'sdcpp:qwen-image-edit-2509', VIDEO_MODEL)]})
        return super().__call__(request)


class Harness:
    """One companion over a store dir; ``say`` returns the reply or 'ERR:<code>'.

    The llama.cpp Models transport fails the test on ANY call: no generation path may need the chat
    or vision model."""

    def __init__(self, tmp_path, ollama=None, studio=None, settings=None):
        self.tmp_path, self.ollama = tmp_path, ollama or FakeOllama()
        self.studio = studio or MultiStudio(file_bytes=MP4)
        self.tg = PhotoTelegram()
        self.settings = settings or cfg()

    async def __aenter__(self):
        s = self.settings
        self.store = Store(self.tmp_path)
        self.telegram = Telegram(s, transport=httpx.MockTransport(self.tg))
        self.core = Core(s, transport=httpx.MockTransport(self.studio))
        self.models = Models(s, self.tmp_path, transport=httpx.MockTransport(lambda r: pytest.fail('no LLM call')))
        self.app = Companion(s, self.store, self.telegram, self.core, self.models)
        self.app.image_poll_seconds = 0.01
        self.app.free_memory_gb = lambda: 64.0
        self.app.refine_transport = httpx.MockTransport(self.ollama)
        self.main_chat = []

        async def converse(person, message, text, route):
            self.main_chat.append(text)
            return 'MAIN-CHAT'
        self.app.converse = converse
        return self

    async def __aexit__(self, *exc):
        await self.telegram.close(); await self.core.close(); await self.models.close(); self.store.close()

    async def say(self, text, person=OWNER, callback=False):
        try:
            return await self.app.handle(person, msg(person, text, callback))
        except CompanionError as exc:
            return 'ERR:' + str(exc)

    async def photo(self, caption='', person=OWNER):
        m = msg(person, caption)
        m['_image'] = {'file_id': 'PHOTO1', 'kind': 'photo'}
        try:
            return await self.app.handle(person, m)
        except CompanionError as exc:
            return 'ERR:' + str(exc)

    def command(self, keyboard, label):
        for row in keyboard:
            for lab, data in row:
                if lab == label:
                    return self.app.buttons[data[2:]][1]
        raise AssertionError(f'no button {label!r} in {[l for r in keyboard for l, _ in r]}')

    async def press(self, reply, label):
        return await self.say(self.command(reply.keyboard, label), callback=True)

    async def press_sent(self, label):
        """Press a button of the last Telegram message that carried one (queue notices)."""
        for method, content in reversed(self.tg.calls):
            if method != 'sendMessage':
                continue
            body = json.loads(content)
            rows = (body.get('reply_markup') or {}).get('inline_keyboard') or []
            keyboard = [[(b['text'], b['callback_data']) for b in row] for row in rows]
            if any(lab == label for row in keyboard for lab, _ in row):
                return await self.say(self.command(keyboard, label), callback=True)
        raise AssertionError('no sent message with button ' + label)

    async def done(self):
        await self.app.gen_queue_join()

    def sent_texts(self):
        return [json.loads(c).get('text', '') for m, c in self.tg.calls if m == 'sendMessage']

    def speed_rows(self):
        path = self.tmp_path / gen_refine.SPEED_LOG
        return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def videos(self):
        return [c for m, c in self.tg.calls if m == 'sendVideo']

    def getfiles(self):
        return [m for m, _ in self.tg.calls if m == 'getFile']


def go(coro):
    return asyncio.run(coro)


def labels(reply):
    return [label for row in (reply.keyboard or []) for label, _ in row]


def assert_launched(reply, position=1):
    assert isinstance(reply, Reply), reply
    assert 'запущено без вопросов' in reply and f'позиция {position}' in reply
    assert labels(reply) == AFTER                       # no «Запустить», no confirm step


# ------------------------------------------------------------------ direct launch: text entry points
def test_vid_launches_at_once_with_the_refined_prompt(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            reply = await h.say('/vid лиса бежит по снежному лесу')
            assert_launched(reply)
            assert VIDEO_PROMPT in reply and 'bossman-uncensored-chat:latest' in reply
            [chat] = h.ollama.chats
            assert chat['model'] == 'bossman-uncensored-chat:latest'
            assert chat['think'] is False and chat['stream'] is False
            assert set(h.ollama.hosts) == {'127.0.0.1'}
            system, user = h.ollama.system(0), h.ollama.user(0)
            assert 'Wan2.2' in system and 'no confirmation' in system and 'minors' in system and 'REFUSE' in system
            assert 'лиса бежит по снежному лесу' in user and 'DATA' in user
            await h.done()
            [job] = h.studio.created
            assert job['model'] == VIDEO_MODEL and job['prompt'] == VIDEO_PROMPT
            assert job['settings']['length'] == '5s'
            [video] = h.videos()
            assert '⏱'.encode() in video and MP4 in video
            assert h.ollama.unloads == [{'model': 'bossman-uncensored-chat:latest', 'keep_alive': 0}]
            [row] = h.speed_rows()
            assert row['ok'] is True and row['model'] == VIDEO_MODEL and row['preset'] == '5s'
            for key in ('refine_s', 'queue_s', 'gen_s', 'total_s'):
                assert isinstance(row[key], (int, float)) and row[key] >= 0
            assert row['width'] == 832 and row['height'] == 480 and row['frames'] and row['steps']
            assert h.app.gen_draft(OWNER)['state'] == 'done'
            # «Запустить снова» on a finished job does not start a second one.
            again = await h.say('/gen_go ' + h.app.gen_draft(OWNER)['id'], callback=True)
            assert 'уже готово' in again.lower() and len(h.studio.created) == 1
    go(scenario())


def test_inline_length(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            assert_launched(await h.say('/vid 10 волны о скалы'))
            assert 'волны о скалы' in h.ollama.user(0) and '10 s' in h.ollama.system(0)
            await h.done()
            assert h.studio.created[0]['settings']['length'] == '10s'
    go(scenario())


@pytest.mark.parametrize('text,idea', [
    ('/pic кофейня под дождём', 'кофейня под дождём'),
    ('/img кофейня под дождём', 'кофейня под дождём'),
    ('нарисуй кофейню под дождём', 'кофейню под дождём'),
    ('Нарисуй картинку: кофейня под дождём', 'кофейня под дождём'),
    ('сделай картинку кофейня под дождём', 'кофейня под дождём'),
])
def test_image_entry_points_launch_at_once(tmp_path, text, idea):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + IMAGE_PROMPT]), studio=MultiStudio()) as h:
            reply = await h.say(text)
            assert_launched(reply)
            assert IMAGE_PROMPT in reply and 'Z-Image-Turbo' in reply
            assert idea in h.ollama.user(0) and 'Z-Image-Turbo' in h.ollama.system(0)
            await h.done()
            [job] = h.studio.created
            assert job['prompt'] == IMAGE_PROMPT and job['model'] == 'sdcpp:z-image-turbo'
            [row] = h.speed_rows()
            assert row['ok'] and row['preset'] == 'image' and row['frames'] is None
            assert h.main_chat == []
    go(scenario())


@pytest.mark.parametrize('text', ['Сделай видео: кот играет на пианино', 'сними видео кот играет на пианино'])
def test_video_phrases_launch_at_once(tmp_path, text):
    async def scenario():
        async with Harness(tmp_path) as h:
            assert_launched(await h.say(text))
            assert 'кот играет на пианино' in h.ollama.user(0)
            await h.done()
            assert h.studio.created[0]['model'] == VIDEO_MODEL
    go(scenario())


def test_button_img_still_runs_the_engine_directly(tmp_path):
    # «🔁 Ещё вариант» / /create choices carry a final prompt: no refine, no queue.
    async def scenario():
        async with Harness(tmp_path, studio=MultiStudio()) as h:
            assert await h.say('/img a red car', callback=True) is None
            assert h.ollama.chats == [] and h.studio.created[0]['prompt'] == 'a red car'
    go(scenario())


# ------------------------------------------------------------------ after-the-fact buttons
async def release_later(app, delay=0.1):
    await asyncio.sleep(delay)
    app.image_job = None


def test_redo_replaces_a_job_that_has_not_started(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + VIDEO_PROMPT, 'PROMPT: ' + VIDEO_PROMPT + ', dusk'])) as h:
            h.app.image_job = {'id': 1, 'cancel': False, 'who': 'someone'}      # Studio busy
            reply = await h.say('/vid лиса')
            assert_launched(reply, position=2)
            redo = await h.press(reply, '✏️ Переделать')
            assert 'different variant' in h.ollama.user(1)
            assert_launched(redo, position=2) and ', dusk' in redo
            asyncio.get_running_loop().create_task(release_later(h.app))
            await h.done()
            assert [j['prompt'] for j in h.studio.created] == [VIDEO_PROMPT + ', dusk']
    go(scenario())


def test_cancel_drops_a_queued_job(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app.image_job = {'id': 1, 'cancel': False, 'who': 'someone'}
            reply = await h.say('/vid лиса')
            out = await h.press(reply, '✖️ Отмена')
            assert 'Снято из очереди' in out
            h.app.image_job = None
            await h.done()
            assert h.studio.created == [] and h.app.gen_draft(OWNER)['state'] == 'cancelled'
    go(scenario())


def test_cancel_drops_queued_launches_via_slash_cancel(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app.image_job = {'id': 1, 'cancel': False, 'who': 'someone'}
            await h.say('/vid лиса')
            out = await h.say('/cancel')
            assert 'Из очереди пульта убрано: 1' in out
            h.app.image_job = None
            await h.done()
            assert h.studio.created == []
    go(scenario())


# ------------------------------------------------------------------ conversation with Qwen
def test_talk_about_generation_answers_briefly_and_launches(tmp_path):
    chat = 'SAY: Сейчас сниму, будет красиво!\nACTION: video\nIDEA: лиса бежит по снегу на рассвете'
    async def scenario():
        async with Harness(tmp_path, FakeOllama([chat, 'PROMPT: ' + VIDEO_PROMPT])) as h:
            reply = await h.say('хочу видео, где лиса бежит по снегу на рассвете')
            assert reply.startswith('🧠 Qwen · bossman-uncensored-chat:latest\nСейчас сниму, будет красиво!')
            assert_launched(reply)
            assert 'SAY:' in h.ollama.system(0) and 'Never ask for confirmation' in h.ollama.system(0)
            assert 'лиса бежит по снегу на рассвете' in h.ollama.user(0)
            assert 'лиса бежит по снегу на рассвете' in h.ollama.user(1)          # the refine call
            await h.done()
            [job] = h.studio.created
            assert job['prompt'] == VIDEO_PROMPT and h.main_chat == []
            assert {'role': 'assistant', 'content': 'Сейчас сниму, будет красиво!'} in h.store.history(OWNER.key)
    go(scenario())


def test_question_about_generation_is_answered_without_a_launch(tmp_path):
    chat = 'SAY: Wan делает ролик 5 секунд, можно до 30.\nACTION: none\nIDEA:'
    async def scenario():
        async with Harness(tmp_path, FakeOllama([chat])) as h:
            reply = await h.say('сколько секунд длится видео?')
            assert reply == '🧠 Qwen · bossman-uncensored-chat:latest\nWan делает ролик 5 секунд, можно до 30.'
            assert h.studio.created == [] and len(h.ollama.chats) == 1 and h.main_chat == []
    go(scenario())


def test_other_plain_text_keeps_the_existing_chat_route(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            assert await h.say('привет, как дела?') == 'MAIN-CHAT'
            assert h.ollama.chats == [] and h.main_chat == ['привет, как дела?']
    go(scenario())


def test_talk_falls_back_to_the_chat_route_when_ollama_is_down(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(models=['qwen3-coder:30b'])) as h:
            assert await h.say('расскажи про видео модели') == 'MAIN-CHAT'
            assert h.studio.created == []
    go(scenario())


def test_correction_right_after_a_launch_relaunches_the_revision(tmp_path):
    slow = VIDEO_PROMPT + ', slow motion'
    chat = 'SAY: Ок, медленнее.\nACTION: redo\nIDEA:'
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + VIDEO_PROMPT, chat, 'PROMPT: ' + slow])) as h:
            await h.say('/vid лиса')
            await h.done()
            reply = await h.say('сделай медленнее')
            assert reply.startswith('🧠 Qwen') and 'Ок, медленнее.' in reply
            assert_launched(reply)
            assert 'Last launched generation' in h.ollama.user(1) and VIDEO_PROMPT in h.ollama.user(1)
            fix = h.ollama.user(2)
            assert VIDEO_PROMPT in fix and 'сделай медленнее' in fix and 'FIX' in fix
            await h.done()
            assert [j['prompt'] for j in h.studio.created] == [VIDEO_PROMPT, slow]
    go(scenario())


def test_correction_words_without_a_recent_launch_are_plain_chat(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            assert await h.say('сделай медленнее') == 'MAIN-CHAT' and h.ollama.chats == []
    go(scenario())


# ------------------------------------------------------------------ photo entry points
@pytest.mark.parametrize('caption,length', [
    ('/vid она улыбается и машет рукой', '5s'), ('/animate 10 она улыбается и машет рукой', '10s'),
    ('оживи она улыбается и машет рукой', '5s'), ('/оживи она улыбается и машет рукой', '5s'),
    ('qwen она улыбается и машет рукой', '5s'), ('сделай видео: она улыбается и машет рукой', '5s'),
])
def test_photo_animation_captions_launch_at_once(tmp_path, caption, length):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + PHOTO_PROMPT]), studio=AnimStudio(file_bytes=MP4)) as h:
            reply = await h.photo(caption)
            assert_launched(reply)
            assert PHOTO_PROMPT in reply and 'Фото принято' not in reply
            system = h.ollama.system(0)
            assert "OWNER'S PHOTO" in system and 'image-to-video' in system
            assert 'она улыбается и машет рукой' in h.ollama.user(0)
            assert h.getfiles() == []                   # the photo is fetched when the queue runs it
            await h.done()
            [ref] = h.studio.references
            [job] = h.studio.created
            assert job['media'] == [{'run_id': 'ref-1', 'role': 'start'}]
            assert job['prompt'] == PHOTO_PROMPT and job['settings']['length'] == length
            [video] = h.videos()
            assert '⏱'.encode() in video
            [row] = h.speed_rows()
            assert row['ok'] and row['kind'] == 'photo'
    go(scenario())


def test_photo_with_a_bare_trigger_is_not_a_question(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + PHOTO_PROMPT]), studio=AnimStudio(file_bytes=MP4)) as h:
            reply = await h.photo('оживи')
            assert_launched(reply)
            assert 'оживи это фото' in h.ollama.user(0)   # default idea, no «напишите, что происходит»
            await h.done()
            assert len(h.studio.created) == 1
    go(scenario())


def test_vidphoto_button_launches_at_once(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + PHOTO_PROMPT]), studio=AnimStudio(file_bytes=MP4)) as h:
            assert_launched(await h.say('/vidphoto tg:PHOTO1', callback=True))
            assert 'Пришлите фото' in await h.say('/vidphoto nothing')
            await h.done()
            assert h.studio.created[0]['media'] == [{'run_id': 'ref-1', 'role': 'start'}]
    go(scenario())


@pytest.mark.parametrize('caption', ['/pic сделай фон закатом', '/img сделай фон закатом', '/edit сделай фон закатом',
                                     'нарисуй фон закатом'])
def test_photo_edit_captions_launch_at_once(tmp_path, caption):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + EDIT_PROMPT]), studio=EditStudio(file_bytes=PNG)) as h:
            reply = await h.photo(caption)
            assert_launched(reply)
            assert 'Qwen-Image-Edit' in h.ollama.system(0) and 'фон закатом' in h.ollama.user(0)
            assert h.getfiles() == []
            await h.done()
            [ref] = h.studio.references
            [job] = h.studio.created
            assert job['model'] == 'sdcpp:qwen-image-edit-2509'
            assert job['media'] == [{'run_id': 'ref-1', 'role': 'reference'}]
            assert job['prompt'].startswith('Edit image 1') and EDIT_PROMPT in job['prompt']
            assert [m for m, _ in h.tg.calls if m == 'sendPhoto']
    go(scenario())


def test_photo_edit_uses_saved_references(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + EDIT_PROMPT]), studio=EditStudio(file_bytes=PNG)) as h:
            h.store.put('media_refs:' + OWNER.key, {'1': {'run_id': 'face'}, '2': {'run_id': 'body'}})
            assert_launched(await h.photo('/edit сделай фон закатом'))
            await h.done()
            [job] = h.studio.created
            assert [m['run_id'] for m in job['media']] == ['ref-1', 'face', 'body']
            assert job['prompt'].startswith('Image 1 is the target')
    go(scenario())


def test_photo_generation_never_needs_the_vision_model(tmp_path):
    # Harness.models fails the test on any llama.cpp call (/props, chat, vision).
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + PHOTO_PROMPT]), studio=AnimStudio(file_bytes=MP4)) as h:
            assert_launched(await h.photo('/vid танцует'))
            await h.done()
            assert len(h.studio.created) == 1
    go(scenario())


def test_guest_photo_command_never_reaches_qwen(tmp_path):
    async def scenario():
        async with Harness(tmp_path, settings=cfg(image_guests=True)) as h:
            out = await h.say('/vidphoto tg:PHOTO1', person=GUEST)
            assert 'только для владельца' in out and h.ollama.chats == []
    go(scenario())


# ------------------------------------------------------------------ refusals: one line, the fixed rule
@pytest.mark.parametrize('caption', ['/vid сделай её голой', '/vid раздень её медленно', '/edit раздень её',
                                     '/pic сделай её обнажённой'])
def test_photo_sexualisation_is_one_rule_line(tmp_path, caption):
    async def scenario():
        async with Harness(tmp_path, studio=AnimStudio(file_bytes=MP4)) as h:
            out = await h.photo(caption)
            assert out == gen_refine.REFUSED_PHOTO and '\n' not in out
            assert getattr(out, 'keyboard', None) is None
            assert h.ollama.chats == [] and h.studio.created == [] and h.getfiles() == []
    go(scenario())


@pytest.mark.parametrize('text,rule', [('/vid голая школьница', 'REFUSED_MINOR'), ('/pic nude teen girl', 'REFUSED_MINOR'),
                                       ('/vid эротика с актрисой из сериала', 'REFUSED_REAL'),
                                       ('нарисуй голую соседку', 'REFUSED_REAL')])
def test_fixed_safety_rules_refuse_in_one_line_without_calling_the_model(tmp_path, text, rule):
    async def scenario():
        async with Harness(tmp_path) as h:
            out = await h.say(text)
            assert out == getattr(gen_refine, rule) and '\n' not in out and out.startswith('⛔ Отказ по правилу')
            assert getattr(out, 'keyboard', None) is None
            assert h.ollama.chats == [] and h.studio.created == []
    go(scenario())


def test_talk_refusal_is_one_line(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['REFUSE: реальный человек без согласия'])) as h:
            out = await h.say('хочу картинку с ней в купальнике')
            assert out == '⛔ Модель отказалась: реальный человек без согласия. Ничего не запущено.'
            assert getattr(out, 'keyboard', None) is None and h.studio.created == []
    go(scenario())


def test_model_refusal_is_one_line_and_nothing_launches(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['REFUSE: реальный человек'])) as h:
            out = await h.say('/pic что-то')
            assert out == '⛔ Модель отказалась: реальный человек. Ничего не запущено.'
            assert h.studio.created == []
    go(scenario())


def test_safety_backstop_on_the_model_output(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: a nude teenage schoolgirl on a beach at sunset'])) as h:
            out = await h.say('/vid пляж на закате')
            assert out == gen_refine.REFUSED_MINOR and h.studio.created == []
    go(scenario())


# ------------------------------------------------------------------ Qwen trouble: the command still runs
@pytest.mark.parametrize('answer', ['', '   ', 'PROMPT:', '<think>long reasoning</think>', 'ok'])
def test_unusable_answer_launches_the_owners_own_text(tmp_path, answer):
    async def scenario():
        async with Harness(tmp_path, FakeOllama([answer])) as h:
            out = await h.say('/vid лиса бежит')
            assert_launched(out)
            assert '⚠️ Qwen' in out and 'лиса бежит' in out
            await h.done()
            assert h.studio.created[0]['prompt'] == 'лиса бежит'
    go(scenario())


def test_fallback_model_and_missing_model(tmp_path):
    async def scenario():
        async with Harness(tmp_path / 'a', FakeOllama(models=['bossman-community-qwen-uncensored:latest'])) as h:
            assert_launched(await h.say('/vid лиса'))
            assert h.ollama.chats[0]['model'] == 'bossman-community-qwen-uncensored:latest'
        async with Harness(tmp_path / 'b', FakeOllama(models=['qwen3-coder:30b'])) as h:
            out = await h.say('/vid лиса')
            assert_launched(out) and 'не найдена в Ollama' in out
            assert h.ollama.chats == []
            await h.done()
            assert h.studio.created[0]['prompt'] == 'лиса'
    go(scenario())


@pytest.mark.parametrize('url', ['http://10.1.2.3:11434', 'http://127.0.0.1.evil.example:11434',
                                 'https://api.example.com', 'http://0.0.0.0:11434'])
def test_non_local_ollama_host_is_refused_before_any_request(tmp_path, url):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app.refine_url = url
            assert await h.say('/vid лиса') == 'ERR:REFINE_HOST_NOT_LOCAL'
            assert h.ollama.hosts == [] and h.studio.created == []
    go(scenario())


def test_busy_refiner_does_not_block_the_command(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app._refine_lock = asyncio.Lock()
            await h.app._refine_lock.acquire()
            out = await h.say('/vid лиса')
            assert_launched(out) and 'занята' in out
            assert h.ollama.chats == []
            h.app._refine_lock.release()
            await h.done()
    go(scenario())


# ------------------------------------------------------------------ STOP/pause, queue, failures
def test_stop_or_pause_blocks_before_the_model_is_asked(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.store.put('delegation_locked', True)
            for text in ('/vid лиса', '/pic кот', '/img кот', 'нарисуй кота'):
                assert await h.say(text) == gen_refine.STOP_LINE
            assert await h.photo('/vid танцует') == gen_refine.STOP_LINE
            assert h.ollama.chats == [] and h.studio.created == [] and h.app._gen_queue() == []
            h.store.put('delegation_locked', False)
            assert_launched(await h.say('/vid лиса'))
            await h.done()
            assert len(h.studio.created) == 1
    go(scenario())


def test_stop_pressed_while_queued_means_the_job_never_starts_and_can_be_relaunched(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app.image_job = {'id': 1, 'cancel': False, 'who': 'someone'}
            await h.say('/vid лиса')
            h.store.put('delegation_locked', True)
            asyncio.get_running_loop().create_task(release_later(h.app, 0.02))
            await h.done()
            assert h.studio.created == []
            assert any('СТОП' in t for t in h.sent_texts())
            assert h.speed_rows()[-1]['error'] == 'STOPPED_IN_QUEUE'
            assert await h.press_sent('🎬 Запустить снова') == gen_refine.STOP_LINE   # still locked
            h.store.put('delegation_locked', False)
            h.app.image_job = None
            assert_launched(await h.press_sent('🎬 Запустить снова'))
            await h.done()
            assert [j['prompt'] for j in h.studio.created] == [VIDEO_PROMPT]
    go(scenario())


def test_queue_stays_serial_and_reports_positions(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + VIDEO_PROMPT, 'PROMPT: ' + VIDEO_PROMPT + ', dusk'])) as h:
            h.app.image_job = {'id': 1, 'cancel': False, 'who': 'someone'}      # e.g. a /video already rendering
            first = await h.say('/vid лиса')
            assert 'позиция 2 (впереди 1)' in first
            second = await h.say('/vid лиса вечером')
            assert 'позиция 3 (впереди 2)' in second
            await asyncio.sleep(0.05)
            assert h.studio.created == []                       # still waiting behind the running job
            asyncio.get_running_loop().create_task(release_later(h.app))
            await h.done()
            assert [j['prompt'] for j in h.studio.created] == [VIDEO_PROMPT, VIDEO_PROMPT + ', dusk']
            rows = h.speed_rows()
            assert len(rows) == 2 and all(r['ok'] for r in rows) and rows[0]['wait_s'] >= 0.05
    go(scenario())


def test_low_memory_waits_and_retries_instead_of_failing(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app.queue_retry_s = 0.01
            free = iter([0.5, 0.5, 64.0, 64.0, 64.0])
            h.app.free_memory_gb = lambda: next(free, 64.0)
            await h.say('/vid лиса')
            await h.done()
            assert len(h.studio.created) == 1
            assert sum('⏳' in t for t in h.sent_texts()) == 1     # one honest waiting notice
    go(scenario())


def test_failed_generation_is_logged_and_offers_launch_again(tmp_path):
    async def scenario():
        async with Harness(tmp_path, studio=MultiStudio(file_bytes=MP4, finish='failed')) as h:
            await h.say('/vid лиса')
            await h.done()
            assert any('Генерация не удалась' in t for t in h.sent_texts())
            [row] = h.speed_rows()
            assert row['ok'] is False and row['error'] == 'IMAGE_GEN_FAILED'
            assert h.app.gen_draft(OWNER)['state'] == 'retry'
            h.studio.finish = 'completed'
            assert_launched(await h.press_sent('🎬 Запустить снова'))
            await h.done()
            assert len(h.studio.created) == 2 and h.speed_rows()[-1]['ok'] is True
    go(scenario())


def test_generation_disabled_is_said_at_once(tmp_path):
    async def scenario():
        async with Harness(tmp_path, settings=cfg(image_enabled=False)) as h:
            assert await h.say('/vid лиса') == 'ERR:IMAGE_GEN_DISABLED'
            assert h.ollama.chats == []
    go(scenario())


def test_owner_only(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            for text in ('/vid лиса', '/pic кот', '/style тёмный', '/speed', '/gen_go abcd1234'):
                out = await h.say(text, person=GUEST)
                assert 'только для владельца' in out
            assert await h.say('нарисуй кота', person=GUEST) == 'MAIN-CHAT'
            assert h.ollama.chats == [] and h.studio.created == []
    go(scenario())


# ------------------------------------------------------------------ style, speed, config
def test_style_persists_across_restart_and_reaches_the_refiner(tmp_path):
    async def first():
        async with Harness(tmp_path) as h:
            assert 'Стиль не задан' in await h.say('/style')
            saved = await h.say('/style тёплый плёночный свет, 35 мм, без текста')
            assert 'сохранён' in saved
    async def after_restart():
        async with Harness(tmp_path) as h:
            assert 'тёплый плёночный свет' in await h.say('/style')
            reply = await h.say('/vid лиса')
            assert 'стиль /style учтён' in reply
            system = h.ollama.system(0)
            assert 'тёплый плёночный свет, 35 мм, без текста' in system and 'STYLE' in system
            await h.done()
            assert 'очищен' in await h.say('/style сброс')
            await h.say('/vid лиса')
            assert 'тёплый' not in h.ollama.system(1)
            await h.done()
    go(first())
    go(after_restart())


def test_speed_shows_last_runs_and_medians_from_measurements(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            assert 'Замеров ещё нет' in await h.say('/speed')
            for gen, total in ((10, 40), (20, 50), (30, 90)):
                h.app._speed_append({'kind': 'video', 'model': VIDEO_MODEL, 'preset': '5s', 'width': 832,
                                     'height': 480, 'frames': 81, 'steps': 20, 'refine_s': 3.0, 'queue_s': 0.5,
                                     'gen_s': gen, 'total_s': total, 'ok': True})
            h.app._speed_append({'kind': 'video', 'model': VIDEO_MODEL, 'preset': '5s', 'gen_s': 999,
                                 'total_s': 999, 'ok': False, 'error': 'IMAGE_GEN_FAILED'})
            out = await h.say('/speed')
            assert 'Последние 4' in out and 'IMAGE_GEN_FAILED' in out
            assert 'n=3' in out and 'генерация 20 с' in out and 'всего 50 с' in out   # failure excluded
            assert 'Последние 2' in await h.say('/speed 2')
    go(scenario())


def test_new_commands_do_not_add_config_keys():
    # The installed companion of an older build rejects unknown config keys.
    import dataclasses
    names = {f.name for f in dataclasses.fields(Settings)}
    assert not {n for n in names if 'refine' in n or 'ollama' in n or 'speed' in n or 'style' in n
                or 'vision_model' in n or 'direct' in n}


def test_intent_and_photo_kind_helpers():
    assert gen_refine.photo_kind('/vid') == 'photo' and gen_refine.photo_kind('Оживи') == 'photo'
    assert gen_refine.photo_kind('/edit') == 'edit' and gen_refine.photo_kind('/img') == 'edit'
    assert gen_refine.photo_kind('что') is None
    assert gen_refine.gen_intent('хочу видео с котом') and gen_refine.gen_intent('нарисуй дом')
    assert not gen_refine.gen_intent('привет, как дела?')
    assert not gen_refine.gen_intent('сделай медленнее') and gen_refine.gen_intent('сделай медленнее', recent=True)


# ------------------------------------------------------------------ Jev
class JevTransport:
    def __init__(self, choice):
        self.choice, self.requests = choice, []

    def __call__(self, url, headers, body, timeout_s):
        req = json.loads(body)
        self.requests.append(req)
        ids = list(req['questions']['action']['criteria'])
        answer = {'choice': self.choice, 'confidence': 0.95,
                  'probabilities': {i: (1.0 if i == self.choice else 0.0) for i in ids}}
        return 200, json.dumps({'model': 'jev-fake', 'answers': {'action': answer}}).encode(), {}


def test_jev_actions_offer_vid_and_pic_with_the_owners_text():
    assert jev_bridge.ACTIONS['vid'][:2] == ('/vid', True)
    assert jev_bridge.ACTIONS['pic'][:2] == ('/pic', True)


def test_jev_vid_launches_through_the_same_flow_with_the_owners_own_text(tmp_path, monkeypatch):
    monkeypatch.setenv('BOSSMAN_JEV_KILL_FILE', str(tmp_path / 'absent.disabled'))
    monkeypatch.delenv('BOSSMAN_JEV_ENABLED', raising=False)

    async def scenario():
        async with Harness(tmp_path, settings=cfg(jev_enabled=True)) as h:
            transport = JevTransport('vid')
            h.app.jev_client = JevClient(jev_config.load(), transport=transport, key_provider=lambda: 'k')
            out = await h.say('/jev сними видео как лиса бежит по снегу')
            assert isinstance(out, Reply) and 'Jev → /vid' in out and VIDEO_PROMPT in out
            assert 'запущено без вопросов' in out
            assert 'vid' in transport.requests[0]['questions']['action']['criteria']
            assert 'сними видео как лиса бежит по снегу' in h.ollama.user(0)
            await h.done()
            assert len(h.studio.created) == 1
    go(scenario())


# ------------------------------------------------------------------ /edit_engine qwen|uncensored (z_image_nsfw_v2)
def test_edit_engine_switch_owner_only_and_z_image_nsfw_v2_single_photo_edit(tmp_path):
    """Переключатель `/edit_engine qwen|uncensored` (по умолчанию `qwen`, только владелец):
    - В режиме `uncensored` используется профиль `z_image_nsfw_v2` (`sdcpp:z-image-turbo`, `role="start"`),
      а обычные веса в `IMAGE_MODELS` не переименовываются.
    - В `draft` и `speed_log` при постановке в очередь фиксируются `engine`, `revision` и `inputs`."""
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + EDIT_PROMPT, 'PROMPT: ' + EDIT_PROMPT]),
                           studio=EditStudio(file_bytes=PNG)) as h:
            # 1. Гостю команда /edit_engine недоступна
            guest_out = await h.say('/edit_engine uncensored', person=GUEST)
            assert 'только для владельца' in guest_out

            # 2. По умолчанию выбран qwen
            status_default = await h.say('/edit_engine')
            assert isinstance(status_default, Reply)
            assert 'Qwen-Image-Edit-2509' in status_default
            assert '7567e753c45bdd16ea9c1c052af97c27a8a4a071' in status_default

            # 3. Переключаем на uncensored (z_image_nsfw_v2)
            switched = await h.say('/edit_engine uncensored')
            assert isinstance(switched, Reply)
            assert 'Z-Image NSFW v2' in switched
            assert 'z_image_nsfw_v2' in switched
            assert h.app.edit_engine_for(OWNER) == 'uncensored'

            # 4. Обычная картинка в IMAGE_MODELS не переименована
            from bcc.telegram_companion.service import IMAGE_MODELS
            assert IMAGE_MODELS['sdcpp:z-image-turbo'] == 'Z-Image-Turbo'

            # 5. Редактирование 1 фото через GenRefine в режиме uncensored идёт через sdcpp:z-image-turbo с role="start"
            reply = await h.photo('/edit сделай фон закатом')
            assert_launched(reply)
            assert 'Z-Image NSFW v2' in reply and 'z_image_nsfw_v2' in reply
            assert '7567e753c45bdd16ea9c1c052af97c27a8a4a071' in h.ollama.system(0)

            draft = h.app.gen_draft(OWNER)
            assert draft['engine'] == 'z_image_nsfw_v2'
            assert draft['revision'] == '7567e753c45bdd16ea9c1c052af97c27a8a4a071'
            assert draft['inputs'] == [{'role': 'start', 'source': 'tg:PHOTO1'}]

            await h.done()
            [job] = h.studio.created
            assert job['model'] == 'sdcpp:z-image-turbo'
            assert job['media'] == [{'run_id': 'ref-1', 'role': 'start'}]
            assert job['prompt'].startswith('Edit image 1.')

            [row] = h.speed_rows()
            assert row['ok'] is True
            assert row['engine'] == 'z_image_nsfw_v2'
            assert row['revision'] == '7567e753c45bdd16ea9c1c052af97c27a8a4a071'
            assert row['inputs'] == [{'role': 'start', 'source': 'tg:PHOTO1'}]
    go(scenario())


def test_edit_engine_uncensored_refuses_incompatible_persistent_references_honestly(tmp_path):
    """Z-Image NSFW v2 принимает только 1 кадр (`role="start"`): при наличии постоянных референсов
    (`/refs on` или сохранённых `media_refs`) система НЕ игнорирует их молча, а предупреждает и предлагает
    переключиться на Qwen (`/edit_engine qwen`) или отключить референсы (`/refs off`)."""
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + EDIT_PROMPT]), studio=EditStudio(file_bytes=PNG)) as h:
            await h.say('/edit_engine uncensored')
            h.store.put('media_refs:' + OWNER.key, {'1': {'run_id': 'face'}, '2': {'run_id': 'body'}})
            h.store.put('media_refs_active:' + OWNER.key, True)

            # 1. Прямая отправка фото с подписью /edit при активных референсах и uncensored
            reply_photo = await h.photo('/edit сделай фон закатом')
            assert isinstance(reply_photo, Reply)
            assert 'принимает ровно 1 входной кадр' in reply_photo
            assert 'не игнорируются молча' in reply_photo
            assert '/edit_engine qwen' in reply_photo
            assert h.ollama.chats == [] and h.studio.created == []

            # 2. Текстовая команда /edit по сохранённым референсам при uncensored
            reply_cmd = await h.say('/edit деловой костюм в офисе')
            assert isinstance(reply_cmd, Reply)
            assert 'принимает ровно 1 входной кадр' in reply_cmd
            assert '/edit_engine qwen' in reply_cmd
            assert h.studio.created == []

            # 3. После нажатия кнопки переключения на Qwen редактирование с 3 референсами проходит штатно
            await h.press(reply_photo, '🎨 На Qwen (/edit_engine qwen)')
            assert h.app.edit_engine_for(OWNER) == 'qwen'
            launched = await h.photo('/edit сделай фон закатом')
            assert_launched(launched)
            draft = h.app.gen_draft(OWNER)
            assert draft['engine'] == 'qwen'
            assert len(draft['inputs']) == 3
            await h.done()
            [job] = h.studio.created
            assert job['model'] == 'sdcpp:qwen-image-edit-2509'
            assert [m['run_id'] for m in job['media']] == ['ref-1', 'face', 'body']
    go(scenario())
