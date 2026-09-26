"""Owner STOP must see Jeff's separate process and stop an in-flight media turn."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bcc.pit.config import PITSettings
from bcc.pit.runtime import ParticipantRuntime, STOP_FLAG
from bcc.pit.models import ConsentState
from bcc.telegram_companion.config import Person


class HeldBroker:
    def __init__(self):
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, *, prompt):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return SimpleNamespace(data=b"image")


class HeldTelegram:
    def __init__(self):
        self.poll = asyncio.Event()
        self.photos = []
        self.messages = []

    async def preflight(self):
        return None

    async def call(self, method, payload):
        await self.poll.wait()
        return []

    async def send_photo(self, person, data, caption):
        self.photos.append(data)

    async def send(self, person, text, **kwargs):
        self.messages.append(text)

    async def close(self):
        return None


async def test_pit_stop_flag_cancels_held_image_before_telegram_delivery(tmp_path):
    person = Person(user_id=101, chat_id=101, role="owner")
    settings = PITSettings(
        data_dir=tmp_path, people=(person,), chat_models=("free/model:free",),
        provider_base_url="http://127.0.0.1:9/v1", provider_key="test",
        bot_token="test", identity_salt="ab" * 32)
    runtime = ParticipantRuntime(settings)
    broker, telegram = HeldBroker(), HeldTelegram()
    runtime.telegram = telegram
    runtime.photo_services.edit = broker
    runtime.photo_services.config = SimpleNamespace(
        ai_max_media_ready=True, image_use_allowed=lambda **_: True)

    class Capacity:
        def reset(self): pass
        async def local_allowed(self): return True
    runtime.capacity_guard = Capacity()

    async def no_catalog():
        return None
    runtime.refresh_catalog_safe = no_catalog
    key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(key, ConsentState(memory_enabled=True, remote_processing_enabled=True))
    runtime.store.ingest(1, person.key, {
        "_user_id": person.user_id, "_chat_id": person.chat_id, "_message_id": 1,
        "text": "Нарисуй кота", "_photo": "", "_document": None, "_voice": False})
    running = asyncio.create_task(runtime.run())
    try:
        await asyncio.wait_for(broker.entered.wait(), timeout=3)
        (runtime.home / STOP_FLAG).write_text("owner STOP", encoding="utf-8")
        await asyncio.wait_for(asyncio.shield(running), timeout=3)
        assert broker.cancelled.is_set()
        assert telegram.photos == []
        assert telegram.messages == []
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await runtime.close()


async def test_owner_stop_does_not_certify_while_pit_poller_is_still_live(env, monkeypatch):
    from bcc.pit import cli as pit_cli

    home = env.settings.data_dir / "pit-v1.7"
    home.mkdir(parents=True, exist_ok=True)
    (home / "poller.lock").touch()
    monkeypatch.setattr(pit_cli, "_is_running", lambda home: True)
    result = (await env.client.post("/api/control-plane/stop-all")).json()
    assert result["ok"] is False
    assert result["requested"]["pit"] == ["Jeff"]
    assert result["remaining"]["pit"] == ["Jeff"]
    assert (env.settings.data_dir / "pit-v1.7" / STOP_FLAG).is_file()
