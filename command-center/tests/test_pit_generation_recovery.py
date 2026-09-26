"""Restart reconciliation for one PIT Studio image and Telegram delivery."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib

import pytest

from bcc.pit.config import PITSettings
from bcc.pit.models import ConsentState
from bcc.pit.runtime import ParticipantRuntime
from bcc.pit.studio_image_edit import EditedImage
from bcc.telegram_companion.config import Person


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image-bytes"
PERSON = Person(user_id=101, chat_id=101, role="owner")
PROMPT = "нарисуй жёлтый дом"


def runtime_at(tmp_path):
    return ParticipantRuntime(PITSettings(
        data_dir=tmp_path, people=(PERSON,), chat_models=("free/model:free",),
        provider_base_url="http://127.0.0.1:9/v1", provider_key="test-key",
        bot_token="test-token", identity_salt="ab" * 32,
    ))


def interrupted(tmp_path, *, phase: str, update_id: int = 201, job_id: int = 42):
    old = runtime_at(tmp_path)
    body = {"_user_id": 101, "_chat_id": 101, "_message_id": 7,
            "text": PROMPT, "_photo": "", "_document": None, "_voice": False}
    assert old.store.ingest(update_id, PERSON.key, body)
    assert old.store.claim(PERSON.key, "chat")[0] == update_id
    old.store.begin_generation(update_id, PERSON.key)
    if phase != "requesting":
        old.store.record_generation_job(update_id, job_id)
    if phase == "sending":
        old.store.begin_generation_delivery(update_id, PERSON.key, job_id)
    old.store.close()
    return body


class Broker:
    def __init__(self):
        self.resumed = []
        self.created = []

    async def resume(self, job_id):
        self.resumed.append(job_id)
        return EditedImage(PNG, "image/png", hashlib.sha256(PNG).hexdigest(),
                           "verified-run", job_id)

    async def generate(self, *, prompt, on_job_created=None):
        self.created.append(prompt)
        assert on_job_created is not None
        on_job_created(43)
        return await self.resume(43)

    async def close(self):
        pass


def enable_generation(runtime, broker):
    runtime.photo_services.generate = broker
    runtime.photo_services.config = dataclasses.replace(
        runtime.photo_services.config, ai_max_media_ready=True,
        image_license_mode="commercial_licensed")


def test_restart_resumes_exact_studio_job_and_sends_once(tmp_path):
    interrupted(tmp_path, phase="studio_submitted")
    current = runtime_at(tmp_path)
    broker = Broker()
    enable_generation(current, broker)
    sent = []

    async def send_photo(person, data, caption):
        sent.append((person.key, data))
        return 501

    current.telegram.send_photo = send_photo
    current.store.recover()
    asyncio.run(current._reconcile_generations())
    asyncio.run(current._reconcile_generations())
    row = current.store.db.execute("SELECT phase FROM inbox WHERE id=201").fetchone()
    media = current.store.db.execute(
        "SELECT phase,job_id,message_id FROM media_jobs WHERE update_id=201").fetchone()
    assert broker.resumed == [42] and broker.created == []
    assert sent == [(PERSON.key, PNG)]
    assert row["phase"] == "done"
    assert tuple(media) == ("sent", 42, 501)
    current.store.close()


def test_restart_before_job_id_records_one_new_dispatch(tmp_path):
    interrupted(tmp_path, phase="requesting", update_id=202)
    current = runtime_at(tmp_path)
    broker = Broker()
    enable_generation(current, broker)
    async def free_capacity():
        return True
    current.capacity_guard.local_allowed = free_capacity
    sent = []

    async def send_photo(person, data, caption):
        sent.append(person.key)
        return 502

    current.telegram.send_photo = send_photo
    current.store.recover()
    asyncio.run(current._reconcile_generations())
    assert broker.created == [PROMPT] and broker.resumed == [43]
    assert sent == [PERSON.key]
    media = current.store.db.execute(
        "SELECT phase,job_id,message_id FROM media_jobs WHERE update_id=202").fetchone()
    assert tuple(media) == ("sent", 43, 502)
    current.store.close()


def test_restart_after_send_started_never_uploads_again(tmp_path):
    interrupted(tmp_path, phase="sending", update_id=203)
    current = runtime_at(tmp_path)
    current.store.recover()

    async def forbidden_send(*args, **kwargs):
        raise AssertionError("Telegram upload was already attempted")

    current.telegram.send_photo = forbidden_send
    asyncio.run(current._reconcile_generations())
    assert current.store.db.execute(
        "SELECT phase FROM inbox WHERE id=203").fetchone()[0] == "delivery_unknown"
    assert current.store.db.execute(
        "SELECT phase FROM media_jobs WHERE update_id=203").fetchone()[0] == "delivery_unknown"
    current.store.close()


def test_crash_after_telegram_receipt_before_local_commit_is_not_resent(tmp_path):
    interrupted(tmp_path, phase="studio_submitted", update_id=204)
    current = runtime_at(tmp_path)
    sent = []

    async def send_photo(person, data, caption):
        sent.append(person.key)
        return 504

    current.telegram.send_photo = send_photo

    def failed_commit(*args):
        raise OSError("simulated local disk failure")

    current.store.generation_delivered = failed_commit
    output = EditedImage(PNG, "image/png", hashlib.sha256(PNG).hexdigest(),
                         "verified-run", 42)
    assert asyncio.run(current._deliver_generated_image(
        PERSON, output, update_id=204)) == ""
    assert sent == [PERSON.key]
    current.store.close()

    restarted = runtime_at(tmp_path)
    restarted.store.recover()
    restarted.telegram.send_photo = send_photo
    asyncio.run(restarted._reconcile_generations())
    assert sent == [PERSON.key]
    assert restarted.store.db.execute(
        "SELECT phase FROM inbox WHERE id=204").fetchone()[0] == "delivery_unknown"
    restarted.store.close()


def test_worker_does_not_mark_photo_done_when_receipt_commit_fails(tmp_path):
    current = runtime_at(tmp_path)
    current.vault.set_consent(
        current.vault.key_for_telegram(101),
        ConsentState(memory_enabled=True, remote_processing_enabled=True))
    broker = Broker()
    enable_generation(current, broker)
    async def free_capacity():
        return True
    current.capacity_guard.local_allowed = free_capacity
    body = {"_user_id": 101, "_chat_id": 101, "_message_id": 8,
            "text": PROMPT, "_photo": "", "_document": None, "_voice": False}
    assert current.store.ingest(205, PERSON.key, body)
    original_claim = current.store.claim
    claims = {"n": 0}

    def one_claim(*args):
        claims["n"] += 1
        if claims["n"] > 1:
            raise asyncio.CancelledError
        return original_claim(*args)

    current.store.claim = one_claim
    sent = []

    async def send_photo(person, data, caption):
        sent.append(person.key)
        return 505

    current.telegram.send_photo = send_photo

    def failed_commit(*args):
        raise OSError("simulated local disk failure")

    current.store.generation_delivered = failed_commit
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(current._worker(PERSON, "chat"))
    assert sent == [PERSON.key]
    assert current.store.db.execute(
        "SELECT phase FROM inbox WHERE id=205").fetchone()[0] == "delivery_unknown"
    assert current.store.db.execute(
        "SELECT phase FROM media_jobs WHERE update_id=205").fetchone()[0] == "delivery_unknown"
    current.store.close()
