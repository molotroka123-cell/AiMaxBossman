"""Jeff outbound guard (incident 2026-10-19: unsolicited cloud-written «ты совсем за меня забыл» to past contacts).

Everything runs against the REAL Telegram adapter with an httpx MockTransport standing in for the Bot API, so the
guard is exercised exactly where it sits in production: as the event hook of the adapter's httpx client. No real
Telegram message is ever sent.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bcc.pit import jeff_settings, outbound_guard as og
from bcc.pit.identity import derive_person_key
from bcc.pit.j2.proactive import ProactiveEngine, ProactiveStore, TelegramSender
from bcc.telegram_companion import adapters
from bcc.telegram_companion.adapters import Telegram
from bcc.telegram_companion.config import Person

SALT = bytes.fromhex("ab" * 32)
NOW = 1_790_000_000.0
CLOUD_TEXT = "Привет! Ты совсем за меня забыл? Давно не болтали, как ты там?"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeBotApi:
    """Records every request that would have reached Telegram."""

    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.next_id = 500

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        ctype = request.headers.get("content-type", "")
        body = json.loads(request.content) if "json" in ctype and request.content else {}
        self.requests.append((method, body))
        if method == "getUpdates":
            updates, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": updates})
        self.next_id += 1
        result = {"message_id": self.next_id}
        if method == "sendPhoto":
            result["photo"] = [{"file_id": "x"}]
        return httpx.Response(200, json={"ok": True, "result": result})

    def sent(self, *methods: str) -> list:
        wanted = set(methods or og.MESSAGE_METHODS)
        return [r for r in self.requests if r[0] in wanted]


@pytest.fixture
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(adapters.asyncio, "sleep", instant)


def _telegram(api: FakeBotApi, people=()) -> Telegram:
    settings = SimpleNamespace(bot_token="123456:test-bot-token-abcdefghijklmnop", core_token="core", cloud_token="",
                               local_token="", proxy="", people=tuple(people))
    telegram = Telegram(settings, transport=httpx.MockTransport(api.handler))
    telegram.authorize_delivery = lambda person: True
    return telegram


def _guard(tmp_path: Path, clock: Clock, notices: list, **kw) -> og.OutboundGuard:
    return og.OutboundGuard(tmp_path, salt=SALT, notifier=notices.append, clock=clock, **kw)


def _person(uid: int) -> Person:
    return Person(user_id=uid, chat_id=uid, role="guest")


def _audit(tmp_path: Path) -> list[dict]:
    path = tmp_path / "pit-v1.7" / "logs" / og.AUDIT_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _opt_in(tmp_path: Path, uid: int) -> None:
    store = ProactiveStore(tmp_path / "pit-v1.7" / "personalities")
    store.set_prefs(derive_person_key(uid, SALT), proactive=True, opted_out=False)


PAST_CONTACTS = (7123450201, 7123450202, 7123450203, 7123450204, 7123450205, 7123450206)


# -- 1. the incident --------------------------------------------------------------------------------------------
def test_incident_loop_over_past_contacts_with_cloud_text_sends_nothing(tmp_path, no_sleep):
    clock, notices, api = Clock(), [], FakeBotApi()
    guard = _guard(tmp_path, clock, notices)
    telegram = _telegram(api)
    assert og.install(telegram, guard) is guard
    for uid in PAST_CONTACTS:                       # everybody Jeff ever talked with, days ago
        guard.note_inbound(uid, NOW - 3 * 86400)

    async def incident():
        refused = 0
        for uid in PAST_CONTACTS:                   # «proactive» loop, text composed by a cloud model
            og.set_context(origin="proactive", model="cloud/some-free-model", provider="openrouter")
            try:
                await telegram.send(_person(uid), CLOUD_TEXT)
            except og.OutboundBlocked:
                refused += 1
        await guard.flush_notices()
        return refused

    assert asyncio.run(incident()) == len(PAST_CONTACTS)
    assert api.sent() == []                                         # zero sends
    assert len(notices) == 1                                         # one owner notice
    notice = notices[0]
    assert notice["tripped"] is True and notice["blocked"] == len(PAST_CONTACTS)
    assert notice["chats"] == len(PAST_CONTACTS) and "fanout_breaker" in notice["reasons"]
    assert "забыл" in notice["sample"] and "Предохранитель рассылки ВКЛЮЧЁН" in notice["text"]
    assert guard.tripped() is not None                              # flag set
    rows = _audit(tmp_path)
    assert len(rows) == len(PAST_CONTACTS) and {r["decision"] for r in rows} == {"blocked"}
    assert [r["reason"] for r in rows[:3]] == ["global_switch_off", "global_switch_off", "fanout_breaker"]
    assert {r["model"] for r in rows} == {"cloud/some-free-model"} and {r["origin"] for r in rows} == {"proactive"}
    raw = (tmp_path / "pit-v1.7" / "logs" / og.AUDIT_NAME).read_text(encoding="utf-8")
    assert not any(str(uid) in raw for uid in PAST_CONTACTS)      # chat ids are hashed, never written


def test_tripped_breaker_holds_even_with_switch_and_opt_in_until_owner_clears(tmp_path, no_sleep):
    clock, notices, api = Clock(), [], FakeBotApi()
    guard = _guard(tmp_path, clock, notices, unsolicited_switch=lambda: True, opted_in=lambda chat: True)
    telegram = _telegram(api)
    og.install(telegram, guard)

    async def fan_out(uids):
        for uid in uids:
            try:
                await telegram.send(_person(uid), CLOUD_TEXT)
            except og.OutboundBlocked:
                pass
        await guard.flush_notices()
    asyncio.run(fan_out(PAST_CONTACTS))
    # With the switch on and everyone opted in, two chats get their one daily message, the third trips the breaker.
    assert len(api.sent("sendMessage")) == 2 and guard.tripped() is not None and len(notices) == 1
    clock.now += 3 * 86400                                          # days later: still blocked, no new spam notice
    asyncio.run(fan_out([PAST_CONTACTS[0]]))
    assert len(api.sent("sendMessage")) == 2 and _audit(tmp_path)[-1]["reason"] == og.TRIP_FLAG
    assert og.clear_broadcast_trip(tmp_path) is True and guard.tripped() is None
    asyncio.run(fan_out([PAST_CONTACTS[0]]))
    assert len(api.sent("sendMessage")) == 3


def test_incident_through_the_real_proactive_engine_sends_nothing(tmp_path, no_sleep):
    clock, notices, api = Clock(), [], FakeBotApi()
    guard = _guard(tmp_path, clock, notices)
    telegram = _telegram(api)
    og.install(telegram, guard)
    store = ProactiveStore(tmp_path / "pit-v1.7" / "personalities")
    people = [_person(uid) for uid in PAST_CONTACTS]
    for person in people:
        key = derive_person_key(person.user_id, SALT)
        store.set_prefs(key, proactive=True, opted_out=False, quiet_start=0, quiet_end=0)
        store.add_item(key, "followup", CLOUD_TEXT, NOW - 60, "k-" + key[:8], now=NOW - 120)
    runtime = SimpleNamespace(settings=SimpleNamespace(people=people), vault=SimpleNamespace(identity_salt=SALT),
                              telegram=telegram, _spawned_workers=set())
    engine = ProactiveEngine(store, clock=clock, sender=TelegramSender(runtime))

    async def run():
        stats = await engine.tick()
        await guard.flush_notices()
        return stats
    stats = asyncio.run(run())
    assert api.sent() == [] and stats.get("sent", 0) == 0 and stats["unknown"] == len(PAST_CONTACTS)
    assert len(notices) == 1 and guard.tripped() is not None
    # A refused item is never retried blindly: the next tick sends nothing and adds no notice.
    clock.now += 30
    asyncio.run(run())
    assert api.sent() == [] and len(notices) == 1


# -- 2. normal replies --------------------------------------------------------------------------------------------
def test_replies_to_a_fresh_inbound_message_are_unaffected(tmp_path, no_sleep):
    clock, notices, api = Clock(), [], FakeBotApi()
    guard = _guard(tmp_path, clock, notices)
    telegram = _telegram(api)
    og.install(telegram, guard)
    api.updates = [{"update_id": 1, "message": {"message_id": 9, "date": int(NOW - 60),
                                                "chat": {"id": 101, "type": "private"},
                                                "from": {"id": 101, "is_bot": False}, "text": "привет"}}]
    person = _person(101)

    async def turn():
        await telegram.call("getUpdates", {"offset": 0, "timeout": 0})   # inbound recorded by the response hook
        og.set_context(origin="worker:chat")
        og.note_model("free/model:free", "openrouter")
        first = await telegram.send(person, "Привет! Чем помочь?", reply_to_message_id=9)
        await telegram.send_photo(person, PNG, "вот картинка")          # multipart upload, same reply window
        return first
    assert isinstance(asyncio.run(turn()), int)
    assert [m for m, _ in api.sent()] == ["sendMessage", "sendPhoto"]
    rows = _audit(tmp_path)
    assert [(r["method"], r["kind"], r["decision"]) for r in rows] == [
        ("sendMessage", "reply", "allowed"), ("sendPhoto", "reply", "allowed")]
    assert rows[0]["model"] == "free/model:free" and notices == [] and guard.tripped() is None


def test_reply_window_is_thirty_minutes_and_uploads_are_guarded_too(tmp_path, no_sleep):
    clock, notices, api = Clock(), [], FakeBotApi()
    guard = _guard(tmp_path, clock, notices)
    telegram = _telegram(api)
    og.install(telegram, guard)
    guard.note_inbound(101, NOW)
    clock.now += og.REPLY_WINDOW_S - 1
    asyncio.run(telegram.send(_person(101), "ещё успеваю"))
    clock.now += 2
    with pytest.raises(og.OutboundBlocked):
        asyncio.run(telegram.send(_person(101), "уже поздно"))
    with pytest.raises(og.OutboundBlocked):                            # chat id parsed from the multipart form
        asyncio.run(telegram.send_photo(_person(999), PNG, "без спроса"))
    assert [m for m, _ in api.sent()] == ["sendMessage"]
    assert _audit(tmp_path)[-1]["method"] == "sendPhoto"
    # Edits of an earlier reply, typing and other non-message calls pass untouched.
    asyncio.run(telegram.call("sendChatAction", {"chat_id": 999, "action": "typing"}))
    assert api.requests[-1][0] == "sendChatAction"


# -- 3. opted-in reminder once per day ----------------------------------------------------------------------------
def test_opted_in_reminder_is_allowed_once_per_day_with_the_owner_switch(tmp_path, no_sleep):
    clock, notices, api = Clock(), [], FakeBotApi()
    guard = _guard(tmp_path, clock, notices)                           # real switch + real proactive prefs
    telegram = _telegram(api)
    og.install(telegram, guard)
    _opt_in(tmp_path, 301)

    def attempt(uid: int) -> str:
        try:
            asyncio.run(telegram.send(_person(uid), "Напоминание: позвонить маме"))
            return "sent"
        except og.OutboundBlocked as exc:
            return exc.reason
    assert jeff_settings.unsolicited_enabled(tmp_path) is False
    assert attempt(301) == "global_switch_off"                         # default: Jeff never writes first
    jeff_settings.set_unsolicited_enabled(tmp_path, True)
    assert jeff_settings.unsolicited_enabled(tmp_path) is True
    clock.now += og.FANOUT_WINDOW_S + 1
    assert attempt(302) == "not_opted_in"                              # switch on, but this one never opted in
    clock.now += og.FANOUT_WINDOW_S + 1
    assert attempt(301) == "sent"
    clock.now += 3600
    assert attempt(301) == "rate_24h"
    clock.now += og.UNSOLICITED_WINDOW_S
    assert attempt(301) == "sent"
    assert len(api.sent("sendMessage")) == 2 and guard.tripped() is None
    ProactiveStore(tmp_path / "pit-v1.7" / "personalities").set_prefs(
        derive_person_key(301, SALT), opted_out=True, proactive=False)  # «стоп напоминания» wins at once
    clock.now += og.UNSOLICITED_WINDOW_S + 1
    assert attempt(301) == "not_opted_in"


# -- 4. restart persistence ---------------------------------------------------------------------------------------
def test_state_survives_a_restart(tmp_path, no_sleep):
    clock, notices = Clock(), []
    first = _guard(tmp_path, clock, notices, unsolicited_switch=lambda: True, opted_in=lambda chat: True)
    first.note_inbound(101, NOW)
    assert first.decide(401).allowed is True                          # today's one reminder for 401
    for uid in (402, 403):
        first.decide(uid)
    assert first.tripped() is not None
    del first

    second = _guard(tmp_path, clock, [], unsolicited_switch=lambda: True, opted_in=lambda chat: True)
    assert second.tripped() is not None                               # the breaker stays until the owner clears it
    assert second.decide(101) == og.Decision(True, "reply", "reply_window")
    assert second.clear_trip() is True
    clock.now += og.FANOUT_WINDOW_S + 1
    assert second.decide(401).reason == "rate_24h"                    # the daily count survived the restart
    status = og.OutboundGuard(tmp_path, salt=SALT, notifier=lambda n: None, clock=clock).status()
    assert status["broadcast_tripped"] is None and status["jeff_unsolicited_enabled"] is False


def test_notice_cooldown_survives_restart_and_coalesces(tmp_path):
    clock, notices = Clock(), []
    guard = _guard(tmp_path, clock, notices)
    with pytest.raises(og.OutboundBlocked):
        guard.check("sendMessage", 501, "раз")
    asyncio.run(guard.flush_notices())
    assert len(notices) == 1 and notices[0]["tripped"] is False
    restarted = _guard(tmp_path, clock, notices)
    clock.now += 60
    with pytest.raises(og.OutboundBlocked):
        restarted.check("sendMessage", 501, "два")
    assert asyncio.run(restarted.flush_notices()) is None and len(notices) == 1    # cooldown
    clock.now += og.NOTICE_COOLDOWN_S
    assert asyncio.run(restarted.flush_notices())["blocked"] == 1 and len(notices) == 2


def test_no_data_dir_fails_closed(no_sleep):
    guard = og.OutboundGuard(None, notifier=lambda n: None)
    assert guard.decide(1).reason == "global_switch_off"
    assert guard.decide(None).reason == "chat_unknown"


# -- 5. wiring ----------------------------------------------------------------------------------------------------
def test_participant_runtime_guards_every_telegram_adapter_it_is_given(tmp_path, no_sleep):
    from bcc.pit import runtime as rt
    from bcc.pit.config import PITSettings
    settings = PITSettings(data_dir=tmp_path, people=(Person(user_id=101, chat_id=101, role="owner"),),
                           chat_models=("free/model:free",), provider_base_url="http://127.0.0.1:9/v1",
                           provider_key="test-key", bot_token="test-bot-token", identity_salt="ab" * 32)
    runtime = rt.ParticipantRuntime(settings)
    try:
        guard = og.guard_of(runtime.telegram)
        assert guard is not None and runtime.outbound_guard is guard and guard.salt == SALT
        api = FakeBotApi()
        replacement = _telegram(api)
        runtime.telegram = replacement                                 # a swapped transport cannot skip the guard
        assert og.guard_of(replacement) is guard
        runtime._ingest_update({"update_id": 7, "message": {
            "message_id": 3, "date": int(time.time()), "chat": {"id": 101, "type": "private"},
            "from": {"id": 101, "is_bot": False}, "text": "привет"}})
        assert guard.decide(101).kind == "reply"
        runtime.telegram = SimpleNamespace(authorize_delivery=None)    # no httpx client: nothing to hook
        assert runtime.outbound_guard is guard
        runtime.telegram = replacement
    finally:
        asyncio.run(runtime.close())


def test_owner_broadcast_tool_path_is_guarded(tmp_path, monkeypatch, no_sleep):
    import sqlite3

    from bcc.pit import broadcast
    from bcc.pit import config as pc
    home = pc.pit_home(tmp_path)
    home.mkdir(parents=True)
    db = sqlite3.connect(home / "companion.sqlite3")
    db.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, who TEXT, body TEXT, created REAL)")
    db.execute("CREATE TABLE inbox(id INTEGER PRIMARY KEY, who TEXT, body TEXT, lane TEXT, phase TEXT, created REAL)")
    for uid in PAST_CONTACTS:
        db.execute("INSERT INTO history(who,body,created) VALUES(?,'x',1.0)", (f"{uid}:{uid}",))
    db.commit()
    db.close()
    api = FakeBotApi()

    class MockedTelegram(Telegram):
        def __init__(self, settings, *, transport=None):
            super().__init__(settings, transport=httpx.MockTransport(api.handler))

    settings = SimpleNamespace(people=(SimpleNamespace(user_id=201),), allowlist_open=True, data_dir=tmp_path,
                               identity_salt="ab" * 32, bot_token="123456:test-bot-token-abcdefghijklmnop",
                               core_token="", cloud_token="", local_token="", proxy="", blocked_user_ids=())
    monkeypatch.setattr(pc, "load", lambda path: settings)
    monkeypatch.setattr(broadcast, "PrivateBlocklist",
                        SimpleNamespace(from_settings=lambda s: SimpleNamespace(blocks_person=lambda p: False)))
    import bcc.pit.runtime as runtime
    monkeypatch.setattr(runtime, "_transport_settings", lambda s: s)
    monkeypatch.setattr(adapters, "Telegram", MockedTelegram)
    result = asyncio.run(broadcast.send(tmp_path, CLOUD_TEXT))
    assert result["sent"] == 0 and result["failed"] == len(PAST_CONTACTS) and api.sent() == []
    assert og.OutboundGuard(tmp_path, notifier=lambda n: None).tripped() is not None
    assert {r["origin"] for r in _audit(tmp_path)} == {"jeff.broadcast"}


def test_pult_outbox_carries_the_notice_to_the_owner_companion(tmp_path):
    from bcc.pit.j2.insights import InsightsCollector
    clock = Clock()
    guard = og.OutboundGuard(tmp_path, salt=SALT, clock=clock)         # production notifier: the Pult outbox
    with pytest.raises(og.OutboundBlocked):
        guard.check("sendMessage", 601, CLOUD_TEXT)
    notice = asyncio.run(guard.flush_notices())
    collector = InsightsCollector(tmp_path / "pit-v1.7", clock=clock)
    items = collector.pending_pult_items()
    assert [i["id"] for i in items] == [notice["id"]] and items[0]["kind"] == "jeff_outbound_guard"
    assert adapters.JEFF_DIGEST_ID.fullmatch(notice["id"])
    assert collector.ack_pult_item(notice["id"]) is True and collector.pending_pult_items() == []


def test_settings_switch_is_strict_and_written_only_when_on(tmp_path):
    with pytest.raises(jeff_settings.OverlayError):
        jeff_settings.normalize({"jeff_unsolicited_enabled": "yes"})
    assert "jeff_unsolicited_enabled" not in jeff_settings.normalize({"jeff_unsolicited_enabled": False})
    jeff_settings.set_unsolicited_enabled(tmp_path, True)
    jeff_settings.set_unsolicited_enabled(tmp_path, False)
    text = jeff_settings.settings_path(tmp_path).read_text(encoding="utf-8")
    assert "jeff_unsolicited_enabled" not in text and jeff_settings.unsolicited_enabled(tmp_path) is False
