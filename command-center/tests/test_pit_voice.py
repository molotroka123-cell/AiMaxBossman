"""PIT voice transport boundaries; real-model acceptance remains an owner run."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from bcc.pit import voice


class TelegramFile:
    def __init__(self, data=b"OggSfixture"):
        self.data = data
        self.fetches = []

    async def fetch_file(self, file_id, max_bytes):
        self.fetches.append((file_id, max_bytes))
        return self.data


def note(**overrides):
    value = {"file_id": "telegram-file-id", "duration": 2,
             "file_size": 100, "mime_type": "audio/ogg"}
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_voice_fetches_only_bounded_file_and_runs_local_stt_off_loop(monkeypatch):
    telegram = TelegramFile()
    observed = {}

    def transcribe(data):
        observed["data"] = data
        return {"provider": "faster-whisper", "text": "Привет, Джефф.", "cloud_used": False}

    monkeypatch.setattr(voice, "_transcribe", transcribe)
    result = await voice.transcribe_telegram_voice(telegram, note())
    assert telegram.fetches == [("telegram-file-id", voice.MAX_VOICE_BYTES)]
    assert observed["data"] == telegram.data
    assert result["text"] == "Привет, Джефф."
    assert result["cloud_used"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"file_id": ""}, {"duration": -1}, {"duration": 601},
    {"duration": True}, {"file_size": voice.MAX_VOICE_BYTES + 1},
    {"mime_type": "video/mp4"},
])
async def test_invalid_metadata_never_downloads_or_loads_model(monkeypatch, bad):
    telegram = TelegramFile()
    monkeypatch.setattr(voice, "_transcribe", lambda data: pytest.fail("model was reached"))
    with pytest.raises(voice.VoiceError):
        await voice.transcribe_telegram_voice(telegram, note(**bad))
    assert telegram.fetches == []


@pytest.mark.asyncio
async def test_stop_after_fetch_prevents_transcription(monkeypatch):
    active = False

    class StoppingTelegram(TelegramFile):
        async def fetch_file(self, file_id, max_bytes):
            nonlocal active
            active = True
            return await super().fetch_file(file_id, max_bytes)

    telegram = StoppingTelegram()
    monkeypatch.setattr(voice, "_transcribe", lambda data: pytest.fail("model was reached"))
    with pytest.raises(voice.VoiceError, match="VOICE_STOPPED"):
        await voice.transcribe_telegram_voice(telegram, note(), stopped=lambda: active)
    assert len(telegram.fetches) == 1


@pytest.mark.asyncio
async def test_fetch_failure_is_redacted():
    class FailingTelegram:
        async def fetch_file(self, file_id, max_bytes):
            raise RuntimeError("bot token in download URL")

    with pytest.raises(voice.VoiceError) as error:
        await voice.transcribe_telegram_voice(FailingTelegram(), note())
    assert str(error.value) == "VOICE_FETCH_FAILED"
    assert "token" not in str(error.value)


def test_non_ogg_audio_never_reaches_native_decoder(monkeypatch):
    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(
        open=lambda *a, **k: pytest.fail("decoder was reached")))
    with pytest.raises(voice.VoiceError, match="VOICE_FORMAT_UNSUPPORTED"):
        voice._decode_ogg_opus(b"RIFF-not-an-ogg-file")


def test_decoder_failure_redacts_native_error(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("secret local path and participant content")

    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=fail))
    with pytest.raises(voice.VoiceError) as error:
        voice._decode_ogg_opus(b"OggSbad-input")
    assert str(error.value) == "VOICE_DECODE_FAILED"
    assert "participant" not in str(error.value)


def test_whisper_failure_redacts_engine_error(monkeypatch):
    monkeypatch.setattr(voice, "_decode_ogg_opus", lambda data: b"safe WAV")

    def fail(*args, **kwargs):
        raise voice.whisper.WhisperError("private model path")

    monkeypatch.setattr(voice.whisper, "transcribe_audio", fail)
    with pytest.raises(voice.VoiceError) as error:
        voice._transcribe(b"OggSfixture")
    assert str(error.value) == "VOICE_STT_UNAVAILABLE"
    assert "private" not in str(error.value)
