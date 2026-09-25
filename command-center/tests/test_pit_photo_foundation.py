from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import httpx
import pytest

from bcc.pit.capabilities import LAPTOP_IMAGE_GENERATION_REPLY_RU
from bcc.pit.models import ConsentState
from bcc.pit.photo_edit import PhotoEditPipeline
from bcc.pit.photo_commands import photo_intent
from bcc.pit.photo_runtime import PhotoRuntimeConfig, photo_runtime_status
from bcc.pit.photo_pipeline import LAPTOP_PHOTO_REPLY_RU, PhotoPipeline, PhotoStore, visual_memory_candidates
from bcc.pit.qwen_vision import QwenVisionBackend, QwenVisionConfig
from bcc.pit.studio_image_edit import StudioImageEditBroker, StudioImageEditConfig
from bcc.pit.vault import PersonaVault

SALT = b"pit-photo-test-salt-123456789"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00JFIF" + b"\x11" * 400
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x22" * 400


def test_photo_store_is_per_participant_and_magic_verified(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    store = PhotoStore(vault)
    a, b = vault.key_for_telegram(1), vault.key_for_telegram(2)
    aa = store.ingest(a, 10, JPEG)
    bb = store.ingest(b, 10, PNG)
    assert aa.path != bb.path
    assert store.read_verified(aa) == JPEG
    assert store.read_verified(bb) == PNG
    assert store.latest(a).sha256 == hashlib.sha256(JPEG).hexdigest()
    with pytest.raises(ValueError):
        store.ingest(a, 11, b"GIF89a-not-supported")


def test_references_are_person_scoped_and_do_not_replace_target(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    store = PhotoStore(vault)
    a, b = vault.key_for_telegram(1), vault.key_for_telegram(2)
    target = store.ingest(a, 10, JPEG)
    store.ingest_reference(a, 11, PNG)
    assert store.latest(a).sha256 == target.sha256
    assert [store.read_verified(ref) for ref in store.references(a)] == [PNG]
    assert store.references(b) == ()
    store.ingest_reference(a, 12, JPEG)
    assert len(store.references(a)) == 2
    store.ingest_reference(a, 13, PNG)
    assert len(store.references(a)) == 2
    with pytest.raises(ValueError):
        store.read_verified(store.references(a)[0].__class__(
            b, "11", store.references(a)[0].path,
            store.references(a)[0].sha256, "image/png", len(PNG)))


def test_laptop_photo_path_is_honest_and_does_not_call_vision(tmp_path):
    class Never:
        async def analyze_fast(self, *a, **k):
            raise AssertionError("vision unavailable on laptop")
        async def analyze_for_memory(self, *a, **k):
            raise AssertionError("vision unavailable on laptop")

    async def go():
        vault = PersonaVault(tmp_path, SALT)
        key = vault.key_for_telegram(3)
        pipe = PhotoPipeline(vault, vision=Never(), ai_max_ready=False)
        reply = await pipe.answer_photo(person_key=key, message_id=1, data=JPEG, prompt="что здесь?")
        assert reply.text == LAPTOP_PHOTO_REPLY_RU
        assert not reply.background_memory_scheduled
    asyncio.run(go())


def test_fast_photo_reply_does_not_wait_for_background_memory(tmp_path):
    class Vision:
        def __init__(self):
            self.release = asyncio.Event()
            self.memory_started = asyncio.Event()
        async def analyze_fast(self, data, mime, prompt):
            return "На фото красная машина."
        async def analyze_for_memory(self, data, mime, caption):
            self.memory_started.set()
            await self.release.wait()
            return {"scene": "красная машина", "memory_hints": ["пользователь делится фотографией автомобиля"]}

    async def go():
        vault = PersonaVault(tmp_path, SALT)
        key = vault.key_for_telegram(4)
        vault.set_consent(key, ConsentState(memory_enabled=True))
        vision = Vision()
        pipe = PhotoPipeline(vault, vision=vision, ai_max_ready=True)
        reply = await pipe.answer_photo(person_key=key, message_id=7, data=JPEG, prompt="что здесь?")
        assert reply.text == "На фото красная машина."
        assert not reply.background_memory_scheduled
        assert reply.background_memory_pending
        assert not vision.memory_started.is_set()
        pipe.schedule_background_after_delivery(reply.asset, caption="что здесь?")
        await asyncio.wait_for(vision.memory_started.wait(), 1)
        assert list(vault.iter_candidate_records(key)) == []
        vision.release.set()
        await pipe.drain_background(timeout=2)
        rows = list(vault.iter_candidate_records(key))
        assert rows and rows[0]["category"] == "visual_context"
    asyncio.run(go())


def test_visual_memory_filter_drops_sensitive_inference():
    rows = visual_memory_candidates(
        analysis={
            "scene": "человек в комнате",
            "memory_hints": [
                "пользователь любит минималистичный интерьер",
                "религиозные взгляды пользователя такие-то",
            ],
        },
        message_id="1",
        source_model="vision",
    )
    dump = json.dumps([r.to_dict() for r in rows], ensure_ascii=False)
    assert "минималистичный" in dump
    assert "религиоз" not in dump


def test_qwen_vision_uses_loopback_data_uri_and_no_tools():
    requests = []
    def world(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "model": "qwen-vl",
                "choices": [{"message": {"content": "На фото кот."}, "finish_reason": "stop"}],
            })
        return httpx.Response(200, json={
            "model": "qwen-vl",
            "choices": [{"message": {"content": json.dumps({
                "scene": "кот на диване", "objects": ["кот", "диван"], "visible_text": [],
                "style": ["домашнее фото"], "memory_hints": ["пользователь делится фото кота"], "uncertain": []
            })}, "finish_reason": "stop"}],
        })

    async def go():
        backend = QwenVisionBackend(
            QwenVisionConfig("http://127.0.0.1:8991/v1", "qwen-vl"),
            transport=httpx.MockTransport(world),
        )
        try:
            assert await backend.analyze_fast(JPEG, "image/jpeg", "Что на фото?") == "На фото кот."
            memory = await backend.analyze_for_memory(JPEG, "image/jpeg", "")
            assert memory["scene"] == "кот на диване"
        finally:
            await backend.close()
    asyncio.run(go())
    content = requests[0]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "tools" not in requests[0]
    assert requests[0]["max_tokens"] == 1024


def test_qwen_vision_rejects_non_loopback():
    with pytest.raises(ValueError):
        QwenVisionConfig("https://example.com/v1", "qwen-vl")


def test_studio_edit_broker_matches_v16_reference_job_run_contract():
    output_sha = hashlib.sha256(PNG).hexdigest()
    seen = []
    polls = {"n": 0}

    def world(request):
        seen.append((request.method, request.url.path))
        path, method = request.url.path, request.method
        if path == "/api/studio/models":
            return httpx.Response(200, json={"items": [{
                "id": "qwen-image-edit:local", "available": True,
                "free": True, "provider": "qwen-image-edit"
            }]})
        if path == "/api/studio/references":
            body = json.loads(request.content)
            assert base64.b64decode(body["data_base64"]) == JPEG
            return httpx.Response(200, json={"id": "input-run"})
        if path == "/api/studio/jobs" and method == "POST":
            body = json.loads(request.content)
            assert body["media"] == [{"run_id": "input-run", "role": "reference"}]
            assert body["model"] == "qwen-image-edit:local"
            return httpx.Response(200, json={"id": 42, "status": "queued"})
        if path == "/api/studio/jobs/42":
            polls["n"] += 1
            return httpx.Response(200, json={"id": 42, "status": "completed"})
        if path == "/api/studio/runs":
            return httpx.Response(200, json={"items": [{
                "id": "out-run", "job_id": 42, "surface": "image", "sha256": output_sha
            }]})
        if path == "/api/studio/runs/out-run/file":
            return httpx.Response(200, content=PNG)
        raise AssertionError((method, path))

    async def go():
        broker = StudioImageEditBroker(
            StudioImageEditConfig(
                core_url="http://127.0.0.1:8800",
                core_token="fixture",
                model_id="qwen-image-edit:local",
                timeout_seconds=60,
            ),
            transport=httpx.MockTransport(world),
        )
        try:
            result = await broker.edit(
                image_bytes=JPEG,
                mime="image/jpeg",
                prompt="сделай фон темнее",
                filename="photo.jpg",
            )
            assert result.data == PNG
            assert result.sha256 == output_sha
        finally:
            await broker.close()
    asyncio.run(go())
    assert ("POST", "/api/studio/references") in seen
    assert ("POST", "/api/studio/jobs") in seen


def test_photo_edit_is_ai_max_only_and_per_user_latest(tmp_path):
    class Broker:
        async def edit(self, **kw):
            assert kw["image_bytes"] == JPEG
            from bcc.pit.studio_image_edit import EditedImage
            return EditedImage(PNG, "image/png", hashlib.sha256(PNG).hexdigest(), "run", 1)

    async def go():
        vault = PersonaVault(tmp_path, SALT)
        key = vault.key_for_telegram(8)
        PhotoStore(vault).ingest(key, 1, JPEG)

        laptop = PhotoEditPipeline(vault, broker=Broker(), ai_max_ready=False)
        answer = await laptop.edit_latest(key, "убери фон")
        assert answer.text == LAPTOP_IMAGE_GENERATION_REPLY_RU and answer.image is None

        ai_max = PhotoEditPipeline(vault, broker=Broker(), ai_max_ready=True)
        answer = await ai_max.edit_latest(key, "убери фон")
        assert answer.image is not None and answer.image.data == PNG
    asyncio.run(go())


def test_photo_intent_supports_simple_followup_edit_commands():
    edit = photo_intent("/photoedit убери фон", has_photo=False)
    assert edit.kind == "edit" and edit.prompt == "убери фон"
    caption = photo_intent("замени фон на ночной город", has_photo=True)
    assert caption.kind == "edit"
    assert photo_intent("что на фото?", has_photo=True).kind == "analyze"


def test_photo_runtime_is_disabled_by_default_and_status_is_secret_free(monkeypatch):
    for name in (
        "BOSSMAN_PIT_AI_MAX_MEDIA", "BOSSMAN_PIT_VISION_MODEL",
        "BOSSMAN_PIT_IMAGE_EDIT_MODEL", "BOSSMAN_PIT_VISION_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = PhotoRuntimeConfig.from_env()
    status = photo_runtime_status(cfg)
    assert cfg.ai_max_media_ready is False
    assert status["vision_configured"] is False
    assert status["image_edit_configured"] is False
    assert "token" not in json.dumps(status).lower()


def test_photo_runtime_ai_max_config(monkeypatch):
    monkeypatch.setenv("BOSSMAN_PIT_AI_MAX_MEDIA", "1")
    monkeypatch.setenv("BOSSMAN_PIT_VISION_MODEL", "qwen2.5-vl")
    monkeypatch.setenv("BOSSMAN_PIT_IMAGE_EDIT_MODEL", "qwen-image-edit:local")
    cfg = PhotoRuntimeConfig.from_env()
    status = photo_runtime_status(cfg)
    assert status["ai_max_media_ready"] is True
    assert status["vision_configured"] is True
    assert status["image_edit_configured"] is True
