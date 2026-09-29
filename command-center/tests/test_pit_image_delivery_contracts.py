"""Jeff image path contracts the final freeze relies on (agent F, FINAL CONVERGENCE).

* A verified WebP upload reaches Studio as a real PNG reference (1.6 API takes png/jpg).
* Studio output is delivered only when its bytes match the run's sha256 and are an image.
* Photo edit only ever reads the asking participant's own verified photo.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image

from bcc.pit.photo_edit import PhotoEditPipeline
from bcc.pit.photo_pipeline import PhotoStore
from bcc.pit.studio_image_edit import EditedImage, StudioImageEditBroker, StudioImageEditConfig
from bcc.pit.vault import PersonaVault

SALT = b"pit-image-contract-salt-123456"
MODEL = "sdcpp:qwen-image-edit-2509"


def _image(fmt: str, color: str = "red", size=(24, 16)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt)
    return out.getvalue()


PNG_OUT = _image("PNG", "green")


def _world(seen: list, *, output: bytes = PNG_OUT, sha: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/studio/models":
            return httpx.Response(200, json={"items": [{
                "id": MODEL, "available": True, "free": True, "provider": "sdcpp"}]})
        if path == "/api/studio/references":
            body = json.loads(request.content)
            seen.append(("reference", body["filename"], base64.b64decode(body["data_base64"])))
            return httpx.Response(200, json={"id": f"ref-{len(seen)}"})
        if path == "/api/studio/jobs" and method == "POST":
            seen.append(("job", json.loads(request.content)))
            return httpx.Response(200, json={"id": 7})
        if path == "/api/studio/jobs/7":
            return httpx.Response(200, json={"status": "completed"})
        if path == "/api/studio/runs":
            return httpx.Response(200, json={"items": [{
                "id": "out", "sha256": sha or hashlib.sha256(output).hexdigest()}]})
        if path == "/api/studio/runs/out/file":
            return httpx.Response(200, content=output)
        raise AssertionError((method, path))
    return handler


def _broker(handler) -> StudioImageEditBroker:
    return StudioImageEditBroker(
        StudioImageEditConfig(core_url="http://127.0.0.1:8930", core_token="fixture",
                              model_id=MODEL, timeout_seconds=60),
        transport=httpx.MockTransport(handler))


def test_webp_source_is_sent_to_studio_as_a_decodable_png():
    webp = _image("WEBP", "blue")
    seen: list = []

    async def go():
        broker = _broker(_world(seen))
        try:
            return await broker.edit(image_bytes=webp, mime="image/webp", prompt="сделай фон светлее",
                                     filename="telegram-photo.webp")
        finally:
            await broker.close()

    result = asyncio.run(go())
    (_kind, filename, sent), job = seen[0], seen[1]
    assert filename.endswith(".png")
    assert sent.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(sent)) as decoded:
        assert decoded.format == "PNG" and decoded.size == (24, 16)
    assert job[1]["media"] == [{"run_id": "ref-1", "role": "reference"}]
    assert result.mime == "image/png" and result.sha256 == hashlib.sha256(PNG_OUT).hexdigest()


def test_jpeg_and_png_sources_are_sent_unchanged():
    for fmt, mime in (("JPEG", "image/jpeg"), ("PNG", "image/png")):
        raw = _image(fmt)
        seen: list = []

        async def go():
            broker = _broker(_world(seen))
            try:
                await broker.edit(image_bytes=raw, mime=mime, prompt="x", filename="p." + fmt.lower())
            finally:
                await broker.close()

        asyncio.run(go())
        assert seen[0][2] == raw


def test_declared_type_must_match_the_bytes():
    async def go():
        broker = _broker(_world([]))
        try:
            await broker.edit(image_bytes=_image("PNG"), mime="image/webp", prompt="x")
        finally:
            await broker.close()

    with pytest.raises(ValueError, match="unverified source image"):
        asyncio.run(go())


@pytest.mark.parametrize("output,sha", [
    (PNG_OUT, "0" * 64),                                   # bytes differ from the run's sha256
    (b"<html>not an image</html>", None),                  # sha matches, but it is not an image
    (b"GIF89a" + b"\x00" * 64, None),                      # image, but not a deliverable type
])
def test_studio_output_is_rejected_unless_sha_and_type_verify(output, sha):
    async def go():
        broker = _broker(_world([], output=output, sha=sha))
        try:
            await broker.generate(prompt="дом у моря")
        finally:
            await broker.close()

    with pytest.raises(RuntimeError, match="verification failed"):
        asyncio.run(go())


class _RecordingBroker:
    def __init__(self):
        self.calls: list[bytes] = []

    async def edit(self, **kw):
        self.calls.append(kw["image_bytes"])
        return EditedImage(PNG_OUT, "image/png", hashlib.sha256(PNG_OUT).hexdigest(), "run", 1)


def test_photo_edit_never_reads_another_participants_photo(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    store = PhotoStore(vault)
    a, b = vault.key_for_telegram(101), vault.key_for_telegram(202)
    a_photo, b_photo = _image("JPEG", "red"), _image("PNG", "yellow")
    store.ingest(a, 1, a_photo)
    broker = _RecordingBroker()
    pipeline = PhotoEditPipeline(vault, broker=broker, ai_max_ready=True)

    async def go():
        # B has not sent anything: B must not get A's photo edited.
        reply = await pipeline.edit_latest(b, "убери фон")
        assert reply.image is None and "Сначала пришли фото" in reply.text
        assert broker.calls == []
        store.ingest(b, 1, b_photo)
        assert (await pipeline.edit_latest(b, "убери фон")).image is not None
        assert (await pipeline.edit_latest(a, "убери фон")).image is not None

    asyncio.run(go())
    assert broker.calls == [b_photo, a_photo]


def test_forged_latest_manifest_cannot_point_at_another_participant(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    store = PhotoStore(vault)
    a, b = vault.key_for_telegram(101), vault.key_for_telegram(202)
    a_asset = store.ingest(a, 1, _image("JPEG"))
    store.ingest(b, 1, _image("PNG"))
    latest = vault.person_dir(b) / "media" / "latest.json"
    record = json.loads(latest.read_text(encoding="utf-8"))
    record["path"] = "../" + a_asset.path.relative_to(vault.person_dir(a).parent).as_posix()
    latest.write_text(json.dumps(record), encoding="utf-8")
    broker = _RecordingBroker()

    with pytest.raises(ValueError, match="escaped participant root"):
        asyncio.run(PhotoEditPipeline(vault, broker=broker, ai_max_ready=True).edit_latest(b, "x"))
    assert broker.calls == []


def test_photo_replaced_on_disk_is_not_sent(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    store = PhotoStore(vault)
    a = vault.key_for_telegram(101)
    asset = store.ingest(a, 1, _image("JPEG"))
    asset.path.write_bytes(_image("JPEG", "white"))
    broker = _RecordingBroker()

    with pytest.raises(ValueError, match="photo bytes changed"):
        asyncio.run(PhotoEditPipeline(vault, broker=broker, ai_max_ready=True).edit_latest(a, "x"))
    assert broker.calls == []
