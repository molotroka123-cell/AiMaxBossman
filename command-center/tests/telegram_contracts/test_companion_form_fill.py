from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bcc.telegram_companion.config import Person
from bcc.telegram_companion.form_bridge import FormBridgeMixin, fill_task_prompt, parse_fill_fields

OWNER = Person(11111, 11111, "owner", 7)
GUEST = Person(22222, 22222, "guest", 8)


class FakeStore:
    def __init__(self):
        self.kv = {}
        self.payload = None
        self.delegated_row = None

    def get(self, key, default=None):
        return self.kv.get(key, default)

    def propose(self, who, payload):
        self.payload = payload
        return "abcdef123456"

    def consume(self, who, nonce):
        return dict(self.payload)

    def delegated(self, who, nonce, task_id, identity=None):
        self.delegated_row = (who, nonce, task_id, identity)


class FakeCore:
    def __init__(self):
        self.calls = []

    async def executor(self, person):
        return "f" * 64

    async def delegate(self, person, prompt, expected_fingerprint, *, before_submit=None,
                       client_request_id=None):
        if before_submit:
            before_submit()
        self.calls.append({
            "person": person, "prompt": prompt, "fp": expected_fingerprint,
            "client_request_id": client_request_id,
        })
        return 42, "queued", "a" * 64


class Harness(FormBridgeMixin):
    def __init__(self, person=OWNER):
        self.person = person
        self.store = FakeStore()
        self.core = FakeCore()
        self.screen_calls = 0

    def authorized(self, message):
        return self.person

    async def fresh_screen(self):
        self.screen_calls += 1
        return {"generation": 1, "observed_at": 1.0}


def msg(uid=11111, mid=5):
    return {"_update_id": 99, "message_id": mid, "from": {"id": uid}, "chat": {"id": uid, "type": "private"}}


@pytest.mark.parametrize("raw,expected", [
    ("Имя=Timur; Email=t@example.com", {"Имя": "Timur", "Email": "t@example.com"}),
    ("Город: Praha\nИндекс: 11000", {"Город": "Praha", "Индекс": "11000"}),
    ('{"Имя":"Timur","Город":"Praha"}', {"Имя": "Timur", "Город": "Praha"}),
])
def test_fill_parser_accepts_owner_form_fields(raw, expected):
    assert parse_fill_fields(raw) == expected


@pytest.mark.parametrize("raw", [
    "password=qwerty",
    "OTP=123456",
    "CVV=123",
    "API key=abc",
    "номер карты=4111111111111111",
])
def test_fill_parser_refuses_auth_and_payment_secret_fields(raw):
    with pytest.raises(ValueError, match="SECRET_FIELD"):
        parse_fill_fields(raw)


def test_fill_prompt_explicitly_forbids_submit_and_echo():
    prompt = fill_task_prompt({"Имя": "Timur"})
    assert "Do NOT click Submit, Pay, Buy, Send, Confirm" in prompt
    assert "Do not echo the field values" in prompt
    assert "OWNER_FIELDS_JSON" in prompt


def test_owner_fill_delegates_once_after_fresh_screen_and_is_idempotent():
    h = Harness()
    out = asyncio.run(h.form_command(OWNER, "/fill", "Имя=Timur; Город=Praha", msg()))
    assert h.screen_calls == 1
    assert len(h.core.calls) == 1
    call = h.core.calls[0]
    assert call["client_request_id"].startswith("tgfill:")
    assert "Do NOT click Submit" in call["prompt"]
    assert h.store.delegated_row[2] == 42
    assert "Задача #42" in out


def test_guest_cannot_use_fill():
    h = Harness(person=GUEST)
    out = asyncio.run(h.form_command(GUEST, "/fill", "Имя=Guest", msg(uid=22222)))
    assert "только владельцу" in out
    assert h.core.calls == []


def test_stop_lock_blocks_fill_before_screen_or_task():
    h = Harness()
    h.store.kv["delegation_locked"] = True
    out = asyncio.run(h.form_command(OWNER, "/fill", "Имя=Timur", msg()))
    assert "заблокированы" in out
    assert h.screen_calls == 0
    assert h.core.calls == []
