"""Jeff speaks and understands English as an OPT-IN second language: voice by script, Whisper language, fixed call phrases.

Without an English Piper model nothing changes (English text is spoken with the Russian voice as before). Each test fails on the code that
had Russian hard-coded (STT language="ru", one model for every text, Russian-only fixed phrases).
"""
from __future__ import annotations

import re

import pytest

from bcc.pit import tts_engines, voice_language as vl
from bcc.telegram_calls.call.session import _DISCLOSES
from bcc.telegram_calls.settings import CallSettings
from bcc.telegram_calls.speech import jeff_engines as je
from bcc.telegram_calls.types import CancelToken

CYR = re.compile(r"[А-Яа-яЁё]")


@pytest.fixture
def en_model(tmp_path, monkeypatch):
    model = tmp_path / "en_US-voice-medium.onnx"
    model.write_bytes(b"x")
    (tmp_path / "en_US-voice-medium.onnx.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(vl.EN_MODEL_ENV, str(model))
    return str(model)


def test_language_is_decided_by_script():
    assert vl.detect_language("Hello, this is Jeff speaking.") == "en"
    assert vl.detect_language("Привет, это Джефф.") == "ru"
    assert vl.detect_language("Привет, это Jeff") == "ru"                  # a Russian sentence with one Latin name stays Russian
    assert vl.detect_language("OK") == "ru" and vl.detect_language("") == "ru" and vl.detect_language("12345") == "ru"


def test_english_voice_needs_the_complete_model_on_disk(tmp_path, monkeypatch):
    model = tmp_path / "en.onnx"
    model.write_bytes(b"x")
    monkeypatch.setenv(vl.EN_MODEL_ENV, str(model))
    assert vl.english_model() == ""                                          # the .onnx.json is missing
    (tmp_path / "en.onnx.json").write_text("{}", encoding="utf-8")
    assert vl.english_model() == str(model)
    monkeypatch.setenv(vl.EN_MODEL_ENV, "relative/en.onnx")
    assert vl.english_model() == ""                                          # only an absolute path the owner set


def test_pick_model_uses_english_only_for_english_text_and_only_when_installed(en_model, monkeypatch):
    assert vl.pick_model("Nice to meet you, how are you?", "ru.onnx") == (en_model, "en")
    assert vl.pick_model("Рад познакомиться, как дела?", "ru.onnx") == ("ru.onnx", "ru")
    monkeypatch.delenv(vl.EN_MODEL_ENV)
    assert vl.pick_model("Nice to meet you, how are you?", "ru.onnx") == ("ru.onnx", "ru")      # not installed: unchanged behaviour


def test_voice_notes_route_through_piper_engine_by_language(en_model, tmp_path, monkeypatch):
    seen = []

    def synth(text, *, piper_executable, model_path, ffmpeg_executable, stopped):
        seen.append(model_path)
        return b"OggS" + b"\x00" * 64

    ru = tmp_path / "ru.onnx"
    ru.write_bytes(b"x")
    exe = tmp_path / "piper.exe"
    exe.write_bytes(b"x")
    monkeypatch.setenv("BOSSMAN_PIT_TTS_EXECUTABLE", str(exe))
    monkeypatch.setenv("BOSSMAN_PIT_TTS_MODEL_PATH", str(ru))
    monkeypatch.setattr(tts_engines.shutil, "which", lambda _n: str(exe))
    engine = tts_engines.PiperEngine(synth)
    engine.synthesize("Hello there, friend.")
    engine.synthesize("Привет, друг.")
    assert seen == [en_model, str(ru)]
    assert engine.status()["languages"] == {"ru": True, "en": True}


async def test_call_tts_speaks_english_sentences_with_the_english_voice(en_model):
    seen = []

    def synth(text, *, piper_executable, model_path, stopped):
        seen.append(model_path)
        return b"\x02\x00" * 2205, 22050

    tts = je.JeffTTS(synthesize_pcm=synth, exe="piper.exe", model="voice/ru.onnx", egress_guard=lambda t: True,
                     stopped=lambda: False, model_en=en_model)
    for text in ("Hello, can you hear me?", "Привет, ты меня слышишь?"):
        async for _ in tts.synthesize(text, CancelToken()):
            pass
    assert seen == [en_model, "voice/ru.onnx"]


async def test_call_stt_passes_the_calls_language_to_whisper():
    seen = []

    def transcribe(wav, *, language, stopped, beam_size):
        seen.append(language)
        return {"text": "hello", "duration_seconds": 1.0, "confidence": 0.9}

    for language in ("en", "auto", "ru"):
        stt = je.JeffSTT(transcribe=transcribe, stopped=lambda: False, language=language)
        s = stt.new_stream()
        s.feed(b"\x01\x00" * 16000)
        await s.finalize()
    assert seen == ["en", "auto", "ru"]
    assert vl.whisper_language("auto") is None and vl.whisper_language("en") == "en"      # 'auto' = let Whisper detect


def test_call_settings_validate_the_language_and_default_to_russian():
    assert CallSettings().language == "ru"
    assert CallSettings(language="en").language == "en"
    with pytest.raises(ValueError):
        CallSettings(language="de")


def test_english_call_phrases_keep_the_ai_disclosure_in_the_callers_language():
    en = vl.call_phrases("en", vl.DEFAULT_RU_GREETING)
    assert all(not CYR.search(v) for v in en.values()), en                    # nothing Russian is spoken to an English speaker
    assert _DISCLOSES.search(en["greeting"]) and _DISCLOSES.search(en["disclosure"])      # session recognises both as the AI disclosure
    ru = vl.call_phrases("ru", vl.DEFAULT_RU_GREETING)
    assert ru["disclosure"] == "Это Джефф, ИИ-ассистент." and ru["greeting"] == vl.DEFAULT_RU_GREETING
    assert vl.call_phrases("en", "Welcome to Bossman!")["greeting"] == "Welcome to Bossman!"      # the owner's own greeting is never replaced
    assert vl.call_phrases("en", "")["greeting"] == ""                                          # an owner-blanked greeting stays blank
