"""Jeff answers by voice only when asked. The text answer is always sent; the voice is an addition; an old sticky flag is off."""
from __future__ import annotations

import asyncio
import time

import pytest

from bcc.pit import runtime as rt
from bcc.pit.models import ConsentState

from .test_pit_runtime import FREE_ENDPOINT, make_runtime, message, warm


@pytest.mark.parametrize("text", [
    "Ответь голосом, пожалуйста", "пришли голосовое сообщение", "озвучь ответ", "скажи вслух что такое кэш",
    "reply with a voice message", "read it aloud"])
def test_an_explicit_request_for_voice_is_recognised(text):
    assert rt.VOICE_REQUEST_RE.search(text), text


@pytest.mark.parametrize("text", [
    "Что такое кэш процессора?", "Расскажи про голосование в парламенте", "Как работает звук в наушниках?",
    "напиши письмо", "Какая погода в Москве?"])
def test_ordinary_messages_do_not_ask_for_voice(text):
    assert not rt.VOICE_REQUEST_RE.search(text), text


def test_a_voice_session_expires_and_the_old_sticky_flag_is_off():
    now = 1_000_000.0
    assert rt.voice_session_active(now + 60, now)
    assert not rt.voice_session_active(now - 1, now), "expired"
    assert not rt.voice_session_active(True, now), "the old sticky True flag must not keep Jeff talking forever"
    assert not rt.voice_session_active(False, now) and not rt.voice_session_active(None, now)
    assert not rt.voice_session_active("yes", now)


def _deliver(tmp_path, monkeypatch, text, *, stored=None, voice_error=None):
    """Run one owner chat turn through the real worker delivery with a fake Telegram; return (texts, voices)."""
    runtime = make_runtime(tmp_path)
    owner = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(owner.user_id)
    warm(runtime, key)
    runtime.vault.set_consent(key, ConsentState(memory_enabled=True, remote_processing_enabled=True))
    if stored is not None:
        runtime.store.put("voice_reply:" + owner.key, stored)
    runtime.catalog = {FREE_ENDPOINT.id: FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0
    texts, voices = [], []

    async def send_text(person, body, **kwargs):
        texts.append(body)
        return 500 + len(texts)

    async def send_voice(person, source_text, _synthesize, **kwargs):
        voices.append(source_text)
        if voice_error is not None:
            raise voice_error
        return 900

    claims = iter([(700, message(text, message_id=21))])

    def claim(*_args):
        try:
            return next(claims)
        except StopIteration:
            raise asyncio.CancelledError

    monkeypatch.setattr(runtime.telegram, "send", send_text)
    monkeypatch.setattr(runtime.telegram, "send_voice", send_voice)
    monkeypatch.setattr(runtime.store, "claim", claim)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._worker(owner, "chat"))
    runtime.store.close()
    return texts, voices


def test_default_is_text_only(tmp_path, monkeypatch):
    texts, voices = _deliver(tmp_path, monkeypatch, "Что такое кэш процессора?")
    assert len(texts) == 1 and voices == []


def test_the_old_sticky_flag_no_longer_makes_jeff_voice_only(tmp_path, monkeypatch):
    texts, voices = _deliver(tmp_path, monkeypatch, "Что такое кэш процессора?", stored=True)
    assert len(texts) == 1 and voices == [], "a stored True from an old build must behave as off"


def test_an_explicit_request_adds_a_voice_and_keeps_the_text(tmp_path, monkeypatch):
    texts, voices = _deliver(tmp_path, monkeypatch, "Ответь голосом: что такое кэш процессора?")
    assert len(texts) == 1 and len(voices) == 1


def test_an_active_voice_session_adds_a_voice_and_an_expired_one_does_not(tmp_path, monkeypatch):
    texts, voices = _deliver(tmp_path, monkeypatch, "Что такое кэш процессора?", stored=time.time() + 600)
    assert len(texts) == 1 and len(voices) == 1
    texts, voices = _deliver(tmp_path / "b", monkeypatch, "Что такое кэш процессора?", stored=time.time() - 5)
    assert len(texts) == 1 and voices == []


def test_a_failed_voice_never_loses_or_repeats_the_text(tmp_path, monkeypatch):
    texts, voices = _deliver(tmp_path, monkeypatch, "Ответь голосом: что такое кэш?",
                             voice_error=rt.PiperError("PIPER_UNAVAILABLE"))
    assert len(texts) == 1 and len(voices) == 1, "text delivered once, voice attempted once, no resend"
    texts, voices = _deliver(tmp_path / "c", monkeypatch, "Ответь голосом: что такое кэш?",
                             voice_error=rt.CompanionError("VOICE_TEXT_BLOCKED"))
    assert len(texts) == 1 and len(voices) == 1
