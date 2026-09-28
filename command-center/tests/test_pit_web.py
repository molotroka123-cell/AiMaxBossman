"""Jeff web transport (bcc.pit.web): the real chat behind the Jeff window.

No network and no Telegram: the model adapter is faked; the runtime's Bot API
client is replaced by a transport that has no Bot API at all.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bcc.pit import runtime as rt
from bcc.pit import web
from bcc.pit.config import PITSettings, config_path, load, save_setup
from bcc.providers import ChatResult
from bcc.telegram_companion.config import CompanionError, Person

from .test_pit_runtime import FakeAdapter, make_settings

PORT = 8850
BASE = f"http://127.0.0.1:{PORT}"
H = {"X-Jeff-Request": "1"}


class RecordingAdapter(FakeAdapter):
    def __init__(self, text="Ответ Jeff.", delay=0.0):
        super().__init__(text)
        self.delay = delay

    async def chat(self, model, messages, **kw):
        self.calls.append((model, messages))
        if self.delay:
            await asyncio.sleep(self.delay)
        return ChatResult(text=self.text, tokens_in=10, tokens_out=5, model=model)


def make_app(tmp_path: Path, adapter=None, settings: PITSettings | None = None):
    settings = settings or make_settings(tmp_path)
    adapter = adapter or RecordingAdapter()

    def factory():
        runtime = web.WebParticipantRuntime(settings, Path(tmp_path) / "pit-v1.7" / "web")
        runtime.adapter = adapter
        return runtime

    return web.create_app(settings, port=PORT, runtime_factory=factory), adapter


def make_runtime_only(tmp_path: Path, settings: PITSettings | None = None):
    settings = settings or make_settings(tmp_path)
    return web.WebParticipantRuntime(settings, Path(tmp_path) / "pit-v1.7" / "web")


def client_for(app) -> TestClient:
    return TestClient(app, base_url=BASE)


def signup(client, name="alice", password="correct horse 1"):
    return client.post("/api/jeff/signup", json={"username": name, "password": password}, headers=H)


def login(client, name, password):
    return client.post("/api/jeff/login", json={"username": name, "password": password}, headers=H)


def chat(client, text, **extra):
    return client.post("/api/jeff/chat", json={"text": text, **extra}, headers=H)


# -- identity / static / guards --------------------------------------------------------------
def test_identity_is_public_and_build_bound(tmp_path):
    app, _ = make_app(tmp_path)
    with client_for(app) as c:
        ident = c.get("/api/jeff/identity").json()
    assert ident["app"] == web.WEB_APP_ID
    assert set(ident) >= {"build_sha", "build_sha_short", "source_identity", "version"}


def test_only_jeff_ui_files_are_served(tmp_path):
    app, _ = make_app(tmp_path)
    with client_for(app) as c:
        assert c.get("/jeff.html").status_code == 200
        assert c.get("/jeff.js").status_code == 200
        for owner_file in ("index.html", "app.js", "api.js", "desktop.js"):
            assert c.get("/" + owner_file).status_code == 404
        assert c.get("/", follow_redirects=False).headers["location"] == "/jeff.html"


def test_host_origin_and_csrf_are_enforced(tmp_path):
    app, _ = make_app(tmp_path)
    with client_for(app) as c:
        assert c.get("/api/jeff/identity", headers={"Host": "evil.example"}).status_code == 421
        assert c.post("/api/jeff/login", json={}).status_code == 403              # no header
        assert c.post("/api/jeff/login", json={}, headers={
            **H, "Origin": "http://evil.example"}).status_code == 403
        assert c.get("/api/jeff/history").status_code == 401


def test_serve_refuses_non_loopback_bind(tmp_path):
    assert web.serve(make_settings(tmp_path), host="0.0.0.0", port=PORT) == 2


# -- accounts --------------------------------------------------------------------------------
def test_first_account_via_ui_then_signup_closes(tmp_path):
    app, _ = make_app(tmp_path)
    with client_for(app) as c:
        assert c.get("/api/jeff/me").json()["signup_open"] is True
        first = signup(c)
        assert first.status_code == 200
        assert first.json()["greeting"] == rt.INTRO_RU
        assert c.get("/api/jeff/me").json()["name"] == "alice"
        assert signup(c, "mallory").status_code == 403
    stored = (tmp_path / "pit-v1.7" / "web" / "accounts.json").read_text(encoding="utf-8")
    assert "correct horse" not in stored


def test_login_rate_limit_and_wrong_password(tmp_path):
    app, _ = make_app(tmp_path)
    with client_for(app) as c:
        signup(c)
        c.post("/api/jeff/logout", headers=H)
        codes = [login(c, "alice", "wrong-password").status_code for _ in range(6)]
        assert codes[:5] == [401] * 5 and codes[5] == 429


# -- chat ------------------------------------------------------------------------------------
def test_chat_reply_history_and_restart(tmp_path):
    adapter = RecordingAdapter("<think>скрытое рассуждение</think>Привет! Чем займёмся?")
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as c:
        signup(c)
        res = chat(c, "Привет, как дела?").json()
        cookie = c.cookies.get(web.SESSION_COOKIE)
    assert res["reply"] == "Привет! Чем займёмся?"
    assert "скрытое" not in json.dumps(res, ensure_ascii=False)
    assert res["disclosure"] and all("free/model" not in d for d in res["disclosure"])
    assert "free/model" not in json.dumps(res, ensure_ascii=False)

    # backend restart: new app + runtime on the same data dir; the session and history survive
    app2, _ = make_app(tmp_path, RecordingAdapter("Снова здесь."))
    with client_for(app2) as c2:
        c2.cookies.set(web.SESSION_COOKIE, cookie)
        history = c2.get("/api/jeff/history").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["text"] == "Привет, как дела?"
    assert history[1]["text"] == "Привет! Чем займёмся?"


def test_context_is_carried_within_a_conversation(tmp_path):
    adapter = RecordingAdapter("Понял.")
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as c:
        signup(c)
        chat(c, "/privacy personalization on")
        chat(c, "Меня зовут Тимур, я люблю горные походы")
        chat(c, "Как меня зовут?")
    last_messages = adapter.calls[-1][1]
    joined = json.dumps(last_messages, ensure_ascii=False)
    assert "Тимур" in joined           # earlier turn reaches the model in the same conversation


def test_memory_isolation_between_web_users(tmp_path):
    adapter = RecordingAdapter("Ок.")
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as a:
        signup(a, "alice")
        chat(a, "/privacy personalization on")
        chat(a, "я люблю секретный-маракуйя-соус")
        facts_a = a.get("/api/jeff/memory").json()["facts"]
    assert any("маракуйя" in str(f["value"]) for f in facts_a)
    web.WebAccounts(tmp_path / "pit-v1.7" / "web").create("bob", "bob-password-1")
    calls_before = len(adapter.calls)
    with client_for(app) as b:
        assert login(b, "bob", "bob-password-1").status_code == 200
        chat(b, "/privacy personalization on")
        chat(b, "что я люблю? маракуйя?")
        memory_b = b.get("/api/jeff/memory").json()
        history_b = b.get("/api/jeff/history").json()["messages"]
    assert all("маракуйя" not in str(f["value"]) for f in memory_b["facts"])
    for _, messages in adapter.calls[calls_before:]:
        system_and_history = [m for m in messages if m["role"] != "user" or "секретный" in m["content"]]
        assert "секретный-маракуйя" not in json.dumps(system_and_history, ensure_ascii=False)
    assert all("секретный-маракуйя" not in m["text"] for m in history_b)


def test_web_identity_namespace_differs_from_telegram(tmp_path):
    settings = make_settings(tmp_path)
    runtime = make_runtime_only(tmp_path, settings)
    from bcc.pit.identity import derive_person_key
    salt = bytes.fromhex(settings.identity_salt)
    assert runtime.vault.key_for_telegram(101) != derive_person_key(101, salt)
    assert runtime.vault.key_for_telegram(101) == web.derive_web_person_key(101, salt)


def test_stop_cancels_an_in_flight_reply(tmp_path):
    adapter = RecordingAdapter("слишком поздно", delay=5.0)
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as c:
        signup(c)
        result = {}

        def run():
            result["res"] = chat(c, "расскажи длинную историю").json()

        worker = threading.Thread(target=run)
        worker.start()
        for _ in range(100):
            if adapter.calls:
                break
            time.sleep(0.05)
        started = time.monotonic()
        assert c.post("/api/jeff/stop", json={}, headers=H).json()["cancelled"] is True
        worker.join(timeout=5)
        assert time.monotonic() - started < 3
        history = c.get("/api/jeff/history").json()["messages"]
    assert result["res"]["stopped"] is True and result["res"]["reply"] == web.STOPPED_RU
    assert all("слишком поздно" not in m["text"] for m in history)


def test_unreachable_model_shows_honest_status(tmp_path):
    settings = make_settings(tmp_path)            # provider at 127.0.0.1:9 (nothing listens)
    app = web.create_app(settings, port=PORT)     # real adapter, dead port
    with client_for(app) as c:
        signup(c)
        res = chat(c, "привет")
    assert res.status_code == 200
    assert res.json()["reply"] in {rt.NO_MODEL_RU, rt.PROVIDER_DOWN_RU}


def test_text_upload_goes_through_document_route_and_bad_types_are_refused(tmp_path):
    adapter = RecordingAdapter("Кратко: список покупок.")
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as c:
        signup(c)
        ok = c.post("/api/jeff/upload", content="молоко\nхлеб\n".encode("utf-8"),
                    headers={**H, "Content-Type": "text/plain",
                             "X-Jeff-Caption": "что это?".encode("utf-8").hex()})
        bad = c.post("/api/jeff/upload", content=b"MZ\x90\x00", headers={
            **H, "Content-Type": "application/x-msdownload"})
    assert ok.status_code == 200 and ok.json()["reply"] == "Кратко: список покупок."
    assert "молоко" in json.dumps(adapter.calls[-1][1], ensure_ascii=False)
    assert bad.status_code == 415


def test_voice_degrades_safely_and_text_chat_still_works(tmp_path, monkeypatch):
    empty = tmp_path / "no-model"
    empty.mkdir()
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", str(empty))
    monkeypatch.setenv("BOSSMAN_PIT_TTS_EXECUTABLE", str(tmp_path / "missing-piper.exe"))
    app, _ = make_app(tmp_path)
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000)
    with client_for(app) as c:
        signup(c)
        health = c.get("/api/jeff/health").json()["voice"]
        asr = c.post("/api/jeff/voice/transcribe", content=buf.getvalue(),
                     headers={**H, "Content-Type": "audio/wav"})
        tts = c.post("/api/jeff/voice/speak", json={"text": "Привет"}, headers=H)
        text = chat(c, "а текстом можно?")
    assert health["asr"]["available"] is False and health["tts"]["available"] is False
    assert asr.status_code == 503 and asr.json()["error"] == "VOICE_STT_UNAVAILABLE"
    assert tts.status_code == 503
    assert text.status_code == 200 and text.json()["reply"] == "Ответ Jeff."


def test_voice_transcript_is_never_a_command(tmp_path):
    app, _ = make_app(tmp_path)
    with client_for(app) as c:
        signup(c)
        res = chat(c, "/delete_me", via="voice").json()
    assert res["reply"] == rt.FORBIDDEN_REPLY_RU


def test_memory_review_correct_delete_and_audit_via_api(tmp_path):
    app, _ = make_app(tmp_path, RecordingAdapter("Ок."))
    with client_for(app) as c:
        signup(c)
        chat(c, "я люблю зелёный чай")
        facts = c.get("/api/jeff/memory").json()["facts"]
        fid = facts[0]["id"]
        assert c.post("/api/jeff/memory/correct", json={"id": fid, "value": "чёрный чай"},
                      headers=H).json()["ok"] is True
        assert c.post("/api/jeff/memory/correct", json={"id": fid, "value": "пароль: qwerty123"},
                      headers=H).status_code == 400
        after = c.get("/api/jeff/memory").json()
        assert after["facts"][0]["value"] == "чёрный чай"
        assert c.post("/api/jeff/memory/delete", json={"id": fid}, headers=H).json()["ok"] is True
        final = c.get("/api/jeff/memory").json()
    assert final["facts"] == []
    actions = [row["action"] for row in final["audit"]]
    assert {"write", "view", "correct", "delete"} <= set(actions)
    assert "чай" not in json.dumps(final["audit"], ensure_ascii=False)


# -- the web window never reaches either Telegram bot ---------------------------------------
def test_web_runtime_has_no_bot_api(tmp_path):
    runtime = make_runtime_only(tmp_path)
    assert isinstance(runtime.telegram, web.WebTransport)
    with pytest.raises(CompanionError, match="WEB_TRANSPORT_HAS_NO_TELEGRAM"):
        asyncio.run(runtime.telegram.call("getUpdates", {}))
    with pytest.raises(CompanionError):
        asyncio.run(runtime.telegram.preflight())
    assert runtime.surface == "web"
    assert Path(runtime.store.path).parent.name == "web"      # its own conversation store


def test_web_only_config_has_no_token_and_cannot_start_a_poller(tmp_path, monkeypatch, capsys):
    from bcc.pit import cli
    path = config_path(tmp_path)
    save_setup(path, people=[Person(user_id=1, chat_id=1, role="owner")], chat_models=["x/y:free"],
               provider_base_url="https://openrouter.ai/api/v1", core_url="http://127.0.0.1:8800",
               web_only=True, provider_key="test-key")
    monkeypatch.setenv("BOSSMAN_PIT_BOT_TOKEN", "123456:" + "A" * 35)
    settings = load(path)
    assert settings.web_only is True and settings.bot_token == ""
    assert cli.cmd_start(path) == 2
    with pytest.raises(CompanionError):
        save_setup(config_path(tmp_path / "other"), people=[Person(user_id=1, chat_id=1, role="owner")],
                   chat_models=["x/y:free"], provider_base_url="https://openrouter.ai/api/v1",
                   core_url="http://127.0.0.1:8800", web_only=True, bot_token="123456:" + "B" * 35)
    with pytest.raises(ValueError):
        dataclasses.replace(make_settings(tmp_path), web_only=True)   # carries a bot token
