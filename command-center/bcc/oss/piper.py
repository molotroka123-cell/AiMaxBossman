"""Bounded, local Russian speech synthesis for existing Bossman surfaces.

Piper and a voice model are optional host assets. No model is downloaded or
started until a caller explicitly requests synthesis. No shell is involved.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Callable
import wave


MAX_TEXT_CHARS = 1200
MAX_TEXT_BYTES = 3072
MAX_WAV_BYTES = 12 * 1024 * 1024
MAX_OGG_BYTES = 4 * 1024 * 1024
MAX_AUDIO_SECONDS = 120


class PiperError(ValueError):
    """Stable failure code; never includes participant text or host paths."""


def _local_file(path: str | Path, suffix: str | None = None) -> Path:
    value = Path(path)
    if not value.is_absolute() or not value.is_file() or (suffix and value.suffix.lower() != suffix):
        raise PiperError("VOICE_ENGINE_UNAVAILABLE")
    return value.resolve()


def _run(argv: list[str], *, stdin: bytes | None, stopped: Callable[[], bool], timeout: float) -> None:
    """Run a local binary with a deadline and a polled owner STOP."""
    if stopped():
        raise PiperError("VOICE_STOPPED")
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, ValueError) as exc:
        raise PiperError("VOICE_ENGINE_UNAVAILABLE") from exc
    try:
        if stdin is not None:
            assert process.stdin is not None
            process.stdin.write(stdin)
            process.stdin.close()
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if stopped():
                raise PiperError("VOICE_STOPPED")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PiperError("VOICE_TIMEOUT")
            try:
                process.wait(timeout=min(0.2, remaining))
            except subprocess.TimeoutExpired:
                continue
        if stopped():
            raise PiperError("VOICE_STOPPED")
        if process.returncode != 0:
            raise PiperError("VOICE_ENGINE_FAILED")
    except (OSError, BrokenPipeError) as exc:
        raise PiperError("VOICE_ENGINE_FAILED") from exc
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def _validate_wav(path: Path) -> None:
    if not path.is_file() or not 44 < path.stat().st_size <= MAX_WAV_BYTES:
        raise PiperError("VOICE_AUDIO_INVALID")
    try:
        with wave.open(str(path), "rb") as audio:
            channels, width, rate, frames, compression, _ = audio.getparams()
            duration = frames / rate if rate else 0
            if (channels != 1 or width != 2 or compression != "NONE" or
                    not 8000 <= rate <= 48000 or not 0 < duration <= MAX_AUDIO_SECONDS):
                raise PiperError("VOICE_AUDIO_INVALID")
            if len(audio.readframes(frames)) != frames * channels * width:
                raise PiperError("VOICE_AUDIO_INVALID")
    except (OSError, EOFError, wave.Error, ZeroDivisionError) as exc:
        raise PiperError("VOICE_AUDIO_INVALID") from exc


def synthesize_ogg(
    text: str, *, piper_executable: str | Path, model_path: str | Path,
    ffmpeg_executable: str | Path, stopped: Callable[[], bool] = lambda: False,
) -> bytes:
    """Return verified OGG/Opus bytes for a short UTF-8 utterance.

    The caller must apply its egress guard *before* invoking this function when
    audio will be sent outside the host. Configured paths must be local files.
    """
    if (not isinstance(text, str) or not 0 < len(text.strip()) <= MAX_TEXT_CHARS or
            "\x00" in text or len(text.encode("utf-8")) > MAX_TEXT_BYTES):
        raise PiperError("VOICE_TEXT_INVALID")
    piper = _local_file(piper_executable)
    model = _local_file(model_path, ".onnx")
    config = _local_file(str(model) + ".json", ".json")
    try:
        details = json.loads(config.read_text(encoding="utf-8"))
        language = details.get("language", {})
        if not isinstance(language, dict) or language.get("code") != "ru_RU":
            raise PiperError("VOICE_LANGUAGE_UNSUPPORTED")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
        raise PiperError("VOICE_MODEL_INVALID") from exc
    ffmpeg = _local_file(ffmpeg_executable)
    if stopped():
        raise PiperError("VOICE_STOPPED")
    # None of the input/audio is written to the repository or long-lived data.
    with tempfile.TemporaryDirectory(prefix="bossman-voice-") as directory:
        wav = Path(directory) / "reply.wav"
        ogg = Path(directory) / "reply.ogg"
        _run([str(piper), "--model", str(model), "--config", str(config),
              "--output_file", str(wav)], stdin=text.encode("utf-8") + b"\n",
             stopped=stopped, timeout=60)
        _validate_wav(wav)
        _run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
              "-i", str(wav), "-ac", "1", "-c:a", "libopus", "-b:a", "24k",
              "-f", "ogg", str(ogg)], stdin=None, stopped=stopped, timeout=30)
        if stopped():
            raise PiperError("VOICE_STOPPED")
        if not ogg.is_file() or not 32 < ogg.stat().st_size <= MAX_OGG_BYTES:
            raise PiperError("VOICE_AUDIO_INVALID")
        output = ogg.read_bytes()
        if not output.startswith(b"OggS"):
            raise PiperError("VOICE_AUDIO_INVALID")
        return output
