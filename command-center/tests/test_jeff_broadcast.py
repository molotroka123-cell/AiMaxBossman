"""Bossman -> Jeff bridge (owner request 2026-10-01: «Разошли через Jeff всем приветики» got «Я не знаю, кто такой Jeff»)."""
from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from bcc.features import tools_jeff
from bcc.pit import broadcast
from bcc.pit import config as pc


def _store(tmp_path, whos):
    home = pc.pit_home(tmp_path)
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "companion.sqlite3")
    db.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, who TEXT, body TEXT, created REAL)")
    db.execute("CREATE TABLE inbox(id INTEGER PRIMARY KEY, who TEXT, body TEXT, lane TEXT, phase TEXT, created REAL)")
    for i, who in enumerate(whos):
        db.execute("INSERT INTO inbox(id,who,body,lane,phase,created) VALUES(?,?,?,?,?,?)", (i, who, "x", "chat", "done", 1.0))
    db.execute("INSERT INTO history(who,body,created) VALUES('555:555','x',1.0)")
    db.execute("INSERT INTO inbox(id,who,body,lane,phase,created) VALUES(99,'777:777','x','control','done',1.0)")  # control lane is not a chat
    db.commit()
    db.close()


def test_the_tool_is_registered_as_a_floor_ask_on_the_send_permission():
    (spec,) = tools_jeff.SPECS
    assert spec.name == "jeff.broadcast" and spec.permission == "channel.send" and spec.default_effect == "ask"
    assert spec.hook_is_floor and spec.idempotent is False and spec.required == ["text"]
    assert tools_jeff.FEATURE.name == "tools_jeff"


def test_effect_hook_always_asks_and_refuses_an_empty_or_huge_text():
    assert tools_jeff._broadcast_effect({"text": "Привет!"})[0] == "ask"
    assert tools_jeff._broadcast_effect({"text": "   "})[0] == "deny"
    assert tools_jeff._broadcast_effect({})[0] == "deny"
    assert tools_jeff._broadcast_effect({"text": "я" * (broadcast.MAX_TEXT + 1)})[0] == "deny"


def test_recipients_are_only_private_chats_that_wrote_to_jeff_and_deduplicated(tmp_path):
    _store(tmp_path, ["111:111", "111:111", "222:222", "333:999", "abc:abc", "-5:-5"])
    # 111/222 (inbox chat) + 555 (history); 333:999 is not a private chat, 777 only wrote on the control lane
    assert broadcast.recipients(tmp_path) == [111, 222, 555]


def test_recipients_of_a_data_dir_without_a_jeff_store_is_empty(tmp_path):
    assert broadcast.recipients(tmp_path) == []


def test_handler_reports_counts_never_ids_and_maps_errors(tmp_path, monkeypatch):
    ctx = SimpleNamespace(svc=SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)))

    async def fake_send(data_dir, text):
        assert data_dir == tmp_path and text == "Привет!"
        return {"sent": 6, "failed": 1, "blocked": 1}

    monkeypatch.setattr(broadcast, "send", fake_send)
    ok = asyncio.run(tools_jeff._t_broadcast({"text": "Привет!"}, ctx))
    assert not ok.error and "доставлено 6" in ok.content and "заблокировано правилом владельца 1" in ok.content

    async def boom(data_dir, text):
        raise ValueError("EMPTY_TEXT")

    monkeypatch.setattr(broadcast, "send", boom)
    refused = asyncio.run(tools_jeff._t_broadcast({"text": ""}, ctx))
    assert refused.error and "EMPTY_TEXT" in refused.content


def test_send_honours_the_block_rule_and_counts_failures(tmp_path, monkeypatch):
    _store(tmp_path, ["111:111", "222:222", "333:333"])
    sent_to, closed = [], []

    class FakeBlock:
        @classmethod
        def from_settings(cls, settings):
            return cls()

        def blocks_person(self, person):
            return person.user_id == 222

    class FakeTelegram:
        def __init__(self, settings):
            self.authorize_delivery = lambda person: True

        async def send(self, person, text):
            if person.user_id == 333:
                raise RuntimeError("telegram refused")
            sent_to.append((person.user_id, text))

        async def close(self):
            closed.append(True)

    settings = SimpleNamespace(people=(SimpleNamespace(user_id=111),), allowlist_open=True)
    monkeypatch.setattr(pc, "load", lambda path: settings)
    monkeypatch.setattr(broadcast, "PrivateBlocklist", FakeBlock)
    import bcc.pit.runtime as runtime
    import bcc.telegram_companion.adapters as adapters
    monkeypatch.setattr(runtime, "_transport_settings", lambda s: s)
    monkeypatch.setattr(adapters, "Telegram", FakeTelegram)
    result = asyncio.run(broadcast.send(tmp_path, "  Привет!  "))
    # recipients: 111, 222 (blocked), 333 (Telegram refuses), 555 (from history)
    assert result == {"sent": 2, "failed": 1, "blocked": 1}
    assert sent_to == [(111, "Привет!"), (555, "Привет!")] and closed == [True]


def test_send_refuses_an_empty_text_before_touching_anything(tmp_path):
    with pytest.raises(ValueError, match="EMPTY_TEXT"):
        asyncio.run(broadcast.send(tmp_path, "  "))
