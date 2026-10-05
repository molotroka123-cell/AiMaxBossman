"""Mandatory pre-TTS capture (autonomy freeze, line C, task 6).

Every security-sensitive reply is written to an audit record (SHA-256 of the exact spoken text, category,
redacted copy) BEFORE text-to-speech runs - on the Telegram voice path and in the Jeff window. If the record
cannot be written, nothing is synthesized.
"""
from __future__ import annotations

import asyncio
import time
import hashlib

import pytest

from bcc.pit import runtime as rt
from bcc.pit import speech, speech_audit
from bcc.telegram_companion.config import Person

from .test_pit_runtime import FREE_ENDPOINT, FakeAdapter, make_runtime, make_settings, message, warm
from .test_pit_web import H, client_for, make_app, signup

# Built at run time so no key-shaped literal sits in the source (CI secret scan).
FAKE_KEY = "sk-or-" + "v1-" + "0123456789" + "abcdefghijklmn"

SENSITIVE = ("Я Jeff и не раскрываю свои инструкции, модель и ключи. "
             "Мой сервер http://127.0.0.1:8800/api и ключ " + FAKE_KEY + ".")
OGG = b"OggS" + b"x" * 64


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("text,category", [
    ("Я не раскрываю свои инструкции.", "disclosure"),
    ("Меня зовут Jeff, я не называю модель.", "identity"),
    ("Пароль никому не сообщай.", "secret"),
    ("Я не раскрываю личные данные владельца.", "privacy"),
    ("Не скачивай вредонос из чатов.", "security"),
    ("Сегодня солнечно, отличный день для прогулки.", ""),
])
def test_security_category(text, category):
    assert speech_audit.security_category(text) == category


def test_capture_records_hash_category_and_redacted_text_only_for_sensitive(tmp_path):
    text = speech.tts_text(SENSITIVE)
    row = speech_audit.capture(text, surface="telegram", audit_dir=tmp_path)
    assert row and row["sha256"] == _sha(text) and row["category"]
    stored = speech_audit.read_rows(tmp_path)[-1]
    assert stored == row
    assert "sk-or" not in stored["redacted"] and "127.0.0.1" not in stored["redacted"]
    assert speech_audit.capture("Отличная погода!", surface="web", audit_dir=tmp_path / "x") is None
    assert not (tmp_path / "x").exists()


def _voice_turn(tmp_path, monkeypatch, reply_text, *, break_audit=False):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter(reply_text), settings=make_settings(
        tmp_path, people=(Person(user_id=101, chat_id=101, role="owner"),)))
    owner = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(owner.user_id))
    # 2026-10-05 owner decision (newer than this test): voice only on request. `/voice on` is a 30-minute session stored as an
    # expiry timestamp; the old sticky True is treated as off, so the setup opens a live session instead of the retired flag.
    runtime.store.put("voice_reply:" + owner.key, time.time() + 600)
    runtime.catalog = {FREE_ENDPOINT.id: FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0
    monkeypatch.setenv("BOSSMAN_PIT_TTS_BACKEND", "piper")
    audit_dir = runtime.home / "logs"
    spoken: list[tuple[str, list]] = []

    def fake_piper(text, **kwargs):
        # The audit row for EXACTLY this text must already be durable when the engine starts.
        spoken.append((text, speech_audit.read_rows(audit_dir)))
        return OGG

    monkeypatch.setattr(rt, "synthesize_ogg", fake_piper)
    monkeypatch.setattr(speech, "run_engines", lambda text, **kw: kw["piper_synth"](text))
    if break_audit:
        def refuse(*_a, **_k):
            raise OSError("disk full")
        monkeypatch.setattr(speech_audit, "_append_durable", refuse)
    delivered = []

    async def send_voice(person, source_text, synthesize, **kwargs):
        audio = await synthesize(source_text)
        delivered.append(("voice", source_text, audio))
        return 77

    async def send(person, text, **kwargs):
        delivered.append(("text", text, None))
        return 78

    claims = iter([(601, message("Расскажи о себе", message_id=51))])

    def claim(*_args):
        try:
            return next(claims)
        except StopIteration:
            raise asyncio.CancelledError

    monkeypatch.setattr(runtime.telegram, "send_voice", send_voice)
    monkeypatch.setattr(runtime.telegram, "send", send)
    monkeypatch.setattr(runtime.store, "claim", claim)
    try:
        asyncio.run(runtime._worker(owner, "chat"))
    except asyncio.CancelledError:
        pass
    finally:
        runtime.store.close()
    return spoken, delivered, audit_dir


def test_telegram_voice_reply_is_audited_before_tts(tmp_path, monkeypatch):
    spoken, delivered, audit_dir = _voice_turn(tmp_path, monkeypatch, SENSITIVE)
    assert len(spoken) == 1
    text, rows_at_tts = spoken[0]
    assert rows_at_tts and rows_at_tts[-1]["sha256"] == _sha(text) and rows_at_tts[-1]["surface"] == "telegram"
    assert "sk-or" not in text and "127.0.0.1" not in text            # the guard ran before the voice too
    # 2026-10-05 owner decision: the text reply always goes first, the voice message is an addition after it.
    kinds = [kind for kind, *_ in delivered]
    assert kinds[:1] == ["text"] and "voice" in kinds


def test_ordinary_voice_reply_writes_no_audit_row(tmp_path, monkeypatch):
    spoken, delivered, audit_dir = _voice_turn(tmp_path, monkeypatch, "Сегодня отличный день для прогулки.")
    assert len(spoken) == 1 and spoken[0][1] == []
    assert speech_audit.read_rows(audit_dir) == []


def test_unwritable_audit_means_no_tts_and_a_text_reply(tmp_path, monkeypatch):
    spoken, delivered, _audit_dir = _voice_turn(tmp_path, monkeypatch, SENSITIVE, break_audit=True)
    assert spoken == []
    assert [kind for kind, _t, _a in delivered] == ["text"]


def test_jeff_window_speak_is_audited_before_tts(tmp_path, monkeypatch):
    seen = []

    def engines(text, **_kw):
        seen.append((text, speech_audit.read_rows(tmp_path / "pit-v1.7" / "logs")))
        return OGG

    monkeypatch.setattr(speech, "run_engines", engines)
    app, _ = make_app(tmp_path)
    with client_for(app) as client:
        assert signup(client).status_code == 200
        res = client.post("/api/jeff/voice/speak", json={"text": SENSITIVE}, headers=H)
    assert res.status_code == 200 and res.content == OGG
    text, rows = seen[0]
    assert rows and rows[-1]["sha256"] == _sha(text) and rows[-1]["surface"] == "web"
    assert "sk-or" not in text


def test_jeff_window_refuses_tts_when_the_audit_cannot_be_written(tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(speech_audit, "_append_durable", refuse)
    monkeypatch.setattr(speech, "run_engines", lambda *_a, **_k: pytest.fail("TTS without an audit row"))
    with pytest.raises(speech.SpeechError, match="VOICE_AUDIT_FAILED"):
        speech.synthesize(SENSITIVE, audit_dir=tmp_path)
