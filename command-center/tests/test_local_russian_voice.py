"""Local Russian TTS and guarded Telegram voice delivery contracts."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import wave

import httpx
import pytest

from bcc.oss import piper
from bcc.pit.presentation import spoken_reply_text
from bcc.telegram_companion.adapters import Telegram
from bcc.telegram_companion.config import CompanionError, Person


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(b"\x00\x00" * 2205)


def _assets(tmp_path: Path, language: str = "ru_RU"):
    binary = tmp_path / "piper.exe"
    binary.write_bytes(b"fake executable")
    model = tmp_path / "ru_RU-denis-medium.onnx"
    model.write_bytes(b"fake model")
    (tmp_path / (model.name + ".json")).write_text(
        json.dumps({"language": {"code": language}}), encoding="utf-8")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fake executable")
    return binary, model, ffmpeg


def test_russian_text_is_utf8_and_local_output_is_bounded(tmp_path, monkeypatch):
    binary, model, ffmpeg = _assets(tmp_path)
    seen = []

    def run(argv, *, stdin, stopped, timeout):
        seen.append((argv, stdin, timeout))
        if argv[0] == str(binary):
            _wav(Path(argv[-1]))
        else:
            Path(argv[-1]).write_bytes(b"OggS" + b"x" * 60)

    monkeypatch.setattr(piper, "_run", run)
    audio = piper.synthesize_ogg("Привет, это Боссман.",
        piper_executable=binary, model_path=model, ffmpeg_executable=ffmpeg)
    assert audio.startswith(b"OggS")
    assert seen[0][1] == "Привет, это Боссман.\n".encode("utf-8")
    assert all("shell" not in argv for argv, _, _ in seen)


def test_non_russian_model_and_stop_do_not_start_synthesis(tmp_path, monkeypatch):
    binary, model, ffmpeg = _assets(tmp_path, language="en_US")
    monkeypatch.setattr(piper, "_run", lambda *args, **kwargs: pytest.fail("started"))
    with pytest.raises(piper.PiperError, match="VOICE_LANGUAGE_UNSUPPORTED"):
        piper.synthesize_ogg("Привет", piper_executable=binary,
                             model_path=model, ffmpeg_executable=ffmpeg)
    (tmp_path / (model.name + ".json")).write_text(
        json.dumps({"language": {"code": "ru_RU"}}), encoding="utf-8")
    with pytest.raises(piper.PiperError, match="VOICE_STOPPED"):
        piper.synthesize_ogg("Привет", piper_executable=binary,
                             model_path=model, ffmpeg_executable=ffmpeg, stopped=lambda: True)


def test_run_times_out_and_kills_child():
    with pytest.raises(piper.PiperError, match="VOICE_TIMEOUT"):
        piper._run([sys.executable, "-c", "import time; time.sleep(5)"],
                   stdin=None, stopped=lambda: False, timeout=0.1)


def test_spoken_text_does_not_speak_html_or_markdown_syntax():
    assert spoken_reply_text("<b>Привет &amp; пока</b>\n### **Детали**") == "Привет & пока\nДетали"


def _telegram(handler):
    person = Person(user_id=101, chat_id=101, role="owner")
    settings = SimpleNamespace(bot_token="test-bot-token", core_token="test-core-token",
                               cloud_token="", local_token="", proxy="", people=(person,))
    return Telegram(settings, transport=httpx.MockTransport(handler)), person


@pytest.mark.asyncio
async def test_send_voice_guards_before_tts_and_requires_voice_receipt(monkeypatch):
    events = []

    def handler(request):
        events.append("upload")
        assert request.url.path.endswith("/sendVoice")
        assert b"bossman.ogg" in request.content
        return httpx.Response(200, json={"ok": True, "result": {
            "message_id": 55, "voice": {"file_id": "returned-file-id"}}})

    telegram, person = _telegram(handler)
    async def synthesize(text):
        events.append("tts:" + text)
        return b"OggS" + b"x" * 60

    try:
        receipt = await telegram.send_voice(person, "Привет", synthesize,
                                            reply_to_message_id=4)
        assert receipt == 55
        assert events == ["tts:Привет", "upload"]
    finally:
        await telegram.close()


@pytest.mark.asyncio
async def test_send_voice_blocked_text_never_reaches_tts_or_upload(monkeypatch):
    from bossman.notifications import telegram_transport
    monkeypatch.setattr(telegram_transport, "_egress_guard_text", lambda _: "[held]")
    telegram, person = _telegram(lambda _: pytest.fail("uploaded"))
    async def synthesize(_):
        pytest.fail("TTS started")
    try:
        with pytest.raises(CompanionError, match="VOICE_TEXT_BLOCKED"):
            await telegram.send_voice(person, "private data", synthesize)
    finally:
        await telegram.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["missing_voice", "timeout"])
async def test_send_voice_uncertain_delivery_has_no_auto_retry(outcome):
    attempts = 0
    def handler(_request):
        nonlocal attempts
        attempts += 1
        if outcome == "timeout":
            raise httpx.ReadTimeout("unknown outcome")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 33}})
    telegram, person = _telegram(handler)
    async def synthesize(_):
        return b"OggS" + b"x" * 60
    try:
        with pytest.raises(CompanionError):
            await telegram.send_voice(person, "Привет", synthesize)
        assert attempts == 1
    finally:
        await telegram.close()
