"""Pult /vid, /pic, /style, /speed: local Qwen refines the prompt, the EXISTING generate() runs it.

Fake Ollama + fake Bossman Studio + fake Telegram (httpx.MockTransport): no network, no GPU.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bcc.jev import config as jev_config
from bcc.jev.client import JevClient
from bcc.telegram_companion import gen_refine, jev_bridge
from bcc.telegram_companion.adapters import Core, Models, Telegram
from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.service import VIDEO_MODEL, Companion, Reply, failure_text
from bcc.telegram_companion.store import Store

from test_companion_image_gen import GUEST, OWNER, TelegramRecorder, cfg
from test_companion_media_models import MP4, MultiStudio

VIDEO_PROMPT = ("A red fox runs through a snowy pine forest at sunrise, powder snow bursting under its paws, "
                "low tracking shot, golden backlight, cinematic film grain")
IMAGE_PROMPT = ("Cozy coffee shop on a rainy evening, warm window light, wide shot, wet street reflections, "
                "35mm photo, shallow depth of field")


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


def msg(person, text, callback=False):
    m = {'from': {'id': person.user_id, 'is_bot': False}, 'chat': {'id': person.chat_id, 'type': 'private'},
         'text': text}
    if callback:
        m['_callback'] = True
    return m


class Harness:
    """One companion over a store dir; ``say`` returns the reply or 'ERR:<code>'."""

    def __init__(self, tmp_path, ollama=None, studio=None, settings=None):
        self.tmp_path, self.ollama = tmp_path, ollama or FakeOllama()
        self.studio = studio or MultiStudio(file_bytes=MP4)
        self.tg = TelegramRecorder()
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
        return self

    async def __aexit__(self, *exc):
        await self.telegram.close(); await self.core.close(); await self.models.close(); self.store.close()

    async def say(self, text, person=OWNER, callback=False):
        try:
            return await self.app.handle(person, msg(person, text, callback))
        except CompanionError as exc:
            return 'ERR:' + str(exc)

    def command(self, reply, label):
        for row in reply.keyboard:
            for lab, data in row:
                if lab == label:
                    return self.app.buttons[data[2:]][1]
        raise AssertionError(f'no button {label!r} in {[l for r in reply.keyboard for l, _ in r]}')

    async def press(self, reply, label):
        return await self.say(self.command(reply, label), callback=True)

    async def launch(self, reply, label):
        """Press Launch, then let the pult queue finish (the reply is the queue notice)."""
        out = await self.press(reply, label)
        await self.app.gen_queue_join()
        return out

    def sent_texts(self):
        return [json.loads(c).get('text', '') for m, c in self.tg.calls if m == 'sendMessage']

    def speed_rows(self):
        path = self.tmp_path / gen_refine.SPEED_LOG
        return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def videos(self):
        return [c for m, c in self.tg.calls if m == 'sendVideo']


def go(coro):
    return asyncio.run(coro)


def labels(reply):
    return [label for row in reply.keyboard for label, _ in row]


# ------------------------------------------------------------------ refine -> buttons -> launch
def test_vid_refines_then_launch_runs_existing_generate_with_the_refined_prompt(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            reply = await h.say('/vid лиса бежит по снежному лесу')
            assert isinstance(reply, Reply) and VIDEO_PROMPT in reply
            assert labels(reply)[0] == '🎬 Запустить (5 с)'
            assert {'✏️ Переделать', 'Отмена', '⚡ 1 с TestRun', '10 с', '15 с', '30 с'} <= set(labels(reply))
            assert h.studio.created == []                       # nothing starts before Launch
            [chat] = h.ollama.chats
            assert chat['model'] == 'bossman-uncensored-chat:latest'
            assert chat['think'] is False and chat['stream'] is False
            assert set(h.ollama.hosts) == {'127.0.0.1'}
            system, user = chat['messages'][0]['content'], chat['messages'][1]['content']
            assert 'Wan2.2' in system and '/vid' in system and 'minors' in system and 'REFUSE' in system
            assert 'лиса бежит по снежному лесу' in user and 'DATA' in user

            out = await h.launch(reply, '🎬 Запустить (5 с)')
            assert out.startswith('📥 Видео поставлено в очередь пульта: позиция 1')
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
            assert row['refine_model'] == 'bossman-uncensored-chat:latest'
            # The draft is closed: the same button cannot start a second job.
            again = await h.press(reply, '🎬 Запустить (5 с)')
            assert 'устарел' in again and len(h.studio.created) == 1
    go(scenario())


def test_length_button_and_inline_length(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            reply = await h.say('/vid 10 волны о скалы')
            assert labels(reply)[0] == '🎬 Запустить (10 с)'
            assert 'волны о скалы' in h.ollama.chats[0]['messages'][1]['content']
            assert '10 s' in h.ollama.chats[0]['messages'][0]['content']
            await h.launch(reply, '⚡ 1 с TestRun')
            assert h.studio.created[0]['settings']['length'] == 'test_1s'
    go(scenario())


def test_correction_loop_revises_the_prompt_and_launch_uses_the_revision(tmp_path):
    revised = IMAGE_PROMPT + ", neon signs, night"
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + IMAGE_PROMPT, 'PROMPT: ' + revised]),
                           studio=MultiStudio()) as h:
            first = await h.say('/pic кофейня под дождём')
            assert IMAGE_PROMPT in first and 'Z-Image-Turbo' in first
            assert 'Z-Image-Turbo' in h.ollama.chats[0]['messages'][0]['content']
            second = await h.say('сделай ночь и неон')            # plain text = correction
            assert isinstance(second, Reply) and revised in second
            user = h.ollama.chats[1]['messages'][1]['content']
            assert IMAGE_PROMPT in user and 'сделай ночь и неон' in user and 'кофейня под дождём' in user
            out = await h.launch(second, '🎬 Запустить')
            assert 'позиция 1' in out
            [job] = h.studio.created
            assert job['prompt'] == revised and job['model'] == 'sdcpp:z-image-turbo'
            [row] = h.speed_rows()
            assert row['rounds'] == 2 and row['preset'] == 'image' and row['frames'] is None
            # Draft closed after launch: plain text is ordinary chat again (no refine call).
            assert h.app.gen_draft(OWNER) is None
    go(scenario())


def test_redo_asks_for_a_different_variant_and_cancel_closes(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + VIDEO_PROMPT, 'PROMPT: ' + VIDEO_PROMPT + ', dusk'])) as h:
            reply = await h.say('/vid лиса')
            redo = await h.press(reply, '✏️ Переделать')
            assert 'different variant' in h.ollama.chats[1]['messages'][1]['content']
            assert ', dusk' in redo
            closed = await h.press(redo, 'Отмена')
            assert 'ничего не запущено' in closed and h.app.gen_draft(OWNER) is None
            assert h.studio.created == []
    go(scenario())


def test_natural_phrase_reaches_the_flow(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            reply = await h.say('Сделай видео: кот играет на пианино')
            assert isinstance(reply, Reply) and labels(reply)[0].startswith('🎬 Запустить')
            assert 'кот играет на пианино' in h.ollama.chats[0]['messages'][1]['content']
    go(scenario())


# ------------------------------------------------------------------ style
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
            system = h.ollama.chats[0]['messages'][0]['content']
            assert 'тёплый плёночный свет, 35 мм, без текста' in system and 'STYLE' in system
            await h.say('/gen_cancel ' + h.app.gen_draft(OWNER)['id'])
            assert 'очищен' in await h.say('/style сброс')
            await h.say('/vid лиса')
            assert 'тёплый' not in h.ollama.chats[1]['messages'][0]['content']
    go(first())
    go(after_restart())


# ------------------------------------------------------------------ /speed
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


def test_failed_generation_is_logged_and_the_draft_stays_open(tmp_path):
    async def scenario():
        async with Harness(tmp_path, studio=MultiStudio(file_bytes=MP4, finish='failed')) as h:
            reply = await h.say('/vid лиса')
            await h.launch(reply, '🎬 Запустить (5 с)')
            assert any('Генерация не удалась' in t for t in h.sent_texts())
            [row] = h.speed_rows()
            assert row['ok'] is False and row['error'] == 'IMAGE_GEN_FAILED'
            assert h.app.gen_draft(OWNER) is not None            # can press Launch again
    go(scenario())


# ------------------------------------------------------------------ gates
def test_owner_only(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            for text in ('/vid лиса', '/pic кот', '/style тёмный', '/speed', '/gen_go abcd1234'):
                out = await h.say(text, person=GUEST)
                assert 'только для владельца' in out
            assert h.ollama.chats == [] and h.studio.created == []
    go(scenario())


def test_stop_or_pause_blocks_launch_and_keeps_the_draft(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            reply = await h.say('/vid лиса')
            h.store.put('delegation_locked', True)
            out = await h.press(reply, '🎬 Запустить (5 с)')
            assert 'СТОП' in out and h.studio.created == [] and h.ollama.unloads == []
            assert h.app.gen_draft(OWNER) is not None
            h.store.put('delegation_locked', False)
            assert 'позиция 1' in await h.launch(reply, '🎬 Запустить (5 с)')
            assert len(h.studio.created) == 1
    go(scenario())


@pytest.mark.parametrize('answer', ['', '   ', 'PROMPT:', '<think>long reasoning</think>', 'ok'])
def test_empty_or_invalid_answer_is_an_honest_error_and_nothing_launches(tmp_path, answer):
    async def scenario():
        async with Harness(tmp_path, FakeOllama([answer])) as h:
            out = await h.say('/vid лиса')
            assert out == 'ERR:REFINE_EMPTY'
            assert 'ничего не запущено' in failure_text('REFINE_EMPTY')
            assert h.app.gen_draft(OWNER) is None and h.studio.created == []
    go(scenario())


def test_model_refusal_is_shown_and_nothing_launches(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['REFUSE: реальный человек'])) as h:
            out = await h.say('/pic что-то')
            assert out.startswith('⛔ Модель отказалась: реальный человек')
            assert h.app.gen_draft(OWNER) is None and h.studio.created == []
    go(scenario())


@pytest.mark.parametrize('url', ['http://10.1.2.3:11434', 'http://127.0.0.1.evil.example:11434',
                                 'https://api.example.com', 'http://0.0.0.0:11434'])
def test_non_local_ollama_host_is_refused_before_any_request(tmp_path, url):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app.refine_url = url
            assert await h.say('/vid лиса') == 'ERR:REFINE_HOST_NOT_LOCAL'
            assert h.ollama.hosts == []
    go(scenario())


def test_fallback_model_and_missing_model(tmp_path):
    async def scenario():
        async with Harness(tmp_path / 'a', FakeOllama(models=['bossman-community-qwen-uncensored:latest'])) as h:
            await h.say('/vid лиса')
            assert h.ollama.chats[0]['model'] == 'bossman-community-qwen-uncensored:latest'
        async with Harness(tmp_path / 'b', FakeOllama(models=['qwen3-coder:30b'])) as h:
            assert await h.say('/vid лиса') == 'ERR:REFINE_MODEL_MISSING'
            assert h.ollama.chats == []
    go(scenario())


@pytest.mark.parametrize('idea', ['голая школьница', 'nude teen girl', 'эротика с актрисой из сериала'])
def test_fixed_safety_rules_refuse_without_calling_the_model(tmp_path, idea):
    async def scenario():
        async with Harness(tmp_path) as h:
            out = await h.say('/vid ' + idea)
            assert out.startswith('⛔ Отказ') and h.ollama.chats == [] and h.studio.created == []
    go(scenario())


def test_safety_backstop_on_the_model_output(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: a nude teenage schoolgirl on a beach at sunset'])) as h:
            out = await h.say('/vid пляж на закате')
            assert out.startswith('⛔ Отказ') and h.app.gen_draft(OWNER) is None and h.studio.created == []
    go(scenario())


def test_one_refine_at_a_time(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app._refine_lock = asyncio.Lock()
            await h.app._refine_lock.acquire()
            assert await h.say('/vid лиса') == 'ERR:REFINE_BUSY'
            assert h.ollama.chats == []
    go(scenario())


def test_new_commands_do_not_add_config_keys():
    # The installed companion of an older build rejects unknown config keys.
    import dataclasses
    names = {f.name for f in dataclasses.fields(Settings)}
    assert not {n for n in names if 'refine' in n or 'ollama' in n or 'speed' in n or 'style' in n}


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


def test_jev_vid_goes_through_the_refine_flow_with_the_owners_own_text(tmp_path, monkeypatch):
    monkeypatch.setenv('BOSSMAN_JEV_KILL_FILE', str(tmp_path / 'absent.disabled'))
    monkeypatch.delenv('BOSSMAN_JEV_ENABLED', raising=False)

    async def scenario():
        async with Harness(tmp_path, settings=cfg(jev_enabled=True)) as h:
            transport = JevTransport('vid')
            h.app.jev_client = JevClient(jev_config.load(), transport=transport, key_provider=lambda: 'k')
            out = await h.say('/jev сними видео как лиса бежит по снегу')
            assert isinstance(out, Reply) and 'Jev → /vid' in out and VIDEO_PROMPT in out
            assert 'vid' in transport.requests[0]['questions']['action']['criteria']
            user = h.ollama.chats[0]['messages'][1]['content']
            assert 'сними видео как лиса бежит по снегу' in user
            assert h.studio.created == []                       # Jev never launches; the owner presses
    go(scenario())


# ------------------------------------------------------------------ photo -> Qwen -> existing /animate path
import io  # noqa: E402

from PIL import Image  # noqa: E402

from test_companion_video_lengths import AnimStudio  # noqa: E402

PHOTO_PROMPT = ("She slowly smiles and waves her right hand, hair moving gently in the breeze, slow push-in, "
                "soft warm evening light, cinematic")


def jpeg(w=1280, h=960):
    out = io.BytesIO()
    Image.new('RGB', (w, h), (30, 120, 200)).save(out, format='JPEG')
    return out.getvalue()


class PhotoTelegram(TelegramRecorder):
    def __init__(self, photo):
        super().__init__()
        self.photo = photo

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


class PhotoHarness(Harness):
    def __init__(self, tmp_path, ollama=None, **kw):
        super().__init__(tmp_path, ollama or FakeOllama(['PROMPT: ' + PHOTO_PROMPT] * 4),
                         studio=kw.pop('studio', None) or AnimStudio(file_bytes=MP4), **kw)
        self.tg = PhotoTelegram(jpeg())

    async def photo(self, caption='', person=OWNER):
        m = msg(person, caption)
        m['_image'] = {'file_id': 'PHOTO1', 'kind': 'photo'}
        try:
            return await self.app.handle(person, m)
        except CompanionError as exc:
            return 'ERR:' + str(exc)


def test_photo_with_qwen_caption_refines_and_launch_animates_through_the_existing_path(tmp_path):
    async def scenario():
        async with PhotoHarness(tmp_path) as h:
            reply = await h.photo('/vid она улыбается и машет рукой')
            assert isinstance(reply, Reply) and PHOTO_PROMPT in reply and 'оживление фото' in reply
            assert labels(reply) == ['🎬 Запустить (5 с)', '10 с', '✏️ Переделать', 'Отмена']
            system = h.ollama.chats[0]['messages'][0]['content']
            assert "OWNER'S PHOTO" in system and 'image-to-video' in system
            assert 'она улыбается и машет рукой' in h.ollama.chats[0]['messages'][1]['content']
            assert h.studio.created == [] and not [m for m, _ in h.tg.calls if m == 'getFile']
            out = await h.launch(reply, '🎬 Запустить (5 с)')
            assert out.startswith('📥 Оживление фото поставлено в очередь пульта: позиция 1')
            [ref] = h.studio.references                         # existing animate(): crop + import
            [job] = h.studio.created
            assert job['media'] == [{'run_id': 'ref-1', 'role': 'start'}]
            assert job['prompt'] == PHOTO_PROMPT and job['settings']['length'] == '5s'
            [video] = h.videos()
            assert '⏱'.encode() in video and 'очередь пульта'.encode() in video
            [row] = h.speed_rows()
            assert row['ok'] and row['kind'] == 'photo' and row['preset'] == '5s'
            assert isinstance(row['wait_s'], (int, float)) and isinstance(row['gen_s'], (int, float))
    go(scenario())


def test_photo_then_text_and_ten_second_button(tmp_path):
    async def scenario():
        async with PhotoHarness(tmp_path) as h:
            asked = await h.photo('qwen')
            assert 'Фото принято' in asked and h.ollama.chats == []
            reply = await h.say('пусть волосы развеваются на ветру')      # this text is the idea
            assert PHOTO_PROMPT in reply
            assert 'волосы развеваются' in h.ollama.chats[0]['messages'][1]['content']
            await h.launch(reply, '10 с')
            assert h.studio.created[0]['settings']['length'] == '10s'
    go(scenario())


def test_photo_button_command_opens_the_same_flow(tmp_path):
    async def scenario():
        async with PhotoHarness(tmp_path) as h:
            assert 'Фото принято' in await h.say('/vidphoto tg:PHOTO1')
            assert h.app.gen_draft(OWNER)['source'] == 'tg:PHOTO1'
            assert 'Пришлите фото' in await h.say('/vidphoto nothing')
    go(scenario())


def test_plain_photo_caption_is_still_a_vision_question_not_the_qwen_flow():
    assert not {'что', 'оживи', '/animate'} & set(gen_refine.PHOTO_TRIGGERS)


@pytest.mark.parametrize('caption', ['/vid сделай её голой', '/vid раздень её медленно'])
def test_photo_sexualisation_of_a_real_person_is_refused_before_the_model(tmp_path, caption):
    async def scenario():
        async with PhotoHarness(tmp_path) as h:
            out = await h.photo(caption)
            assert out.startswith('⛔ Отказ') and h.ollama.chats == [] and h.studio.created == []
    go(scenario())


def test_guest_photo_command_never_reaches_qwen(tmp_path):
    async def scenario():
        async with PhotoHarness(tmp_path, settings=cfg(image_guests=True)) as h:
            out = await h.say('/vidphoto tg:PHOTO1', person=GUEST)
            assert 'только для владельца' in out and h.ollama.chats == []
    go(scenario())


# ------------------------------------------------------------------ queue
async def release_later(app, delay=0.1):
    await asyncio.sleep(delay)
    app.image_job = None


def test_launch_queues_behind_a_running_generation_and_reports_positions(tmp_path):
    async def scenario():
        async with Harness(tmp_path, FakeOllama(['PROMPT: ' + VIDEO_PROMPT, 'PROMPT: ' + VIDEO_PROMPT + ', dusk'])) as h:
            h.app.image_job = {'id': 1, 'cancel': False, 'who': 'someone'}      # e.g. a /video already rendering
            first = await h.say('/vid лиса')
            out1 = await h.press(first, '🎬 Запустить (5 с)')
            assert 'позиция 2 (впереди 1)' in out1
            second = await h.say('/vid лиса вечером')
            out2 = await h.press(second, '🎬 Запустить (5 с)')
            assert 'позиция 3 (впереди 2)' in out2
            await asyncio.sleep(0.05)
            assert h.studio.created == []                       # still waiting behind the running job
            asyncio.get_running_loop().create_task(release_later(h.app))
            await h.app.gen_queue_join()
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
            reply = await h.say('/vid лиса')
            await h.launch(reply, '🎬 Запустить (5 с)')
            assert len(h.studio.created) == 1
            assert sum('⏳' in t for t in h.sent_texts()) == 1     # one honest waiting notice
    go(scenario())


def test_stop_pressed_while_queued_means_the_job_never_starts(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app.image_job = {'id': 1, 'cancel': False, 'who': 'someone'}
            reply = await h.say('/vid лиса')
            await h.press(reply, '🎬 Запустить (5 с)')
            h.store.put('delegation_locked', True)
            asyncio.get_running_loop().create_task(release_later(h.app, 0.02))
            await h.app.gen_queue_join()
            assert h.studio.created == []
            assert any('СТОП' in t for t in h.sent_texts())
            assert h.app.gen_draft(OWNER) is not None
            assert h.speed_rows()[-1]['error'] == 'STOPPED_IN_QUEUE'
    go(scenario())


def test_cancel_drops_queued_launches(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            h.app.image_job = {'id': 1, 'cancel': False, 'who': 'someone'}
            reply = await h.say('/vid лиса')
            await h.press(reply, '🎬 Запустить (5 с)')
            out = await h.say('/cancel')
            assert 'Из очереди пульта убрано: 1' in out
            h.app.image_job = None
            await h.app.gen_queue_join()
            assert h.studio.created == []
    go(scenario())
