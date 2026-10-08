"""Owner key intake (/key NAME=value) in the Telegram «Пульт», owner request 07.10.

The value must land only in the local key file; it never reaches the Store inbox, a reply,
or anyone but the owner. Negative controls: a participant's /key, a malformed /key and a bare
secret-looking message are all refused without writing anything.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bcc.telegram_companion import key_intake, service
from bcc.telegram_companion.secret_intake import SecretIntakeManager

VALUE = "fake-or-value-ZZZZ-0001"  # ci-secret-scan: allow (fake fixture value)
OTHER = "fake-nim-value-YYYY-0002"  # ci-secret-scan: allow (fake fixture value)

OWNER = SimpleNamespace(key="1:1", chat_id=1, role="owner")
GUEST = SimpleNamespace(key="2:2", chat_id=2, role="participant")


class FakeStore:
    def __init__(self):
        self.acked, self.ingested = [], []

    def acknowledge_without_body(self, update_id):
        self.acked.append(update_id)
        return True

    def ingest(self, update_id, who, body):
        self.ingested.append((update_id, who, body))
        return False

    def lane(self, body):
        return "chat"


class FakeTelegram:
    def __init__(self):
        self.sent, self.deleted = [], []

    async def send(self, person, text, **_):
        self.sent.append((person.key, text))
        return 1

    async def delete_message(self, person, message_id):
        self.deleted.append(message_id)
        return True


def companion(person):
    c = object.__new__(service.Companion)
    c.store, c.telegram = FakeStore(), FakeTelegram()
    c.secret_intake = SecretIntakeManager()
    c.wake = {}
    c.authorized = lambda message: person
    return c


def update(text, uid=7, mid=70):
    return {"update_id": uid, "message": {"message_id": mid, "text": text,
                                          "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}}


@pytest.fixture
def keyfile(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "Bossman" / "keys" / "provider-keys.env"


def _all_text(c):
    return " ".join(t for _, t in c.telegram.sent) + repr(c.store.ingested)


def test_owner_key_is_written_locally_deleted_and_never_echoed(keyfile):
    c = companion(OWNER)
    asyncio.run(c.ingest(update(f"/key OPENROUTER_API_KEY={VALUE}")))
    assert keyfile.read_text(encoding="utf-8") == f"OPENROUTER_API_KEY={VALUE}\n"
    assert c.store.ingested == [] and c.store.acked == [7]          # never in the Store inbox
    assert c.telegram.deleted == [70]
    assert "OPENROUTER_API_KEY" in _all_text(c) and VALUE not in _all_text(c)


def test_a_second_key_keeps_the_first_and_a_new_value_is_reported_as_replaced(keyfile):
    keyfile.parent.mkdir(parents=True)
    keyfile.write_text(f"# owner\nNVIDIA_API_KEY={OTHER}\nOPENROUTER_API_KEY=old\n", encoding="utf-8")
    c = companion(OWNER)
    asyncio.run(c.ingest(update(f"/key OPENROUTER_API_KEY={VALUE}")))
    assert key_intake._parse_env(keyfile.read_text(encoding="utf-8")) == {
        "NVIDIA_API_KEY": OTHER, "OPENROUTER_API_KEY": VALUE}
    reply = c.telegram.sent[-1][1]
    assert "заменены: OPENROUTER_API_KEY" in reply and OTHER not in reply and VALUE not in reply


def test_a_participant_key_is_refused_and_nothing_is_written(keyfile):
    c = companion(GUEST)
    asyncio.run(c.ingest(update(f"/key OPENROUTER_API_KEY={VALUE}")))
    assert not keyfile.exists()
    assert c.store.ingested == [] and c.telegram.deleted == [70]
    assert "только владелец" in c.telegram.sent[-1][1] and VALUE not in _all_text(c)


@pytest.mark.parametrize("text", [
    "/key",
    f"/key openrouter={VALUE}",
    "/key OPENROUTER_API_KEY=",
    f"/key OPENROUTER_API_KEY={VALUE} extra",
    f"/key OPENROUTER_API_KEY={VALUE}\nOPENROUTER_API_KEY={VALUE}",
])
def test_a_malformed_key_command_writes_nothing_and_never_echoes_the_value(keyfile, text):
    c = companion(OWNER)
    asyncio.run(c.ingest(update(text)))
    assert not keyfile.exists() and c.store.ingested == []
    assert "не принят" in c.telegram.sent[-1][1] and VALUE not in _all_text(c)


def test_a_bare_secret_without_the_command_still_takes_the_old_refusal_path(keyfile):
    c = companion(OWNER)
    asyncio.run(c.ingest(update(f"api_key={VALUE}")))
    assert not keyfile.exists() and c.store.ingested == []
    assert "без активной secret-сессии" in c.telegram.sent[-1][1]


def test_parse_accepts_several_lines_and_bot_suffix():
    assert key_intake.parse_key_command(f"/key@BossmanBot OPENROUTER_API_KEY={VALUE}\nNVIDIA_API_KEY={OTHER}") == {
        "OPENROUTER_API_KEY": VALUE, "NVIDIA_API_KEY": OTHER}
    assert not key_intake.is_key_command("/keyboard") and not key_intake.is_key_command("hello /key")
