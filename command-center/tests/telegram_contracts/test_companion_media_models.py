"""/imgmodel (three local image models) and /video (Wan2.2) through a FAKE Studio API."""
from __future__ import annotations

import hashlib

import httpx

from bcc.telegram_companion.service import VIDEO_MODEL, VIDEO_SETTINGS, Reply

from test_companion_image_gen import PNG, FakeStudio, run

MP4 = b'\x00\x00\x00\x18ftypisom' + b'\x01' * 400


class MultiStudio(FakeStudio):
    """Serves every sdcpp model; the run's mime follows the requested surface."""

    def __call__(self, request):
        url = request.url
        if url.path == '/api/studio/models':
            self.requests.append((request.method, url.path))
            return httpx.Response(200, json={'items': [{'id': m, 'available': True} for m in
                                  ('sdcpp:z-image-turbo', 'sdcpp:flux1-schnell', 'sdcpp:sdxl-base', 'sdcpp:flux2-klein-4b', VIDEO_MODEL)]})
        if url.path == '/api/studio/runs':
            self.surface = url.params['surface']
            mime = 'video/mp4' if self.surface == 'video' else 'image/png'
            return httpx.Response(200, json={'items': [{'id': 'run-abc', 'job_id': 7, 'mime': mime,
                                  'sha256': hashlib.sha256(self.file_bytes).hexdigest()}]})
        return super().__call__(request)


def test_imgmodel_menu_lists_every_image_model_and_marks_the_current_one(tmp_path):
    reply, _, _ = run(tmp_path, MultiStudio(), text='/imgmodel')
    assert isinstance(reply, Reply)
    labels = [label for row in reply.keyboard for label, _ in row]
    # Every image model in the catalogue, the video model never among them.
    assert labels == ['✅ Z-Image-Turbo', 'FLUX.1-schnell', 'SDXL 1.0', 'FLUX.2-klein']


def test_chosen_model_is_used_with_its_own_default_steps(tmp_path):
    studio = MultiStudio()
    run(tmp_path / 'a', studio, text='/img кофейня')
    assert studio.created[0]['model'] == 'sdcpp:z-image-turbo'   # default without a choice

    studio2 = MultiStudio()
    run(tmp_path / 'b', studio2, text='/imgmodel sdcpp:flux1-schnell')   # same store dir keeps the choice
    reply, _, tg = run(tmp_path / 'b', studio2, text='/img coffee shop')   # English: no LLM translation call
    job = studio2.created[0]
    assert reply is None and job['model'] == 'sdcpp:flux1-schnell'
    assert 'steps' not in job['settings']            # FLUX's own range (1..8), not the owner's Z-Image steps
    [photo] = tg.photos()
    assert 'FLUX.1-schnell'.encode() in photo and PNG in photo


def test_imgmodel_rejects_unknown_or_video_model(tmp_path):
    reply, _, _ = run(tmp_path, MultiStudio(), text='/imgmodel ' + VIDEO_MODEL)
    assert isinstance(reply, Reply)                  # shows the menu again, choice unchanged
    reply, _, _ = run(tmp_path, MultiStudio(), text='/imgmodel sdcpp:evil')
    assert isinstance(reply, Reply)


def test_video_is_sent_only_as_verified_mp4(tmp_path):
    studio = MultiStudio(file_bytes=MP4)
    reply, history, tg = run(tmp_path, studio, text='/video 5 волны')
    job = studio.created[0]
    assert reply is None and job['model'] == VIDEO_MODEL and studio.surface == 'video'
    assert {k: job['settings'][k] for k in VIDEO_SETTINGS} == VIDEO_SETTINGS and job['settings']['length'] == '5s'
    videos = [c for m, c in tg.calls if m == 'sendVideo']
    assert len(videos) == 1 and MP4 in videos[0]
    assert history[0]['content'] == '[видео] волны'


def test_video_with_non_mp4_bytes_is_never_sent(tmp_path):
    reply, _, tg = run(tmp_path, MultiStudio(file_bytes=PNG), text='/video 5 волны')
    assert reply == 'ERR:IMAGE_BYTES_UNVERIFIED'
    assert not [m for m, _ in tg.calls if m == 'sendVideo']


def test_cyrillic_prompt_is_translated_for_english_only_encoders(tmp_path):
    import asyncio
    from bcc.telegram_companion.config import CompanionError
    from bcc.telegram_companion.service import Companion

    class FakeModels:
        def __init__(self, reply=None, fail=False):
            self.reply, self.fail, self.calls = reply, fail, []

        async def summarize(self, instructions, text):
            self.calls.append(text)
            if self.fail:
                raise CompanionError('FAST_MODEL_UNAVAILABLE')
            return self.reply

    def translate(models, text):
        app = Companion.__new__(Companion)
        app.models = models
        return asyncio.run(app.english_prompt(text))

    ok = FakeModels('"cozy Tokyo coffee shop at night"')
    assert translate(ok, 'кофейня в Токио') == 'cozy Tokyo coffee shop at night'
    english = FakeModels('unused')
    assert translate(english, 'mountain lake') == 'mountain lake' and english.calls == []
    assert translate(FakeModels(fail=True), 'кофейня') == 'кофейня'      # never blocks generation
    assert translate(FakeModels(''), 'кофейня') == 'кофейня'
