"""Optional voice clone and Telegram video receipt contracts; no model download."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import wave

import httpx
import pytest

from bcc.oss import chatterbox_clone as clone
from bcc.oss.piper import PiperError
from bcc.oss.voice_profile import VoiceProfile
from bcc.telegram_companion.adapters import Telegram
from bcc.telegram_companion.config import CompanionError, Person


def _telegram(handler):
    person = Person(user_id=101, chat_id=101, role="owner")
    settings = SimpleNamespace(bot_token="test-bot-token", core_token="test-core-token",
                               cloud_token="", local_token="", proxy="", people=(person,))
    return Telegram(settings, transport=httpx.MockTransport(handler)), person


def _assets(tmp_path: Path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"stub")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"stub")
    model = tmp_path / "model"
    model.mkdir()
    for name in clone.MODEL_FILES:
        (model / name).write_bytes(b"stub")
    reference = tmp_path / "private-reference.wav"
    with wave.open(str(reference), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 24000 * 6)
    return python, ffmpeg, model, reference


def test_clone_uses_local_worker_and_bounded_private_input(tmp_path, monkeypatch):
    python, ffmpeg, model, reference = _assets(tmp_path)
    calls = []

    def run(argv, *, stdin, stopped, timeout):
        calls.append((argv, stdin, timeout))
        if argv[0] == str(python):
            with wave.open(argv[4], "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(b"\x00\x00" * 24000)
        else:
            Path(argv[-1]).write_bytes(b"OggS" + b"x" * 100)

    monkeypatch.setattr(clone, "_run", run)
    result = clone.synthesize_ogg("Привет", python_executable=python,
        model_dir=model, reference_path=reference, ffmpeg_executable=ffmpeg)
    assert result.startswith(b"OggS")
    assert calls[0][1] == "Привет".encode()
    assert "Привет" not in str(calls[0][0])
    assert calls[0][2] == 180
    assert calls[0][0][-2:] == ["0.5", "0.5"]


def test_private_voice_profile_scales_change_request_and_bounded_tts_args(tmp_path, monkeypatch):
    python, ffmpeg, model, reference = _assets(tmp_path)
    calls = []

    def run(argv, *, stdin, stopped, timeout):
        calls.append(argv)
        if argv[0] == str(python):
            with wave.open(argv[4], "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(b"\x00\x00" * 24000)
        else:
            Path(argv[-1]).write_bytes(b"OggS" + b"x" * 100)

    monkeypatch.setattr(clone, "_run", run)
    neutral = VoiceProfile("Acid")
    energetic = VoiceProfile("Acid", mood=9)
    forceful = VoiceProfile("Acid", aggression=9)
    assert neutral.text_style_instruction() != energetic.text_style_instruction()
    assert neutral.text_style_instruction() != forceful.text_style_instruction()
    assert energetic.chatterbox_parameters() != neutral.chatterbox_parameters()
    assert forceful.chatterbox_parameters() != neutral.chatterbox_parameters()
    assert VoiceProfile("Voice Ember").chatterbox_parameters() == (0.5, 0.5)
    for profile in (neutral, energetic, forceful):
        clone.synthesize_ogg("Привет", python_executable=python,
            model_dir=model, reference_path=reference, ffmpeg_executable=ffmpeg,
            profile=profile)
    assert [call[-2:] for call in calls[::2]] == [
        [str(a), str(b)] for a, b in (
            neutral.chatterbox_parameters(), energetic.chatterbox_parameters(),
            forceful.chatterbox_parameters())]
    for scale in (0, 11, 5.0, True):
        with pytest.raises(ValueError, match="VOICE_PROFILE_SCALE_INVALID"):
            VoiceProfile("Acid", mood=scale)


def test_clone_rejects_invalid_reference_or_stop_before_worker(tmp_path, monkeypatch):
    python, ffmpeg, model, reference = _assets(tmp_path)
    monkeypatch.setattr(clone, "_run", lambda *_args, **_kwargs: pytest.fail("worker started"))
    with pytest.raises(PiperError, match="VOICE_STOPPED"):
        clone.synthesize_ogg("Привет", python_executable=python,
            model_dir=model, reference_path=reference, ffmpeg_executable=ffmpeg,
            stopped=lambda: True)
    reference.write_bytes(b"bad")
    with pytest.raises(PiperError, match="VOICE_REFERENCE_INVALID"):
        clone.synthesize_ogg("Привет", python_executable=python,
            model_dir=model, reference_path=reference, ffmpeg_executable=ffmpeg)


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_video_delivery_requires_video_file_receipt(valid):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        assert request.url.path.endswith("/sendVideo")
        result = {"message_id": 44, "video": {"file_id": "saved-video"}} if valid else {"message_id": 44}
        return httpx.Response(200, json={"ok": True, "result": result})

    telegram, person = _telegram(handler)
    try:
        data = b"\x00\x00\x00\x18ftypisom" + b"x" * 40
        if valid:
            assert await telegram.send_video(person, data, "") == 44
        else:
            with pytest.raises(CompanionError, match="TELEGRAM_DELIVERY_UNVERIFIED"):
                await telegram.send_video(person, data, "")
        assert attempts == 1
    finally:
        await telegram.close()


@pytest.mark.asyncio
async def test_video_timeout_has_no_automatic_retry():
    attempts = 0

    def handler(_request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("unknown delivery outcome")

    telegram, person = _telegram(handler)
    try:
        data = b"\x00\x00\x00\x18ftypisom" + b"x" * 40
        with pytest.raises(CompanionError, match="NETWORK_UNAVAILABLE"):
            await telegram.send_video(person, data, "")
        assert attempts == 1
    finally:
        await telegram.close()
