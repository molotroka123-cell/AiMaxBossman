"""Jeff closeout 10.10, gap 1: Telegram photos/documents reach the j2 media module (``ctx.extra["attachment"]``).

Before: the chat route never passed a file to the module, so images sent as files and PDFs got «формат не разбираю»
and «распознай текст» on a photo went to the generic photo answer. Fakes only: fake Telegram download, fake local
vision, fake model adapter (no network, no model).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.pit import jeff_settings as js
from bcc.pit import runtime as rt
from bcc.pit.j2 import media as M
from bcc.telegram_companion.config import CompanionError

from .test_pit_runtime import FakeAdapter, JPEG_BYTES, make_runtime, message, warm

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"0" * 64
EXE_AS_PNG = b"MZ\x90\x00" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(js.ENV_PATH, raising=False)
    monkeypatch.delenv("BOSSMAN_JEFF_J2", raising=False)
    js._cache.clear()
    yield
    js._cache.clear()


class Vision:
    def __init__(self, answer="Счёт №17. Итого: 4 200 руб."):
        self.answer, self.calls = answer, []

    async def __call__(self, data, mime, prompt):
        self.calls.append((mime, prompt, len(data)))
        return self.answer


def setup(tmp_path, monkeypatch, payload, *, vision=None):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("ответ модели"))
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    warm(runtime, key)
    fetched: list[tuple[str, int]] = []

    async def fetch_file(file_id, max_bytes):
        fetched.append((file_id, max_bytes))
        if isinstance(payload, Exception):
            raise payload
        return payload
    monkeypatch.setattr(runtime.telegram, "fetch_file", fetch_file)
    media = next(m for m in runtime.j2.modules if m.name == "media")
    media.desk.vision = vision if vision is not None else Vision()
    return runtime, person, key, fetched, media


def doc(name, *, size=None, mime=None, file_id="doc-1"):
    out = {"file_id": file_id, "file_name": name}
    if size is not None:
        out["file_size"] = size
    if mime is not None:
        out["mime_type"] = mime
    return out


def test_image_sent_as_document_is_read_by_the_media_module_and_nothing_is_stored(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, PNG)
    answer = asyncio.run(runtime.handle(person, message("распознай текст", _document=doc("scan.png", size=len(PNG)),
                                                        message_id=70)))
    assert "Разобрал «scan.png»" in answer and "Итого: 4 200 руб." in answer and "Запомнить" in answer
    assert fetched == [("doc-1", rt.MEDIA_DOCUMENT_LIMITS[".png"])]
    assert runtime.adapter.calls == []                                        # no model guessed at the file
    assert media.desk.vision.calls and media.desk.vision.calls[0][1] == M.EXTRACT_PROMPT
    assert not (runtime.vault.person_dir(key) / "media" / "j2").exists()      # nothing until «да»
    saved = asyncio.run(runtime.handle(person, message("да", message_id=71)))
    assert "Запомнил описание" in saved
    rows = (runtime.vault.person_dir(key) / "media" / "j2" / "notes.jsonl").read_text("utf-8").splitlines()
    assert len(rows) == 1 and "4 200" in json.loads(rows[0])["note"]
    asyncio.run(runtime.close())


def test_paused_memory_means_the_description_is_never_stored(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, PNG)
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=80)))
    answer = asyncio.run(runtime.handle(person, message("", _document=doc("a.png"), message_id=81)))
    assert "Разобрал" in answer
    asyncio.run(runtime.handle(person, message("да", message_id=82)))
    assert not (runtime.vault.person_dir(key) / "media" / "j2" / "notes.jsonl").exists()
    asyncio.run(runtime.close())


def test_oversized_document_is_refused_before_download(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, PNG)
    answer = asyncio.run(runtime.handle(person, message("", _document=doc("big.png", size=11 * 1024 * 1024),
                                                        message_id=90)))
    assert "слишком большой" in answer and "10 МБ" in answer
    assert fetched == []
    asyncio.run(runtime.close())


def test_download_over_the_cap_is_reported_not_crashed(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, CompanionError("IMAGE_TOO_LARGE"))
    answer = asyncio.run(runtime.handle(person, message("", _document=doc("x.pdf"), message_id=91)))
    assert "слишком большой" in answer and "20 МБ" in answer
    asyncio.run(runtime.close())


def test_disguised_binary_is_rejected_by_real_type(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, EXE_AS_PNG)
    answer = asyncio.run(runtime.handle(person, message("", _document=doc("photo.png", mime="image/png"),
                                                        message_id=92)))
    assert "тип файла я не разбираю" in answer
    assert media.desk.vision.calls == [] and runtime.adapter.calls == []
    asyncio.run(runtime.close())


def test_pdf_without_a_renderer_degrades_honestly(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, PDF)
    answer = asyncio.run(runtime.handle(person, message("", _document=doc("contract.pdf"), message_id=93)))
    assert "PDF получил" in answer and "Ничего не сохранено" in answer
    asyncio.run(runtime.close())


def test_owner_switched_media_off_keeps_the_old_reply_and_downloads_nothing(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, PNG)
    js.write_overlay(js.settings_path(runtime.vault.data_dir), {"version": 1, "j2_modules": {"media": False}})
    answer = asyncio.run(runtime.handle(person, message("", _document=doc("scan.png"), message_id=94)))
    assert answer == rt.MEDIA_UNAVAILABLE_RU and fetched == []
    asyncio.run(runtime.close())


def test_photo_with_read_text_caption_goes_to_media_other_captions_to_the_photo_pipeline(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, JPEG_BYTES)
    answer = asyncio.run(runtime.handle(person, message("распознай текст на фото", _photo="ph-1", message_id=95)))
    assert "Разобрал" in answer and "Итого" in answer
    assert media.desk.vision.calls[-1][0] == "image/jpeg"
    calls = len(media.desk.vision.calls)
    other = asyncio.run(runtime.handle(person, message("", _photo="ph-2", message_id=96)))
    assert "Разобрал" not in other and len(media.desk.vision.calls) == calls   # old photo path, unchanged
    asyncio.run(runtime.close())


def test_text_documents_keep_the_model_path(tmp_path, monkeypatch):
    runtime, person, key, fetched, media = setup(tmp_path, monkeypatch, "строка 1\nстрока 2".encode("utf-8"))
    answer = asyncio.run(runtime.handle(person, message("о чём файл?", _document=doc("notes.txt"), message_id=97)))
    assert answer and len(runtime.adapter.calls) == 1 and media.desk.vision.calls == []
    asyncio.run(runtime.close())


def test_minimized_document_keeps_size_and_claimed_type_only():
    raw = {"from": {"id": 1}, "chat": {"id": 1}, "message_id": 5, "caption": "x",
           "document": {"file_id": "f", "file_name": "a.png", "mime_type": "image/png", "file_size": 123,
                        "file_unique_id": "private", "thumbnail": {"file_id": "t"}}}
    assert rt._minimize_message(raw)["_document"] == {"file_id": "f", "file_name": "a.png",
                                                      "mime_type": "image/png", "file_size": 123}
