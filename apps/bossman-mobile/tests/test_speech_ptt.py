"""Push-to-talk through the phone gateway (audit 28.10, part III 3.1).

A recording made by the browser (WebM/Opus here, synthesized by ffmpeg: a tone, no human voice) goes to the pult, is
converted to 16 kHz mono WAV and forwarded to the (fake) local Bossman, whose Whisper answer comes back as text.
Synthetic only; the real Whisper is exercised by the live proof, not here.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import wave

import pytest

from bossmobile import speech

needs_ffmpeg = pytest.mark.skipif(speech.find_ffmpeg() is None and not shutil.which("ffmpeg"), reason="ffmpeg required")
URL = "/m/api/speech/transcribe"


def _webm(seconds: int = 2) -> bytes:
    ff = speech.find_ffmpeg()
    return subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "libopus",
                           "-f", "webm", "pipe:1"], capture_output=True, check=True).stdout


def _post(env, data: bytes, ctype: str = "audio/webm", query: str = "", csrf: bool = True):
    headers = {"Content-Type": ctype}
    if csrf:
        headers["X-BM-CSRF"] = env.csrf
    return env.c.post(URL + query, content=data, headers=headers)


def _speech_calls(env):
    return [c for c in env.fake.calls if c["path"] == "/api/oss/speech/transcribe"]


def test_needs_a_session_and_csrf_and_nothing_reaches_bossman(env):
    assert env.c.post(URL, content=b"x" * 500, headers={"Content-Type": "audio/webm"}).status_code in (401, 403)
    env.login()
    assert _post(env, b"x" * 500, csrf=False).status_code == 403
    assert not _speech_calls(env)


@needs_ffmpeg
def test_recording_is_converted_to_a_valid_16k_mono_wav_and_the_text_comes_back(authed):
    r = _post(authed, _webm(2), query="?language=ru")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "text": "привет, Босман", "language": "ru", "duration_s": 3.0}
    (call,) = _speech_calls(authed)
    assert call["query"] == {"language": "ru"} and call["headers"]["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(authed.fake.speech_bytes)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert 30000 <= w.getnframes() <= 34000                      # about 2 s of audio, real sizes in the header


@needs_ffmpeg
def test_garbage_and_too_short_audio_are_refused_before_bossman(authed):
    r = _post(authed, b"this is not audio at all" * 40)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "BM_SPEECH_FORMAT"
    r = _post(authed, b"tiny")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "BM_SPEECH_EMPTY"
    assert not _speech_calls(authed)


def test_oversize_is_refused_by_length_and_while_streaming(authed, monkeypatch):
    monkeypatch.setattr(speech, "MAX_BYTES", 1000)
    r = _post(authed, b"x" * 5000)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "BM_SPEECH_TOO_LARGE"
    assert not _speech_calls(authed)


def test_bad_language_is_refused(authed):
    r = _post(authed, b"x" * 500, query="?language=../../etc")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "BM_SPEECH_LANG"
    assert not _speech_calls(authed)


def test_missing_ffmpeg_is_an_honest_503(authed, monkeypatch):
    monkeypatch.setattr(speech, "find_ffmpeg", lambda: None)
    r = _post(authed, b"x" * 500)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "BM_SPEECH_NO_FFMPEG"


def test_wav_sizes_are_repaired_even_with_a_chunk_before_data():
    pcm = b"\x01\x00" * 2000
    fmt = b"fmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") \
        + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
    junk = b"LIST" + (4).to_bytes(4, "little") + b"INFO"
    streamed = b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + fmt + junk + b"data" + b"\xff\xff\xff\xff" + pcm
    fixed = speech.fix_wav_sizes(streamed)
    with wave.open(io.BytesIO(fixed)) as w:
        assert w.getnframes() == 2000 and w.getframerate() == 16000
    with pytest.raises(speech.SpeechInputError):
        speech.fix_wav_sizes(b"RIFF" + b"\x00" * 8 + b"WAVE")


def test_ui_has_the_hold_to_talk_button_wired_to_the_endpoint():
    from pathlib import Path
    ui = Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
    html, js, css = (ui / "index.html").read_text(encoding="utf-8"), (ui / "app.js").read_text(encoding="utf-8"), (ui / "app.css").read_text(encoding="utf-8")
    assert 'id="btn-mic"' in html and 'id="i-mic"' in html and 'aria-label="Голос: удерживайте и говорите"' in html
    assert "/m/api/speech/transcribe" in js and "MediaRecorder" in js and "'X-BM-CSRF': S.csrf" in js
    assert "pointerdown" in js and "pointerup" in js and "isSecureContext" in js          # hold to talk, honest https hint
    assert ".compose-tool.rec" in css
    import re
    ver = int(re.search(r"bossman-shell-v(\d+)", (ui / "sw.js").read_text(encoding="utf-8")).group(1))
    assert ver >= 26                                                                     # phones fetch a shell that has the mic (v26 or later)
