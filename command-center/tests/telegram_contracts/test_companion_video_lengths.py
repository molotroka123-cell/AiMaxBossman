"""/video with a length (1 s TestRun / 5 / 10 / 15 / 30 s) and photo -> 5/10 s clips, through FAKE APIs."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json

import httpx
from PIL import Image

from bcc.telegram_companion.adapters import Core, Models, Telegram
from bcc.telegram_companion.config import CompanionError
from bcc.telegram_companion.service import VIDEO_MODEL, Companion, Reply, frame_for_video

from test_companion_image_gen import OWNER, cfg, run
from test_companion_media_models import MP4, MultiStudio


def jpeg(w, h):
    out = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 200)).save(out, format="JPEG")
    return out.getvalue()


class AnimStudio(MultiStudio):
    """MultiStudio plus the reference import used for photo -> video."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.references = []

    def __call__(self, request):
        if request.url.path == '/api/studio/references':
            body = json.loads(request.content)
            self.references.append(body)
            return httpx.Response(200, json={'id': 'ref-1', 'surface': 'image'})
        return super().__call__(request)


class TelegramWorld:
    """Fake Telegram: getFile + file download + sends."""

    def __init__(self, photo):
        self.photo, self.calls = photo, []

    def __call__(self, request):
        url = str(request.url)
        self.calls.append((url.rsplit('/', 1)[-1], request.content))
        if url.endswith('/getFile'):
            return httpx.Response(200, json={'ok': True, 'result': {'file_path': 'photos/p.jpg',
                                                                    'file_size': len(self.photo)}})
        if '/file/bot' in url:
            return httpx.Response(200, content=self.photo)
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': len(self.calls)}})


def run_message(tmp_path, studio, message, photo=b''):
    world = TelegramWorld(photo)

    async def go():
        s = cfg()
        from bcc.telegram_companion.store import Store
        store = Store(tmp_path)
        tg = Telegram(s, transport=httpx.MockTransport(world))
        core = Core(s, transport=httpx.MockTransport(studio))
        models = Models(s, tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        app = Companion(s, store, tg, core, models)
        app.image_poll_seconds = 0.01
        app.free_memory_gb = lambda: 64.0
        msg = {'from': {'id': OWNER.user_id, 'is_bot': False}, 'chat': {'id': OWNER.chat_id, 'type': 'private'},
               **message}
        try:
            return await app.handle(OWNER, msg)
        except CompanionError as exc:
            return 'ERR:' + str(exc)
        finally:
            await tg.close(); await core.close(); await models.close(); store.close()
    return asyncio.run(go()), world


def test_video_without_length_offers_every_length_and_starts_nothing(tmp_path):
    studio = MultiStudio(file_bytes=MP4)
    reply, _, _ = run(tmp_path, studio, text='/video биткоин над Прагой')
    assert isinstance(reply, Reply) and studio.created == []
    labels = [label for row in reply.keyboard for label, _ in row]
    assert labels == ['⚡ 1 с TestRun', '5 с', '10 с', '15 с', '30 с']
    assert '1 с TestRun' in reply and '30 с' in reply


def test_each_length_reaches_studio_as_the_preset(tmp_path):
    for n, preset in (('1', 'test_1s'), ('5', '5s'), ('10', '10s'), ('15', '15s'), ('30', '30s')):
        studio = MultiStudio(file_bytes=MP4)
        reply, _, tg = run(tmp_path / n, studio, text=f'/video {n} биткоин над Прагой')
        job = studio.created[0]
        assert reply is None and job['model'] == VIDEO_MODEL and job['settings']['length'] == preset
        assert job['prompt'] == 'биткоин над Прагой' and 'media' not in job
        assert [m for m, _ in tg.calls if m == 'sendVideo']


def test_a_number_alone_is_a_prompt_question_not_a_run(tmp_path):
    studio = MultiStudio(file_bytes=MP4)
    reply, _, _ = run(tmp_path, studio, text='/video 10')
    assert isinstance(reply, Reply) and studio.created == []


def test_frame_for_video_crops_without_stretching():
    data, size = frame_for_video(jpeg(1280, 960))
    assert size == (832, 480) and Image.open(io.BytesIO(data)).size == (832, 480)
    data, size = frame_for_video(jpeg(960, 1280))
    assert size == (640, 1120) and Image.open(io.BytesIO(data)).size == (640, 1120)


def test_photo_with_animate_caption_becomes_an_i2v_job_from_the_imported_frame(tmp_path):
    studio = AnimStudio(file_bytes=MP4)
    photo = jpeg(1280, 960)
    reply, world = run_message(tmp_path, studio, {'_image': {'file_id': 'PHOTO1', 'kind': 'photo'},
                                                  'text': '/animate 10 волны набегают на берег'}, photo)
    assert reply is None
    [ref] = studio.references
    assert Image.open(io.BytesIO(__import__('base64').b64decode(ref['data_base64']))).size == (832, 480)
    [job] = studio.created
    assert job['media'] == [{'run_id': 'ref-1', 'role': 'start'}]
    assert job['settings']['length'] == '10s' and (job['settings']['width'], job['settings']['height']) == (832, 480)
    assert job['prompt'] == 'волны набегают на берег'
    assert [m for m, _ in world.calls if m == 'sendVideo']


def test_russian_trigger_and_default_length_and_prompt(tmp_path):
    studio = AnimStudio(file_bytes=MP4)
    reply, _ = run_message(tmp_path, studio, {'_image': {'file_id': 'P', 'kind': 'photo'}, 'text': 'оживи'},
                           jpeg(900, 1600))
    [job] = studio.created
    assert reply is None and job['settings']['length'] == '5s'
    assert (job['settings']['width'], job['settings']['height']) == (640, 1120)
    assert job['prompt'].startswith('оживи это фото')


def test_animate_button_on_a_studio_run_uses_that_image(tmp_path):
    studio = AnimStudio(file_bytes=MP4)

    class Both(AnimStudio):
        def __call__(self, request):
            if request.url.path == '/api/studio/runs/run-img/file':
                return httpx.Response(200, content=jpeg(1024, 1024))
            return super().__call__(request)
    studio = Both(file_bytes=MP4)
    reply, _ = run_message(tmp_path, studio, {'text': '/animate 5 run:run-img'})
    assert reply is None and studio.references and studio.created[0]['media'][0]['run_id'] == 'ref-1'


def test_animate_without_a_source_explains_how(tmp_path):
    reply, _ = run_message(tmp_path, AnimStudio(), {'text': '/animate 5'})
    assert 'фото' in reply and 'animate' in reply


def test_generated_image_offers_animate_buttons(tmp_path):
    from test_companion_image_gen import FakeStudio
    reply, _, tg = run(tmp_path, FakeStudio(), text='/img кот')
    [photo] = tg.photos()
    for label in ('Оживить 5', 'Оживить 10'):
        assert label.encode() in photo or json.dumps(label)[1:-1].encode() in photo


def test_clip_too_large_for_telegram_is_named_not_sent(tmp_path):
    class Big(MultiStudio):
        def __call__(self, request):
            if request.url.path == '/api/studio/runs':
                return httpx.Response(200, json={'items': [{'id': 'run-abc', 'job_id': 7, 'mime': 'video/mp4',
                                      'file_bytes': 60 * 1024 * 1024,
                                      'sha256': hashlib.sha256(self.file_bytes).hexdigest()}]})
            return super().__call__(request)
    reply, _, tg = run(tmp_path, Big(file_bytes=MP4), text='/video 30 волны')
    assert reply == 'ERR:VIDEO_TOO_LARGE_FOR_TELEGRAM' and not [m for m, _ in tg.calls if m == 'sendVideo']
