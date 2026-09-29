"""FasterWhisperSTT with an injected fake model (no faster-whisper, no weights, no network)."""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from bcc.telegram_calls.audio.pcm import to_pcm
from bcc.telegram_calls.speech.stt import FasterWhisperSTT, resolve_model_dir, validate_model_dir
from bcc.telegram_calls.types import CallError, STTEngine, STTStream


def model_dir(tmp_path, name="whisper-small-ct2", files=("model.bin", "config.json", "tokenizer.json")):
    d = tmp_path / name
    d.mkdir()
    for f in files:
        (d / f).write_bytes(b"x")
    return d


class FakeModel:
    def __init__(self, text="привет мир", delay=0.0, boom=False):
        self.text, self.delay, self.boom = text, delay, boom
        self.calls: list[dict] = []
        self.threads: set[str] = set()

    def transcribe(self, audio, **kw):
        self.calls.append({"n": len(audio), "dtype": audio.dtype, **kw})
        self.threads.add(threading.current_thread().name)
        if self.boom:
            raise RuntimeError("C:\\secret\\path\\model.bin exploded")
        if self.delay:
            time.sleep(self.delay)
        seg = SimpleNamespace(text=" " + self.text + " ", avg_logprob=-0.2)
        return iter([seg]), SimpleNamespace(language="ru")


def engine(tmp_path, model=None, **kw):
    d = model_dir(tmp_path)
    m = model or FakeModel()
    loads = []

    def factory(path, compute, threads):
        loads.append((path, compute, threads))
        return m

    e = FasterWhisperSTT(d, model_factory=factory, **kw)
    e.loads, e.fake = loads, m
    return e


def pcm(seconds, amp=0.3):
    n = int(16000 * seconds)
    return to_pcm(amp * np.sin(2 * np.pi * 200 * np.arange(n) / 16000))


def test_protocol_and_model_name(tmp_path):
    e = engine(tmp_path)
    assert isinstance(e, STTEngine) and isinstance(e.new_stream(), STTStream)
    assert e.model == "whisper-small-ct2" and e.name == "faster-whisper"


def test_model_dir_convention_legit_and_bad(tmp_path, monkeypatch):
    d = model_dir(tmp_path)
    assert validate_model_dir(d) == d.resolve()
    with pytest.raises(ValueError):
        validate_model_dir(model_dir(tmp_path, "inc", files=("model.bin", "config.json")))   # tokenizer.json missing
    with pytest.raises(ValueError):
        validate_model_dir("relative/dir")
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", str(d))
    assert resolve_model_dir(None) == d.resolve()
    monkeypatch.delenv("BOSSMAN_WHISPER_MODEL_PATH")
    with pytest.raises(ValueError):
        resolve_model_dir(None)


def test_status_never_loads_and_reports_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_WHISPER_MODEL_PATH", raising=False)
    e = engine(tmp_path)
    s = e.status()
    assert s["ok"] and not s["loaded"] and e.loads == [] and s["cloud_used"] is False and s["download_on_request"] is False
    s = FasterWhisperSTT(None).status()
    assert s["ok"] is False and s["model_configured"] is False and s["reason"]


async def test_lazy_load_once_and_decode_flags(tmp_path):
    e = engine(tmp_path)
    assert e.loads == []
    st = e.new_stream()
    st.feed(pcm(1.0))
    r = await st.finalize()
    assert r.text == "привет мир" and r.language == "ru" and 0.8 < r.confidence < 0.9
    assert r.duration_s == pytest.approx(1.0)
    st2 = e.new_stream()
    st2.feed(pcm(0.5))
    await st2.finalize()
    assert len(e.loads) == 1                                   # loaded once, shared
    kw = e.fake.calls[0]
    assert kw["language"] == "ru" and kw["vad_filter"] is False and kw["condition_on_previous_text"] is False
    assert kw["dtype"] == np.float32


def test_default_factory_uses_local_files_only(tmp_path, monkeypatch):
    seen = {}

    class WM:
        def __init__(self, path, **kw):
            seen.update(kw, path=path)

        def transcribe(self, *a, **k):
            return iter([]), SimpleNamespace(language="ru")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=WM))
    e = FasterWhisperSTT(model_dir(tmp_path))
    e._load_sync()
    assert seen["local_files_only"] is True and seen["device"] == "cpu" and seen["compute_type"] == "int8"


async def test_decode_runs_off_the_event_loop_thread(tmp_path):
    e = engine(tmp_path)
    st = e.new_stream()
    st.feed(pcm(1.0))
    await st.finalize()
    assert e.fake.threads and threading.current_thread().name not in e.fake.threads
    assert all(n.startswith("stt") for n in e.fake.threads)


async def test_event_loop_stays_responsive_during_decode(tmp_path):
    e = engine(tmp_path, model=FakeModel(delay=0.4))
    st = e.new_stream()
    st.feed(pcm(1.0))
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.ensure_future(ticker())
    await st.finalize()
    t.cancel()
    assert ticks >= 10                                          # a blocked loop would have ticked ~0 times


async def test_missing_model_is_stt_unavailable_on_use_not_on_construction(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_WHISPER_MODEL_PATH", raising=False)
    e = FasterWhisperSTT(None)                                  # construction is fine
    st = e.new_stream()
    st.feed(pcm(1.0))
    with pytest.raises(CallError) as ex:
        await st.finalize()
    assert ex.value.code == "STT_UNAVAILABLE"
    with pytest.raises(CallError):
        await e.ensure_ready()


async def test_missing_package_is_stt_unavailable(tmp_path):
    def nopkg(*a):
        raise ImportError("No module named faster_whisper")

    e = FasterWhisperSTT(model_dir(tmp_path), model_factory=nopkg)
    with pytest.raises(CallError) as ex:
        await e.ensure_ready()
    assert ex.value.code == "STT_UNAVAILABLE" and ex.value.detail == "package_missing"
    assert e.status()["ok"] is False


async def test_native_decode_error_is_stt_unavailable_without_leaking_paths(tmp_path):
    e = FasterWhisperSTT(model_dir(tmp_path), model_factory=lambda *a: FakeModel(boom=True))
    st = e.new_stream()
    st.feed(pcm(1.0))
    with pytest.raises(CallError) as ex:
        await st.finalize()
    assert ex.value.code == "STT_UNAVAILABLE" and ex.value.detail == "RuntimeError"
    assert "secret" not in repr(ex.value.as_dict()) and "secret" not in str(ex.value)


async def test_short_or_empty_audio_returns_empty_without_loading(tmp_path):
    e = engine(tmp_path)
    st = e.new_stream()
    assert (await st.finalize()).text == ""
    st.feed(pcm(0.05))
    assert (await st.finalize()).text == "" and e.loads == []
    st.feed(pcm(1.0))                                           # legit control: enough audio decodes
    assert (await st.finalize()).text == "привет мир"


async def test_partial_throttled_and_needs_new_audio(tmp_path):
    e = engine(tmp_path, partial_interval_s=0.0)
    st = e.new_stream()
    assert await st.partial() == ""                            # nothing yet, no decode
    st.feed(pcm(0.6))
    assert await st.partial() == "привет мир"
    n = len(e.fake.calls)
    assert await st.partial() == "привет мир" and len(e.fake.calls) == n      # no new audio -> no re-decode
    st.feed(pcm(0.6))
    await st.partial()
    assert len(e.fake.calls) == n + 1
    assert e.fake.calls[0]["beam_size"] == 1                    # cheap greedy partial


async def test_partial_interval_is_respected(tmp_path):
    e = engine(tmp_path, partial_interval_s=60.0)
    st = e.new_stream()
    st.feed(pcm(0.6))
    await st.partial()
    st.feed(pcm(0.6))
    await st.partial()
    assert len(e.fake.calls) == 1


async def test_partial_failure_is_not_fatal(tmp_path):
    e = engine(tmp_path, model=FakeModel(boom=True), partial_interval_s=0.0)
    st = e.new_stream()
    st.feed(pcm(0.6))
    assert await st.partial() == ""


async def test_cancel_clears_and_final_is_empty(tmp_path):
    e = engine(tmp_path)
    st = e.new_stream()
    st.feed(pcm(1.0))
    st.cancel()
    st.feed(pcm(1.0))                                           # ignored after cancel
    assert await st.partial() == "" and (await st.finalize()).text == "" and e.fake.calls == []


async def test_buffer_is_bounded_to_last_30_seconds(tmp_path):
    e = engine(tmp_path)
    st = e.new_stream()
    for _ in range(40):
        st.feed(pcm(1.0))
    r = await st.finalize()
    assert r.duration_s == pytest.approx(30.0)
    assert e.fake.calls[0]["n"] == 30 * 16000
