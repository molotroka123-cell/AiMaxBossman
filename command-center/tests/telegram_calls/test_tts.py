"""PiperTTS with a fake voice (no piper, no ONNX, no network). Covers both Piper API generations by duck typing."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from bcc.telegram_calls.speech.tts import PiperTTS, iter_pcm, resolve_voice, validate_voice
from bcc.telegram_calls.types import CallError, CancelToken, TTSEngine

RATE = 22050


def voice_files(tmp_path, name="ru_RU-irina-medium", rate=RATE, lang="ru_RU"):
    onnx = tmp_path / (name + ".onnx")
    onnx.write_bytes(b"onnx")
    (tmp_path / (name + ".onnx.json")).write_text(
        json.dumps({"audio": {"sample_rate": rate}, "language": {"code": lang}}), encoding="utf-8")
    return onnx


def sentence(ms, v=1000):
    n = RATE * ms // 1000
    return (v.to_bytes(2, "little", signed=True)) * n


class NewApiVoice:
    """piper-tts 1.3+: synthesize(text) -> chunks with audio_int16_bytes."""

    def __init__(self, sentences, delay=0.0):
        self.sentences, self.delay = sentences, delay
        self.texts: list[str] = []
        self.threads: set[str] = set()
        self.produced = 0

    def synthesize(self, text):
        self.texts.append(text)
        self.threads.add(threading.current_thread().name)
        for s in self.sentences:
            if self.delay:
                time.sleep(self.delay)
            self.produced += 1
            yield SimpleNamespace(audio_int16_bytes=s)


class OldApiVoice:
    """piper-tts 1.2.x: synthesize_stream_raw(text) -> raw bytes per sentence."""

    def __init__(self, sentences):
        self.sentences = sentences

    def synthesize_stream_raw(self, text):
        yield from self.sentences

    def synthesize(self, text, wav_file):            # the 1.2 signature must NOT be used by us
        raise AssertionError("old API synthesize(text, wav) must not be called")


def engine(tmp_path, voice, **kw):
    loads = []

    def factory(onnx, cfg):
        loads.append((onnx, cfg))
        return voice

    t = PiperTTS(voice_files(tmp_path), voice_factory=factory, **kw)
    t.loads = loads
    return t


async def collect(t, text, cancel=None):
    out = []
    async for chunk in t.synthesize(text, cancel or CancelToken()):
        out.append(chunk)
    return out


def test_protocol_and_rate_from_config_without_loading(tmp_path):
    t = engine(tmp_path, NewApiVoice([]))
    assert isinstance(t, TTSEngine)
    assert t.sample_rate == RATE and t.voice == "ru_RU-irina-medium" and t.language == "ru_RU" and t.loads == []
    assert t.status()["ok"] and not t.status()["loaded"] and t.loads == []


def test_voice_validation_legit_and_bad(tmp_path, monkeypatch):
    onnx = voice_files(tmp_path)
    assert validate_voice(onnx)[0] == onnx.resolve()
    with pytest.raises(ValueError):
        validate_voice("voice.onnx")                                   # relative
    lonely = tmp_path / "x.onnx"
    lonely.write_bytes(b"1")
    with pytest.raises(ValueError):
        validate_voice(lonely)                                         # no .onnx.json
    monkeypatch.delenv("BOSSMAN_PIPER_VOICE_PATH", raising=False)
    with pytest.raises(ValueError):
        resolve_voice(None)
    monkeypatch.setenv("BOSSMAN_PIPER_VOICE_PATH", str(onnx))
    assert resolve_voice(None)[0] == onnx.resolve()


def test_non_russian_voice_is_a_warning_not_an_error(tmp_path):
    t = PiperTTS(voice_files(tmp_path, "en_US-amy-medium", lang="en_US"), voice_factory=lambda a, b: None)
    s = t.status()
    assert s["ok"] and s["warning"] and "en_US" in s["warning"]
    assert PiperTTS(voice_files(tmp_path, "ru_RU-x"), voice_factory=lambda a, b: None).status()["warning"] is None


async def test_new_api_yields_pcm_chunks_lazily_off_loop(tmp_path):
    v = NewApiVoice([sentence(250)])
    t = engine(tmp_path, v, chunk_ms=100)
    assert t.loads == []
    chunks = await collect(t, "Привет, мир.")
    assert len(t.loads) == 1 and v.texts == ["Привет, мир."]
    assert b"".join(chunks) == sentence(250)
    assert all(len(c) % 2 == 0 for c in chunks) and max(len(c) for c in chunks) <= RATE * 100 // 1000 * 2
    assert len(chunks) == 3                                           # 250 ms in <=100 ms chunks
    assert v.threads and threading.current_thread().name not in v.threads


async def test_old_api_is_detected_and_used(tmp_path):
    t = engine(tmp_path, OldApiVoice([sentence(100), sentence(100)]))
    assert len(b"".join(await collect(t, "Раз. Два."))) == len(sentence(200))


def test_iter_pcm_supports_both_generations():
    assert list(iter_pcm(NewApiVoice([b"\x01\x00", b"\x02\x00"]), "x")) == [b"\x01\x00", b"\x02\x00"]
    assert list(iter_pcm(OldApiVoice([b"\x03\x00"]), "x")) == [b"\x03\x00"]


async def test_second_sentence_arrives_while_first_is_being_consumed(tmp_path):
    v = NewApiVoice([sentence(100), sentence(100)], delay=0.15)
    t = engine(tmp_path, v, chunk_ms=100)
    t0 = time.monotonic()
    first = None
    async for _ in t.synthesize("Раз. Два.", CancelToken()):
        first = first or time.monotonic() - t0
    assert first < 0.3 and time.monotonic() - t0 >= 0.28               # first audio before synthesis of all is done


async def test_cancel_stops_yielding_and_frees_worker(tmp_path):
    v = NewApiVoice([sentence(400)] * 20, delay=0.02)
    t = engine(tmp_path, v, chunk_ms=100)
    cancel = CancelToken()
    got = 0
    async for _ in t.synthesize("Длинный текст.", cancel):
        got += 1
        if got == 2:
            cancel.cancel("barge_in")
    assert got == 2                                                    # nothing after the cancel
    await asyncio.sleep(0.2)
    assert v.produced < 20                                             # the worker stopped early too
    v2 = NewApiVoice([sentence(100)])
    t._voice = v2
    assert len(await collect(t, "Ещё.")) == 1                          # single worker is free again


async def test_precancelled_and_empty_text_yield_nothing_and_load_nothing(tmp_path):
    t = engine(tmp_path, NewApiVoice([sentence(100)]))
    c = CancelToken()
    c.cancel()
    assert await collect(t, "Привет.", c) == []
    assert await collect(t, "   ") == []
    assert t.loads == []


async def test_abandoned_generator_does_not_wedge_the_worker(tmp_path):
    v = NewApiVoice([sentence(200)] * 10, delay=0.01)
    t = engine(tmp_path, v)
    gen = t.synthesize("Текст.", CancelToken())
    await gen.__anext__()
    await gen.aclose()
    await asyncio.sleep(0.2)
    assert v.produced < 10
    assert len(await collect(t, "Снова.")) >= 1


async def test_missing_voice_or_package_is_tts_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_PIPER_VOICE_PATH", raising=False)
    t = PiperTTS(None)
    assert t.status()["ok"] is False and t.voice == "unconfigured"
    with pytest.raises(CallError) as e:
        await collect(t, "Привет.")
    assert e.value.code == "TTS_UNAVAILABLE"

    def nopkg(a, b):
        raise ImportError("piper")

    t2 = PiperTTS(voice_files(tmp_path), voice_factory=nopkg)
    with pytest.raises(CallError) as e:
        await collect(t2, "Привет.")
    assert e.value.code == "TTS_UNAVAILABLE" and e.value.detail == "package_missing"
    assert t2.status()["ok"] is False


async def test_engine_errors_do_not_leak_details(tmp_path):
    class Boom:
        def synthesize(self, text):
            raise RuntimeError("C:\\secret\\voice.onnx broke")
            yield  # pragma: no cover

    t = engine(tmp_path, Boom())
    with pytest.raises(CallError) as e:
        await collect(t, "Привет.")
    assert e.value.code == "TTS_UNAVAILABLE" and e.value.detail == "RuntimeError" and "secret" not in repr(e.value.as_dict())


def test_default_factory_uses_piper_voice_load(tmp_path, monkeypatch):
    seen = {}

    class PV:
        @staticmethod
        def load(model, config_path=None, use_cuda=False):
            seen.update(model=model, config=config_path, cuda=use_cuda)
            return NewApiVoice([])

    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=PV))
    t = PiperTTS(voice_files(tmp_path))
    t._load_sync()
    assert seen["model"].endswith(".onnx") and seen["config"].endswith(".onnx.json") and seen["cuda"] is False
