"""The REAL `bcc.pit.speech.transcribe_wav` path (only the Whisper model is a fake): `beam_size` must reach `model.transcribe`.

Regression (found after the calls merge): `transcribe_wav(..., beam_size=N)` called `_transcribe_wav(...)` without passing it while the
callee used the name -> NameError -> every real STT turn ended as VOICE_STT_FAILED. The call tests with an injected decoder could not
see it because they never enter this function.
"""
from __future__ import annotations

import io
import types
import wave

import pytest

from bcc.oss import whisper
from bcc.pit import speech
from bcc.telegram_calls.speech import jeff_engines as je


def wav_bytes(seconds: float = 0.6, rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return out.getvalue()


class FakeModel:
    def __init__(self):
        self.calls: list[dict] = []

    def transcribe(self, audio, **kw):
        self.calls.append(dict(kw))
        seg = types.SimpleNamespace(text=" привет ", start=0.0, end=0.5, no_speech_prob=0.01, avg_logprob=-0.2)
        return iter([seg]), types.SimpleNamespace(language="ru")


@pytest.fixture()
def model(monkeypatch, tmp_path):
    fake = FakeModel()
    monkeypatch.setattr(whisper, "_model_directory", lambda: tmp_path)
    monkeypatch.setattr(speech, "_recogniser", lambda path: fake)
    return fake


@pytest.mark.parametrize("beam", [1, 3, 8])
def test_beam_size_reaches_the_model(model, beam):
    out = speech.transcribe_wav(wav_bytes(), beam_size=beam)
    assert out["text"] == "привет"
    assert model.calls[-1]["beam_size"] == beam and model.calls[-1]["language"] == "ru"


def test_the_default_beam_is_five_and_a_bad_path_is_not_a_nameerror(model):
    assert speech.transcribe_wav(wav_bytes())["text"] == "привет"
    assert model.calls[-1]["beam_size"] == 5


async def test_the_call_stt_adapter_drives_the_real_path_with_its_cheap_beam(model):
    stt = je.JeffSTT(transcribe=speech.transcribe_wav, stopped=lambda: False)
    stream = stt.new_stream()
    stream.feed(b"\x01\x00" * 16000)
    res = await stream.finalize()
    assert res.text == "привет"
    assert model.calls[-1]["beam_size"] == je.STT_BEAM == 1
