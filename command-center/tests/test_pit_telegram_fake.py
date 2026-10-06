"""Jeff Telegram bot end-to-end against a FAKE Bot API (async httpx transport).

No real Telegram: getMe/getWebhookInfo/getUpdates/sendMessage/getFile are
answered in-process. Covers receive -> reply -> STOP, the Jeff/«Пульт» token
separation, the per-token single-poller guard and voice degradation.
"""
from __future__ import annotations

import asyncio
import io
import json
import wave

import httpx
import pytest

from bcc.pit import bot_guard
from bcc.pit import runtime as rt
from bcc.pit import voice
from bcc.telegram_companion.adapters import Telegram
from bcc.telegram_companion.config import CompanionError

from .test_pit_runtime import FakeAdapter, make_settings

FAKE_TOKEN = "123456789:" + "J" * 35


class FakeBotAPI(httpx.AsyncBaseTransport):
    """Async like the real network: an empty getUpdates yields to the loop
    (a real long poll never returns instantly; a sync mock would spin)."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates") and not self.pending:
            await asyncio.sleep(0.05)
        await request.aread()
        return self(request)

    def __init__(self, updates):
        self.pending = list(updates)
        self.sent: list[dict] = []
        self.methods: list[str] = []
        self.on_send = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/file/bot" in path:
            return httpx.Response(200, content=b"OggS" + b"\x00" * 64)
        method = path.rsplit("/", 1)[-1]
        self.methods.append(method)
        body = json.loads(request.content or b"{}")
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"is_bot": True, "username": "jeff_fake_bot"}})
        if method == "getWebhookInfo":
            return httpx.Response(200, json={"ok": True, "result": {"url": ""}})
        if method == "getUpdates":
            batch, self.pending = self.pending[:1], self.pending[1:]
            return httpx.Response(200, json={"ok": True, "result": batch})
        if method == "sendMessage":
            self.sent.append(body)
            if self.on_send:
                self.on_send(self)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1000 + len(self.sent)}})
        if method == "getFile":
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/a.oga", "file_size": 68}})
        if method == "sendChatAction":
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": False, "description": "unsupported in fake"})


def update(update_id: int, text: str = "", **extra) -> dict:
    message = {"message_id": update_id, "from": {"id": 101, "is_bot": False},
               "chat": {"id": 101, "type": "private"}, **extra}
    if text:
        message["text"] = text
    return {"update_id": update_id, "message": message}


def fake_runtime(tmp_path, api: FakeBotAPI, adapter=None) -> rt.ParticipantRuntime:
    settings = make_settings(tmp_path)
    runtime = rt.ParticipantRuntime(settings)
    runtime.telegram = Telegram(rt._transport_settings(settings), transport=api)
    runtime.adapter = adapter or FakeAdapter("Всё хорошо, спасибо!")
    return runtime


def run_until(runtime, api: FakeBotAPI, replies: int, timeout: float = 20.0):
    def stop_when_done(bot):
        if len(bot.sent) >= replies:
            (runtime.home / rt.STOP_FLAG).write_text("test", encoding="utf-8")
    api.on_send = stop_when_done

    async def go():
        await asyncio.wait_for(runtime.run(), timeout=timeout)
    asyncio.run(go())


def test_receive_reply_then_stop_through_fake_bot_api(tmp_path):
    api = FakeBotAPI([update(1, "/start")])
    runtime = fake_runtime(tmp_path, api)
    run_until(runtime, api, replies=1)
    assert [row["text"] for row in api.sent] == [rt.INTRO_RU]
    # commands (control lane) and chat (chat lane) are separate workers, so the
    # chat turn is a second poll session after the zero-start intro
    api.pending, api.sent = [update(2, "Как дела?")], []
    runtime = fake_runtime(tmp_path, api)
    run_until(runtime, api, replies=1)
    texts = [row["text"] for row in api.sent]
    assert "Всё хорошо" in texts[0]
    assert all(row["chat_id"] == 101 for row in api.sent)
    assert "getUpdates" in api.methods
    assert not (runtime.home / rt.STOP_FLAG).exists()      # STOP consumed, loop ended


def test_owner_control_commands_are_refused_on_jeff_bot(tmp_path):
    api = FakeBotAPI([update(1, "/start"), update(2, "/approve 42"), update(3, "/stop")])
    runtime = fake_runtime(tmp_path, api)
    run_until(runtime, api, replies=3)
    assert [row["text"] for row in api.sent][1:] == [rt.FORBIDDEN_REPLY_RU, rt.FORBIDDEN_REPLY_RU]


def test_voice_note_without_asr_model_degrades_to_text(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x01\x00" * 8000)
    monkeypatch.setattr(voice, "_decode_ogg_opus", lambda audio: buf.getvalue())
    empty = tmp_path / "no-whisper-model"
    empty.mkdir()
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", str(empty))
    note = {"file_id": "voice-1", "duration": 1, "mime_type": "audio/ogg", "file_size": 68}
    api = FakeBotAPI([update(1, "/start"), update(2, voice=note)])
    runtime = fake_runtime(tmp_path, api)
    run_until(runtime, api, replies=2)
    # /start (control lane) and the voice note (chat lane) run on separate workers, so the
    # two replies may arrive in either order (py3.14 CI sent the voice reply first).
    assert sorted(row["text"] for row in api.sent) == sorted(
        [rt.INTRO_RU, rt._failure_text("VOICE_STT_UNAVAILABLE")])
    assert "getFile" in api.methods


# -- single poller per token / two bots never share a token ----------------------------------
def test_second_poller_for_the_same_token_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_TELEGRAM_POLLER_LOCK_DIR", str(tmp_path / "locks"))
    with bot_guard.token_poller_lock(FAKE_TOKEN):
        with pytest.raises(CompanionError, match="ANOTHER_POLLER_FOR_THIS_BOT_TOKEN"):
            with bot_guard.token_poller_lock(FAKE_TOKEN):
                pass
        with bot_guard.token_poller_lock("987654321:" + "K" * 35):   # another bot is fine
            pass
    with bot_guard.token_poller_lock(FAKE_TOKEN):                    # released after exit
        pass
    assert all(FAKE_TOKEN not in p.name for p in (tmp_path / "locks").iterdir())


def test_pit_start_refuses_second_poller_from_another_data_dir(tmp_path, monkeypatch, capsys):
    from bcc.pit import cli
    from bcc.pit.config import config_path, save_setup
    from bcc.telegram_companion.config import Person
    monkeypatch.setenv("BOSSMAN_TELEGRAM_POLLER_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("BOSSMAN_COMPANION_CONFIG", str(tmp_path / "no-companion" / "config.json"))
    second = tmp_path / "second-data-dir"
    save_setup(config_path(second), people=[Person(user_id=101, chat_id=101, role="owner")],
               chat_models=["x/y:free"], provider_base_url="https://openrouter.ai/api/v1",
               core_url="http://127.0.0.1:8800", bot_token=FAKE_TOKEN, provider_key="k")
    with bot_guard.token_poller_lock(FAKE_TOKEN):          # "first PIT" holds the token
        assert cli.cmd_start(config_path(second)) == 3
    assert "ANOTHER_POLLER_FOR_THIS_BOT_TOKEN" in capsys.readouterr().err


def test_jeff_refuses_the_owner_companion_bot_token(tmp_path, monkeypatch, capsys):
    from bcc.pit import cli
    from bcc.pit.config import config_path, save_setup
    from bcc.telegram_companion.config import Person
    monkeypatch.setenv("BOSSMAN_TELEGRAM_POLLER_LOCK_DIR", str(tmp_path / "locks"))
    companion = tmp_path / "companion"
    companion.mkdir()
    (companion / "config.json").write_text(json.dumps({
        "people": [{"user_id": 5, "chat_id": 5, "role": "owner"}],
        "local_url": "http://127.0.0.1:8080/v1", "local_model": "m"}), encoding="utf-8")
    (companion / "companion.env").write_text(f"TG_COMPANION_BOT_TOKEN={FAKE_TOKEN}\n", encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_COMPANION_CONFIG", str(companion / "config.json"))
    assert bot_guard.companion_token_fingerprint() == bot_guard.token_fingerprint(FAKE_TOKEN)
    data = tmp_path / "jeff"
    save_setup(config_path(data), people=[Person(user_id=101, chat_id=101, role="owner")],
               chat_models=["x/y:free"], provider_base_url="https://openrouter.ai/api/v1",
               core_url="http://127.0.0.1:8800", bot_token=FAKE_TOKEN, provider_key="k")
    assert cli.cmd_start(config_path(data)) == 3
    err = capsys.readouterr().err
    assert "PIT_TOKEN_IS_THE_OWNER_COMPANION_BOT" in err and FAKE_TOKEN not in err
    # a different Jeff token passes the separation check
    bot_guard.assert_not_companion_bot("111111111:" + "Z" * 35)


def test_jeff_and_companion_never_share_state_dirs(tmp_path, monkeypatch):
    from bcc.pit.config import pit_home
    from bcc.telegram_companion.__main__ import default_config
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CONFIG", raising=False)
    monkeypatch.delenv("BOSSMAN_COMPANION_CONFIG", raising=False)
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path))
    jeff_home = pit_home(tmp_path)
    assert jeff_home.name == "pit-v1.7"
    assert default_config().parent.name == "telegram-companion"
    assert jeff_home.resolve() != default_config().parent.resolve()
