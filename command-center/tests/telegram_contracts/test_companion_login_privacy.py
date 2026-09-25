from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bcc.telegram_companion.config import Person
from bcc.telegram_companion.service import Companion

OWNER = Person(11111, 11111, "owner", 7)


class Store:
    def __init__(self):
        self.kv = {}
        self.transients = {}

    def get(self, key, default=None):
        return self.kv.get(key, default)

    def put(self, key, value):
        self.kv[key] = value

    def track_transient(self, who, request_id, message_id):
        self.transients.setdefault((who, request_id), []).append(message_id)

    def pop_transients(self, who, request_id):
        return self.transients.pop((who, request_id), [])


class Telegram:
    def __init__(self):
        self.sent = []
        self.photos = []
        self.deleted = []
        self.authorize_delivery = lambda person: True

    async def send(self, person, text, keyboard=None):
        self.sent.append((person, text))
        return 101

    async def send_photo(self, person, data, caption, keyboard=None):
        self.photos.append((person, data, caption))
        return 102

    async def delete_message(self, person, message_id):
        self.deleted.append((person, message_id))
        return True


class Core:
    def __init__(self):
        self.consumed = []

    async def login_receipts(self):
        return [{
            "id": "abcdef123456",
            "phase": "SUCCESS",
            "login": "owner@example.test",
            "next_fields": ["Company name", "Public nickname"],
        }]

    async def login_receipt_screenshot(self, receipt_id):
        assert receipt_id == "abcdef123456"
        return b"\x89PNG\r\n\x1a\npost-login"

    async def consume_login_receipt(self, receipt_id):
        self.consumed.append(receipt_id)
        return {"id": receipt_id, "phase": "CONSUMED"}


def test_login_receipt_sends_no_password_and_one_post_login_screenshot():
    settings = SimpleNamespace(people=(OWNER,), pc_control=True)
    store, telegram, core = Store(), Telegram(), Core()
    c = Companion(settings, store, telegram, core, models=object(), policy_provider=lambda: settings)
    c.schedule_login_receipt_cleanup = lambda *args, **kwargs: None

    asyncio.run(c.notify_login_receipts())

    assert len(telegram.sent) == 1
    text = telegram.sent[0][1]
    assert "owner@example.test" in text
    assert "Company name" in text
    assert "encrypted vault" in text
    lowered = text.casefold()
    assert "password=" not in lowered and "пароль:" not in lowered
    assert len(telegram.photos) == 1
    assert telegram.photos[0][1].startswith(b"\x89PNG")
    assert core.consumed == ["abcdef123456"]
    assert store.transients[(OWNER.key, "abcdef123456")] == [101, 102]


def test_login_receipt_cleanup_deletes_every_tracked_transient():
    settings = SimpleNamespace(people=(OWNER,), pc_control=True)
    store, telegram, core = Store(), Telegram(), Core()
    c = Companion(settings, store, telegram, core, models=object(), policy_provider=lambda: settings)
    store.transients[(OWNER.key, "abcdef123456")] = [11, 12, 13]

    original_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    async def run():
        import bcc.telegram_companion.service as service
        old = service.asyncio.sleep
        service.asyncio.sleep = fast_sleep
        try:
            await c._cleanup_login_receipt_messages(OWNER, "abcdef123456", 90)
        finally:
            service.asyncio.sleep = old

    asyncio.run(run())
    assert telegram.deleted == [(OWNER, 11), (OWNER, 12), (OWNER, 13)]
    assert store.pop_transients(OWNER.key, "abcdef123456") == []
