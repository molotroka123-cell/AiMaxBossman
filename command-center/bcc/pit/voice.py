"""Bounded Telegram voice-note transcription for Jeff.

Telegram voice notes are OGG/Opus; the existing Bossman faster-whisper adapter
accepts only validated PCM16 WAV.  PyAV is already a faster-whisper dependency,
so this module only bridges those formats.  Audio stays in memory and is never
sent to a model provider or written to the repository.
"""
from __future__ import annotations

import asyncio
import io
import time
import wave
from typing import Callable

from bcc.oss import whisper

MAX_VOICE_BYTES = 20 * 1024 * 1024  # Telegram Bot API getFile limit
MAX_VOICE_SECONDS = 600
SAMPLE_RATE = 16_000
MAX_SAMPLES = MAX_VOICE_SECONDS * SAMPLE_RATE
MAX_DECODED_FRAMES = 60_000


class VoiceError(ValueError):
    """A stable error code; never includes private audio or local paths."""


def _metadata(voice: dict) -> tuple[str, int]:
    if not isinstance(voice, dict):
        raise VoiceError("VOICE_INVALID")
    file_id = voice.get("file_id")
    duration = voice.get("duration")
    size = voice.get("file_size")
    mime = voice.get("mime_type")
    if not isinstance(file_id, str) or not 0 < len(file_id) <= 256:
        raise VoiceError("VOICE_INVALID")
    # Telegram reports whole seconds; a very short valid note can report 0.
    if type(duration) is not int or not 0 <= duration <= MAX_VOICE_SECONDS:
        raise VoiceError("VOICE_DURATION_INVALID")
    if size is not None and (type(size) is not int or not 0 < size <= MAX_VOICE_BYTES):
        raise VoiceError("VOICE_TOO_LARGE")
    if mime is not None and mime not in {"audio/ogg", "audio/opus", "application/ogg"}:
        raise VoiceError("VOICE_FORMAT_UNSUPPORTED")
    return file_id, duration


def _decode_ogg_opus(audio: bytes) -> bytes:
    """Decode a single OGG/Opus stream to bounded in-memory mono PCM16 WAV."""
    if not isinstance(audio, bytes) or not 4 <= len(audio) <= MAX_VOICE_BYTES:
        raise VoiceError("VOICE_TOO_LARGE")
    if not audio.startswith(b"OggS"):
        raise VoiceError("VOICE_FORMAT_UNSUPPORTED")
    try:
        import av  # bundled by the optional faster-whisper speech extra
    except (ImportError, OSError) as exc:
        raise VoiceError("VOICE_DECODER_UNAVAILABLE") from exc

    pcm = io.BytesIO()
    samples = 0
    frames = 0
    try:
        with av.open(io.BytesIO(audio), mode="r", format="ogg") as container:
            streams = list(container.streams.audio)
            if len(streams) != 1 or streams[0].codec_context.name != "opus":
                raise VoiceError("VOICE_FORMAT_UNSUPPORTED")
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=SAMPLE_RATE)

            def append(frame) -> None:
                nonlocal samples
                data = frame.to_ndarray().tobytes()
                if not data or len(data) % 2:
                    raise VoiceError("VOICE_DECODE_FAILED")
                samples += len(data) // 2
                if samples > MAX_SAMPLES:
                    raise VoiceError("VOICE_DURATION_INVALID")
                pcm.write(data)

            for frame in container.decode(audio=0):
                frames += 1
                if frames > MAX_DECODED_FRAMES:
                    raise VoiceError("VOICE_DURATION_INVALID")
                for resampled in resampler.resample(frame):
                    append(resampled)
            for resampled in resampler.resample(None):
                append(resampled)
    except VoiceError:
        raise
    except Exception as exc:
        # PyAV errors can contain the source path or user-controlled metadata.
        raise VoiceError("VOICE_DECODE_FAILED") from exc
    if samples == 0:
        raise VoiceError("VOICE_NO_SPEECH")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.getvalue())
    return output.getvalue()


def _transcribe(audio: bytes) -> dict:
    from .speech import record_latency
    clean_wav = _decode_ogg_opus(audio)
    started = time.perf_counter()
    try:
        result = whisper.transcribe_audio(clean_wav, language="auto")
    except whisper.WhisperError as exc:
        record_latency("stt", started, ok=False)
        raise VoiceError("VOICE_STT_UNAVAILABLE") from exc
    record_latency("stt", started)
    if not str(result.get("text", "")).strip():
        raise VoiceError("VOICE_NO_SPEECH")
    return result


async def transcribe_telegram_voice(
    telegram,
    voice: dict,
    *,
    stopped: Callable[[], bool] = lambda: False,
) -> dict:
    """Fetch one bounded note, run local STT off the event loop, return a transcript.

    The caller must bind ``telegram`` to the already authenticated PIT bot and
    the intended participant.  A STOP prevents new work and discards a result
    completed after STOP; it does not pretend to cancel native decoder work.
    """
    file_id, _ = _metadata(voice)
    if stopped():
        raise VoiceError("VOICE_STOPPED")
    try:
        audio = await telegram.fetch_file(file_id, MAX_VOICE_BYTES)
    except Exception as exc:
        # Telegram transport may include a token-bearing URL in a low-level
        # exception.  Only the stable code is suitable for a participant reply.
        raise VoiceError("VOICE_FETCH_FAILED") from exc
    if stopped():
        raise VoiceError("VOICE_STOPPED")
    try:
        result = await asyncio.to_thread(_transcribe, audio)
    except VoiceError:
        raise
    except Exception as exc:
        raise VoiceError("VOICE_STT_UNAVAILABLE") from exc
    if stopped():
        raise VoiceError("VOICE_STOPPED")
    return result
